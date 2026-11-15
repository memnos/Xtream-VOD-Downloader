"""Download Italian (forced preferred) subtitles next to episode .strm files."""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core import (
    STRM_OUTPUT_SERIES_PATH,
    map_media_server_path_to_local,
    xtream_playback_blocks_extra_streams,
)

# Public API key embedded in Jellyfin Open Subtitles plugin.
DEFAULT_OPENSUBTITLES_API_KEY = "gUCLWGoAg2PmyseoTM0INFFVPcDCeDlT"
DEFAULT_OPENSUBTITLES_UA = "xdownloader 1.0.0"
OPENSUBTITLES_BASE = "https://api.opensubtitles.com/api/v1"

LogFn = Callable[[str], None]
_SE_RE = re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})")
_TMDB_RE = re.compile(r"\[tmdbid-(\d+)\]", re.I)

_token_lock = threading.Lock()
_token_cache: dict[str, object] = {"token": "", "expires": 0.0, "key": ""}
_bg_lock = threading.Lock()
_bg_keys: set[str] = set()


def _log(log: LogFn | None, message: str) -> None:
    if log:
        log(message)


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    body: dict | None = None,
    timeout: int = 60,
    raw: bool = False,
) -> object:
    hdrs = {
        "Accept": "application/json",
        "User-Agent": DEFAULT_OPENSUBTITLES_UA,
        "Content-Type": "application/json",
    }
    if headers:
        hdrs.update(headers)
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = Request(url, data=data, headers=hdrs, method=method)
    with urlopen(req, timeout=timeout) as resp:
        payload = resp.read()
        if raw:
            return payload
        if not payload:
            return {}
        return json.loads(payload.decode("utf-8"))


def load_opensubtitles_credentials(config: dict | None = None) -> dict:
    """Resolve OpenSubtitles credentials from config, env, or Jellyfin plugin XML."""
    cfg = config or {}
    user = str(cfg.get("opensubtitles_username") or os.environ.get("OPENSUBTITLES_USERNAME") or "").strip()
    password = str(
        cfg.get("opensubtitles_password") or os.environ.get("OPENSUBTITLES_PASSWORD") or ""
    ).strip()
    api_key = str(
        cfg.get("opensubtitles_api_key")
        or os.environ.get("OPENSUBTITLES_API_KEY")
        or DEFAULT_OPENSUBTITLES_API_KEY
    ).strip()
    xml_path = str(
        cfg.get("opensubtitles_jf_config")
        or os.environ.get("OPENSUBTITLES_JF_CONFIG")
        or ""
    ).strip()
    if (not user or not password) and xml_path and os.path.isfile(xml_path):
        try:
            tree = ET.parse(xml_path)
            user = user or (tree.findtext("Username") or "").strip()
            password = password or (tree.findtext("Password") or "").strip()
        except ET.ParseError:
            pass
    default_xml = "/config/Jellyfin.Plugin.OpenSubtitles.xml"
    if (not user or not password) and os.path.isfile(default_xml):
        try:
            tree = ET.parse(default_xml)
            user = user or (tree.findtext("Username") or "").strip()
            password = password or (tree.findtext("Password") or "").strip()
        except ET.ParseError:
            pass
    host_xml = "/var/lib/jellyfin/config/data/plugins/configurations/Jellyfin.Plugin.OpenSubtitles.xml"
    if (not user or not password) and os.path.isfile(host_xml):
        try:
            tree = ET.parse(host_xml)
            user = user or (tree.findtext("Username") or "").strip()
            password = password or (tree.findtext("Password") or "").strip()
        except ET.ParseError:
            pass
    return {"username": user, "password": password, "api_key": api_key}


def opensubtitles_login(creds: dict) -> str:
    user = creds.get("username") or ""
    password = creds.get("password") or ""
    api_key = creds.get("api_key") or DEFAULT_OPENSUBTITLES_API_KEY
    if not user or not password:
        raise RuntimeError("OpenSubtitles: username/password mancanti")
    cache_key = f"{user}|{api_key}"
    now = time.time()
    with _token_lock:
        if _token_cache.get("key") == cache_key and float(_token_cache.get("expires") or 0) > now:
            return str(_token_cache.get("token") or "")
    data = _http_json(
        "POST",
        f"{OPENSUBTITLES_BASE}/login",
        headers={"Api-Key": api_key, "User-Agent": DEFAULT_OPENSUBTITLES_UA},
        body={"username": user, "password": password},
    )
    if not isinstance(data, dict) or not data.get("token"):
        raise RuntimeError("OpenSubtitles: login fallito")
    token = str(data["token"])
    with _token_lock:
        _token_cache["token"] = token
        _token_cache["key"] = cache_key
        _token_cache["expires"] = now + 20 * 3600
    return token


def _decode_subtitle_bytes(payload: bytes) -> str:
    if payload[:2] == b"\x1f\x8b":
        payload = gzip.GzipFile(fileobj=io.BytesIO(payload)).read()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return payload.decode(enc)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def opensubtitles_search_params(
    *,
    tmdb_id: int,
    language: str,
    forced: bool | None,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, object]:
    """OpenSubtitles search query. Episodes use parent_tmdb_id; movies use tmdb_id."""
    params: dict[str, object] = {
        "languages": language,
        "order_by": "download_count",
        "order_direction": "desc",
    }
    if season and episode:
        params["parent_tmdb_id"] = int(tmdb_id)
        params["season_number"] = int(season)
        params["episode_number"] = int(episode)
        params["type"] = "episode"
    else:
        params["tmdb_id"] = int(tmdb_id)
        params["type"] = "movie"
    if forced is True:
        params["foreign_parts_only"] = "only"
    elif forced is False:
        params["foreign_parts_only"] = "exclude"
    return params


def search_episode_subtitles(
    *,
    token: str,
    api_key: str,
    tmdb_id: int,
    season: int,
    episode: int,
    languages: str = "it",
    foreign_parts_only: bool | None = None,
) -> list[dict]:
    params = opensubtitles_search_params(
        tmdb_id=tmdb_id,
        language=languages,
        forced=foreign_parts_only,
        season=season,
        episode=episode,
    )
    url = f"{OPENSUBTITLES_BASE}/subtitles?{urlencode(params)}"
    data = _http_json(
        "GET",
        url,
        headers={
            "Api-Key": api_key,
            "Authorization": f"Bearer {token}",
            "User-Agent": DEFAULT_OPENSUBTITLES_UA,
        },
    )
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def search_movie_subtitles(
    *,
    token: str,
    api_key: str,
    tmdb_id: int,
    languages: str = "it",
    foreign_parts_only: bool | None = None,
) -> list[dict]:
    params = opensubtitles_search_params(
        tmdb_id=tmdb_id,
        language=languages,
        forced=foreign_parts_only,
    )
    url = f"{OPENSUBTITLES_BASE}/subtitles?{urlencode(params)}"
    data = _http_json(
        "GET",
        url,
        headers={
            "Api-Key": api_key,
            "Authorization": f"Bearer {token}",
            "User-Agent": DEFAULT_OPENSUBTITLES_UA,
        },
    )
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    return rows if isinstance(rows, list) else []


def download_subtitle_file(*, token: str, api_key: str, file_id: int) -> str:
    data = _http_json(
        "POST",
        f"{OPENSUBTITLES_BASE}/download",
        headers={
            "Api-Key": api_key,
            "Authorization": f"Bearer {token}",
            "User-Agent": DEFAULT_OPENSUBTITLES_UA,
        },
        body={"file_id": int(file_id)},
    )
    if not isinstance(data, dict) or not data.get("link"):
        raise RuntimeError("OpenSubtitles: link download assente")
    link = str(data["link"])
    req = Request(link, headers={"User-Agent": DEFAULT_OPENSUBTITLES_UA})
    with urlopen(req, timeout=90) as resp:
        raw = resp.read()
    return _decode_subtitle_bytes(raw)


def _pick_file_id(entry: dict) -> int | None:
    attrs = entry.get("attributes") if isinstance(entry, dict) else None
    if not isinstance(attrs, dict):
        return None
    files = attrs.get("files")
    if not isinstance(files, list) or not files:
        return None
    first = files[0] if isinstance(files[0], dict) else {}
    try:
        return int(first.get("file_id"))
    except (TypeError, ValueError):
        return None


def _is_forced_entry(entry: dict) -> bool:
    attrs = entry.get("attributes") if isinstance(entry, dict) else None
    if not isinstance(attrs, dict):
        return False
    if attrs.get("foreign_parts_only") is True:
        return True
    feature = str(attrs.get("feature_details") or "")
    name = str(attrs.get("release") or attrs.get("hearing_impaired") or "")
    blob = f"{feature} {name} {attrs.get('tags') or ''}".lower()
    return "forced" in blob or "foreign" in blob


def pick_best_italian_subtitle(entries: list[dict], *, prefer_forced: bool = True) -> tuple[dict | None, bool]:
    """Return (entry, is_forced). Prefer forced/foreign-parts, else full Italian."""
    if not entries:
        return None, False
    forced = [e for e in entries if _is_forced_entry(e)]
    if prefer_forced and forced:
        return forced[0], True
    # Prefer non-HI full subs
    normal = []
    for e in entries:
        attrs = e.get("attributes") if isinstance(e, dict) else {}
        if isinstance(attrs, dict) and attrs.get("hearing_impaired"):
            continue
        normal.append(e)
    pool = normal or entries
    return pool[0], False


def _download_forced_for_language(
    *,
    token: str,
    api_key: str,
    tmdb_id: int,
    language: str,
    season: int | None = None,
    episode: int | None = None,
) -> str | None:
    """Download a forced / foreign-parts-only subtitle, or None if missing."""
    is_episode = season is not None and episode is not None
    if is_episode:
        entries = search_episode_subtitles(
            token=token,
            api_key=api_key,
            tmdb_id=tmdb_id,
            season=int(season or 0),
            episode=int(episode or 0),
            languages=language,
            foreign_parts_only=True,
        )
    else:
        entries = search_movie_subtitles(
            token=token,
            api_key=api_key,
            tmdb_id=tmdb_id,
            languages=language,
            foreign_parts_only=True,
        )
    pick = choose_subtitle_entry(entries, forced=True, trust_query=True)
    file_id = _pick_file_id(pick) if pick else None
    if not file_id:
        return None
    return download_subtitle_file(token=token, api_key=api_key, file_id=file_id)


def _forced_fallback_message(label: str, err: str) -> str:
    """Human-readable reason when IT forced is missing and EN→IT did not succeed."""
    reasons = {
        "deepl_missing": (
            f"{label}: nessun forced IT; chiave DeepL Free assente "
            "(EN/FPO non tradotto)"
        ),
        "en_not_found": f"{label}: nessun forced IT né EN/FPO su OpenSubtitles",
        "en_download_failed": f"{label}: download forced EN/FPO fallito",
        "deepl_quota": f"{label}: quota DeepL Free esaurita (forced EN non tradotto)",
        "deepl_failed": f"{label}: traduzione DeepL del forced EN fallita",
        "deepl_empty": f"{label}: DeepL ha restituito un sottotitolo vuoto",
    }
    return reasons.get(err, f"{label}: fallback EN→IT non riuscito ({err})")


def translate_english_forced_to_italian(
    *,
    token: str,
    api_key: str,
    tmdb_id: int,
    config: dict | None = None,
    season: int | None = None,
    episode: int | None = None,
    log: LogFn | None = None,
    label: str = "",
) -> tuple[str | None, str]:
    """Search EN forced/FPO, translate with DeepL Free, return (srt_text, error)."""
    from deepl_translate import (
        DeepLError,
        DeepLQuotaExhausted,
        load_deepl_api_key,
        translate_srt,
    )

    tag = label or "Sottotitoli"
    deepl_key = load_deepl_api_key(config)
    if not deepl_key:
        return None, "deepl_missing"
    _log(log, f"{tag}: forced IT assente, cerco EN/FPO su OpenSubtitles")
    try:
        en_text = _download_forced_for_language(
            token=token,
            api_key=api_key,
            tmdb_id=tmdb_id,
            language="en",
            season=season,
            episode=episode,
        )
    except (HTTPError, URLError, RuntimeError, OSError, TimeoutError, ValueError) as exc:
        _log(log, f"{tag}: OpenSubtitles EN forced: {exc}")
        return None, "en_download_failed"
    if not en_text:
        return None, "en_not_found"
    _log(log, f"{tag}: trovato forced EN/FPO, traduco con DeepL Free")
    try:
        it_text = translate_srt(en_text, api_key=deepl_key, log=log)
    except DeepLQuotaExhausted as exc:
        _log(log, f"{tag}: {exc}")
        return None, "deepl_quota"
    except DeepLError as exc:
        _log(log, f"{tag}: DeepL: {exc}")
        return None, "deepl_failed"
    if not it_text.strip():
        return None, "deepl_empty"
    return it_text, ""


def tmdb_id_from_folder(series_folder: str) -> int | None:
    match = _TMDB_RE.search(series_folder or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def series_folder_from_path(path: str) -> str:
    p = (path or "").replace("\\", "/")
    season_dir = os.path.dirname(p)
    series_dir = os.path.dirname(season_dir)
    return os.path.basename(series_dir)


def resolve_strm_path(
    playing_path: str,
    *,
    server: str,
    config: dict,
) -> str | None:
    mapped = map_media_server_path_to_local(playing_path, server=server, config=config)
    if mapped and mapped.lower().endswith(".strm") and os.path.isfile(mapped):
        return mapped
    return None


_VIDEO_SUFFIXES = (
    ".strm",
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".ts",
    ".m2ts",
    ".wmv",
    ".mov",
    ".mpg",
    ".mpeg",
)


def media_path_base(path: str) -> str:
    """Path without the video or strm extension, so a sidecar sits beside the file."""
    lower = (path or "").lower()
    for ext in _VIDEO_SUFFIXES:
        if lower.endswith(ext):
            return path[: -len(ext)]
    root, _ext = os.path.splitext(path)
    return root


def sidecar_paths_for_strm(strm_path: str, *, forced: bool) -> list[str]:
    """Possible sidecar names Jellyfin accepts next to a strm or local video."""
    base = media_path_base(strm_path)
    if forced:
        return [
            f"{base}.ita.forced.srt",
            f"{base}.it.forced.srt",
            f"{base}.forced.ita.srt",
        ]
    return [
        f"{base}.ita.srt",
        f"{base}.it.srt",
        f"{base}.ita.full.srt",
    ]


def has_italian_sidecar(strm_path: str, *, accept_full: bool = True) -> tuple[bool, bool]:
    """Return (has_any_ita, has_forced)."""
    has_forced = False
    has_full = False
    for path in sidecar_paths_for_strm(strm_path, forced=True):
        if os.path.isfile(path) and os.path.getsize(path) > 20:
            has_forced = True
            break
    if accept_full:
        for path in sidecar_paths_for_strm(strm_path, forced=False):
            if os.path.isfile(path) and os.path.getsize(path) > 20:
                has_full = True
                break
    return (has_forced or has_full), has_forced


def language_tags(language: str) -> tuple[str, ...]:
    """Filename tags for a subtitle language. Italian keeps the existing ita/it names."""
    lang = (language or "it").strip().lower()
    if lang in {"it", "ita", "italian"}:
        return ("ita", "it")
    return (lang,)


def sidecar_paths_for_language(strm_path: str, *, language: str, forced: bool) -> list[str]:
    tags = language_tags(language)
    if tags[0] == "ita":
        return sidecar_paths_for_strm(strm_path, forced=forced)
    base = media_path_base(strm_path)
    tag = tags[0]
    if forced:
        return [f"{base}.{tag}.forced.srt"]
    return [f"{base}.{tag}.srt"]


def _existing_sidecar(strm_path: str, *, language: str, forced: bool) -> str:
    for path in sidecar_paths_for_language(strm_path, language=language, forced=forced):
        if os.path.isfile(path) and os.path.getsize(path) > 20:
            return path
    return ""


def has_language_sidecar(strm_path: str, *, language: str, forced: bool) -> bool:
    return bool(_existing_sidecar(strm_path, language=language, forced=forced))


def subtitle_saved_at(path: str) -> str:
    """Local time of the subtitle file, Europe/Rome."""
    if not path or not os.path.isfile(path):
        return ""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    stamp = datetime.fromtimestamp(os.path.getmtime(path), ZoneInfo("Europe/Rome"))
    return stamp.strftime("%d/%m/%Y %H:%M")


def subtitle_result_row(info: dict, *, label: str = "") -> dict:
    path = str(info.get("path") or "")
    if info.get("ok") and info.get("skipped"):
        outcome = "existing"
    elif info.get("ok"):
        outcome = "saved"
    else:
        outcome = "missing"
    return {
        "language": str(info.get("language") or ""),
        "forced": bool(info.get("forced")),
        "outcome": outcome,
        "name": os.path.basename(path) if path else "",
        "saved_at": subtitle_saved_at(path),
        "label": label,
    }


def write_language_sidecar(strm_path: str, text: str, *, language: str, forced: bool) -> str:
    if language_tags(language)[0] == "ita":
        return write_sidecar(strm_path, text, forced=forced)
    target = sidecar_paths_for_language(strm_path, language=language, forced=forced)[0]
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    body = text if text.endswith("\n") else text + "\n"
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    return target


def tmdb_id_for_strm(strm_path: str, *, episode: bool) -> int | None:
    """Movie id sits on the strm folder. Episode id sits on the series folder above the season."""
    folder = os.path.basename(os.path.dirname(strm_path))
    if episode:
        return tmdb_id_from_folder(series_folder_from_path(strm_path)) or tmdb_id_from_folder(folder)
    return tmdb_id_from_folder(folder) or tmdb_id_from_folder(os.path.basename(strm_path))


def choose_subtitle_entry(
    entries: list[dict], *, forced: bool, trust_query: bool = False
) -> dict | None:
    """Pick a forced or a full subtitle. A forced request never accepts a full file.

    trust_query is for a search that already asked OpenSubtitles for only forced
    or only full subtitles, where a hit may omit the foreign_parts_only flag.
    """
    if not entries:
        return None
    if forced:
        forced_entries = [entry for entry in entries if _is_forced_entry(entry)]
        if forced_entries:
            return forced_entries[0]
        return entries[0] if trust_query else None
    normal = []
    for entry in entries:
        attrs = entry.get("attributes") if isinstance(entry, dict) else {}
        if isinstance(attrs, dict) and attrs.get("hearing_impaired"):
            continue
        if _is_forced_entry(entry):
            continue
        normal.append(entry)
    return normal[0] if normal else None


def write_sidecar(strm_path: str, text: str, *, forced: bool) -> str:
    target = sidecar_paths_for_strm(strm_path, forced=forced)[0]
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    body = text if text.endswith("\n") else text + "\n"
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body)
    return target


def jellyfin_search_remote_subs(client, item_id: str, language: str = "ita") -> list[dict]:
    data = client._get(f"/Items/{item_id}/RemoteSearch/Subtitles/{language}")  # noqa: SLF001
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def jellyfin_download_remote_sub(client, item_id: str, subtitle_id: str) -> bool:
    """Ask Jellyfin to download a remote subtitle beside the media item."""
    try:
        client._post(f"/Items/{item_id}/RemoteSearch/Subtitles/{subtitle_id}", {})  # noqa: SLF001
        return True
    except Exception:
        return False


def jellyfin_attach_subtitle_file(
    client,
    item_id: str,
    srt_path: str,
    *,
    language: str = "ita",
    forced: bool = False,
) -> bool:
    """Attach an on-disk SRT to a JF item via the JSON subtitle upload API.

    Sidecar files alone are often dropped from PlaybackInfo for Http/.strm sources;
    this forces Jellyfin to register the external stream on the item.
    """
    import base64

    if not item_id or not srt_path or not os.path.isfile(srt_path):
        return False
    if xtream_playback_blocks_extra_streams():
        return False
    try:
        raw = open(srt_path, "rb").read()
    except OSError:
        return False
    if len(raw) < 20:
        return False
    lang = "ita" if language in {"it", "ita"} else language
    fmt = "srt"
    lower = srt_path.lower()
    if lower.endswith(".vtt"):
        fmt = "vtt"
    elif lower.endswith(".ass") or lower.endswith(".ssa"):
        fmt = "ass"
    payload = {
        "Language": lang,
        "Format": fmt,
        "IsForced": bool(forced),
        "IsHearingImpaired": False,
        "Data": base64.b64encode(raw).decode("ascii"),
    }
    try:
        client._post(f"/Videos/{item_id}/Subtitles", payload)  # noqa: SLF001
        return True
    except Exception:
        return False


def _jf_user_id(client, config: dict | None = None) -> str:
    uid = str(getattr(client, "_user_id_cache", "") or "").strip()
    if uid:
        return uid
    cfg = config if isinstance(config, dict) else {}
    username = str(cfg.get("jellyfin_username") or cfg.get("emby_username") or "").strip()
    resolve = getattr(client, "resolve_user_id", None)
    if username and callable(resolve):
        try:
            return str(resolve(username) or "").strip()
        except Exception:
            return ""
    return ""


def _copy_non_subtitle_streams(item: dict) -> list[dict]:
    streams: list[dict] = []
    for s in item.get("MediaStreams") or []:
        if not isinstance(s, dict):
            continue
        typ = str(s.get("Type") or "")
        if typ == "Subtitle":
            continue
        streams.append(
            {
                "Index": len(streams),
                "Type": typ,
                "Codec": s.get("Codec") or "",
                "Language": s.get("Language") or "",
                "Channels": int(s.get("Channels") or 0),
                "Width": int(s.get("Width") or 0),
                "Height": int(s.get("Height") or 0),
                "IsDefault": bool(s.get("IsDefault")),
                "IsExternal": False,
            }
        )
    return streams


def _ensure_video_stream(streams: list[dict]) -> list[dict]:
    """StrmMediaImport Apply replaces the whole stream list — never omit Video."""
    if any(str(s.get("Type") or "") == "Video" for s in streams):
        return streams
    restored = [
        {
            "Index": 0,
            "Type": "Video",
            "Codec": "h264",
            "IsExternal": False,
        }
    ]
    restored.extend(streams)
    for idx, row in enumerate(restored):
        row["Index"] = idx
    return restored


def _item_has_http_subtitle(item: dict, sub_http_url: str) -> bool:
    want = (sub_http_url or "").rstrip("/")
    if not want:
        return False
    tail = want.rsplit("/", 1)[-1]
    for s in item.get("MediaStreams") or []:
        if not isinstance(s, dict):
            continue
        if str(s.get("Type") or "") != "Subtitle":
            continue
        path = str(s.get("Path") or "").rstrip("/")
        if path == want or (tail and path.endswith(tail)):
            return True
    return False


def _item_has_video_stream(item: dict) -> bool:
    return any(
        str(s.get("Type") or "") == "Video"
        for s in (item.get("MediaStreams") or [])
        if isinstance(s, dict)
    )


def jellyfin_publish_http_subtitle(
    client,
    *,
    item_id: str,
    jf_path: str,
    sub_http_url: str,
    language: str = "ita",
    duration_sec: float = 0.0,
    size: int = 0,
    user_id: str = "",
    config: dict | None = None,
) -> bool:
    """Push an HTTP subtitle URL onto a .strm item via StrmMediaImport.

    Jellyfin's PlaybackInfo for Protocol=Http rebuilds streams from the remote
    probe and drops local sidecar SRT. An external subtitle whose Path is itself
    HTTP(S) is kept and exposed to GuamaFlix / Sodalite / Neptune.

    Apply replaces the entire stream list. We always keep/restore a Video
    stream so a subtitle-only payload cannot wipe the episode (and GuamaFlix
    artwork for the series).
    """
    if not client or not item_id or not jf_path or not sub_http_url:
        return False
    if not sub_http_url.startswith("http"):
        return False
    uid = (user_id or _jf_user_id(client, config)).strip()
    item: dict = {}
    if uid and hasattr(client, "get_item"):
        try:
            fetched = client.get_item(
                uid, item_id, "MediaStreams,RunTimeTicks,Size,Path"
            )
            if isinstance(fetched, dict):
                item = fetched
        except Exception:
            item = {}
    if not item:
        try:
            fetched = client._get(  # noqa: SLF001
                f"/Users/{uid}/Items/{item_id}" if uid else f"/Items/{item_id}",
                params={"Fields": "MediaStreams,RunTimeTicks,Size"},
            )
            if isinstance(fetched, dict):
                item = fetched
        except Exception:
            item = {}
    # GET /Items/{id} without userId 400s on current Jellyfin. Never Apply
    # from an empty item: that publishes subtitle-only and destroys the strm.
    if not item:
        return False

    already = _item_has_http_subtitle(item, sub_http_url)
    has_video = _item_has_video_stream(item)
    if already and has_video:
        return True

    streams = _ensure_video_stream(_copy_non_subtitle_streams(item))
    lang = "ita" if language in {"it", "ita"} else language
    streams.append(
        {
            "Index": len(streams),
            "Type": "Subtitle",
            "Codec": "srt",
            "Language": lang,
            "IsExternal": True,
            "IsDefault": True,
            "Path": sub_http_url,
            "Title": "Italian",
        }
    )
    ticks = int(item.get("RunTimeTicks") or 0)
    dur = float(duration_sec or 0) or (ticks / 10_000_000.0 if ticks else 0.0)
    sz = int(size or item.get("Size") or 0) or None
    payload_item: dict = {
        "Path": jf_path,
        "Streams": streams,
    }
    if dur > 0:
        payload_item["DurationSec"] = dur
    if sz and sz > 1000:
        payload_item["Size"] = sz
    try:
        client._post(  # noqa: SLF001
            "/StrmMediaImport/Apply",
            {"Items": [payload_item]},
        )
    except Exception:
        return False
    # Apply can drop episode stills when the previous stream list was invalid.
    # Image-only refresh (no metadata) re-fetches TMDB stills without pulling
    # extra OpenSubtitles attachments that would wipe Video again.
    if not (item.get("ImageTags") or {}):
        try:
            client._post(  # noqa: SLF001
                f"/Items/{item_id}/Refresh",
                None,
                params={
                    "Recursive": "false",
                    "MetadataRefreshMode": "None",
                    "ImageRefreshMode": "FullRefresh",
                    "ReplaceAllMetadata": "false",
                    "ReplaceAllImages": "false",
                },
            )
        except Exception:
            pass
    return True


def _jellyfin_pick_italian(entries: list[dict], *, prefer_forced: bool) -> tuple[dict | None, bool]:
    forced = [e for e in entries if e.get("Forced") is True or e.get("IsForced") is True]
    if prefer_forced and forced:
        return forced[0], True
    normal = [
        e
        for e in entries
        if not (e.get("HearingImpaired") is True or e.get("IsHearingImpaired") is True)
    ]
    pool = normal or entries
    return (pool[0] if pool else None), False


def _publish_http_sub_for_strm(
    client,
    *,
    item_id: str,
    strm_path: str,
    season: int,
    episode: int,
    language: str = "it",
    config: dict | None = None,
    log: LogFn | None = None,
    verify_playback_info: bool = True,
    user_id: str = "",
) -> bool:
    """Expose sidecar SRT as HTTP so PlaybackInfo keeps it for .strm Direct Play."""
    if not client or not item_id or not strm_path:
        return False
    # StrmMediaImport Apply mid-play makes GuamaFlix drop the session.
    if xtream_playback_blocks_extra_streams():
        _log(
            log,
            f"Sottotitoli S{season:02d}E{episode:02d}: HTTP Apply differito "
            f"(riproduzione strm in corso)",
        )
        return True
    folder = series_folder_from_path(strm_path)
    if not folder:
        return False
    try:
        from stream_proxy import build_episode_sub_proxy_url
    except Exception:
        return False
    try:
        sub_url = build_episode_sub_proxy_url(
            folder, int(season), int(episode), config=config
        )
    except Exception as exc:
        _log(log, f"Sottotitoli HTTP S{season:02d}E{episode:02d}: {exc}")
        return False
    # Map container strm path → Jellyfin library path.
    jf_path = strm_path
    if strm_path.startswith("/strm/series/"):
        jf_path = "/media/tv/" + strm_path[len("/strm/series/") :]
    elif "/m3u-editor/series/" in strm_path.replace("\\", "/"):
        # Host path variant
        idx = strm_path.replace("\\", "/").find("/series/")
        if idx >= 0:
            jf_path = "/media/tv/" + strm_path.replace("\\", "/")[idx + len("/series/") :]
    ok = jellyfin_publish_http_subtitle(
        client,
        item_id=item_id,
        jf_path=jf_path,
        sub_http_url=sub_url,
        language=language,
        user_id=user_id,
        config=config,
    )
    if not ok:
        return False
    if not verify_playback_info or xtream_playback_blocks_extra_streams():
        _log(
            log,
            f"Sottotitoli S{season:02d}E{episode:02d}: HTTP pubblicato "
            f"(probe PlaybackInfo saltato)",
        )
        return True
    # StrmMediaImport may accept Apply without actually keeping HTTP subtitle Paths.
    # Verify PlaybackInfo; if still missing, clients like GuamaFlix/Sodalite cannot see them.
    try:
        data = client._post(  # noqa: SLF001
            f"/Items/{item_id}/PlaybackInfo",
            {},
        )
        ms = (data.get("MediaSources") or [{}])[0]
        has_sub = any(
            str(s.get("Type") or "") == "Subtitle"
            for s in (ms.get("MediaStreams") or [])
        )
    except Exception:
        has_sub = False
    if has_sub:
        _log(
            log,
            f"Sottotitoli S{season:02d}E{episode:02d}: presenti in PlaybackInfo (HTTP)",
        )
        return True
    _log(
        log,
        f"Sottotitoli S{season:02d}E{episode:02d}: file OK su disco/proxy, ma Jellyfin "
        f"non li mette in PlaybackInfo per gli .strm HTTP (limite server/client)",
    )
    return False


def ensure_italian_subs_for_strm(
    *,
    strm_path: str,
    season: int,
    episode: int,
    tmdb_id: int | None = None,
    config: dict | None = None,
    client=None,
    item_id: str = "",
    prefer_forced: bool = True,
    language: str = "it",
    log: LogFn | None = None,
    refresh_paths: bool = True,
    verify_playback_info: bool = True,
    user_id: str = "",
) -> dict:
    """Ensure Italian sidecar next to strm: forced if available, else full."""
    result = {
        "ok": False,
        "skipped": False,
        "forced": False,
        "path": "",
        "error": "",
        "source": "",
    }
    if not strm_path or not os.path.isfile(strm_path):
        result["error"] = "strm_missing"
        return result
    has_any, has_forced = has_italian_sidecar(strm_path)
    if has_forced or (has_any and not prefer_forced):
        result["ok"] = True
        result["skipped"] = True
        result["forced"] = has_forced
        # Re-attach existing sidecar so JF clients can select it on .strm/Http.
        if client is not None and item_id:
            if xtream_playback_blocks_extra_streams():
                _log(
                    log,
                    f"Sottotitoli S{season:02d}E{episode:02d}: attach Jellyfin differito "
                    f"(riproduzione strm in corso)",
                )
            else:
                for path in sidecar_paths_for_strm(strm_path, forced=has_forced):
                    if os.path.isfile(path):
                        if jellyfin_attach_subtitle_file(
                            client, item_id, path, language=language, forced=has_forced
                        ):
                            result["source"] = "reattached"
                        break
                _publish_http_sub_for_strm(
                    client,
                    item_id=item_id,
                    strm_path=strm_path,
                    season=season,
                    episode=episode,
                    language=language,
                    config=config,
                    log=log,
                    verify_playback_info=verify_playback_info,
                    user_id=user_id,
                )
        return result
    if has_any and prefer_forced:
        # Keep full IT if forced unavailable; still try forced once.
        pass

    folder = series_folder_from_path(strm_path)
    tid = tmdb_id or tmdb_id_from_folder(folder)
    cfg = config or {}
    creds = load_opensubtitles_credentials(cfg)

    # 1) OpenSubtitles.com (can filter foreign_parts_only / forced)
    if tid and creds.get("username") and creds.get("password"):
        try:
            token = opensubtitles_login(creds)
            api_key = creds["api_key"]
            entries: list[dict] = []
            if prefer_forced:
                entries = search_episode_subtitles(
                    token=token,
                    api_key=api_key,
                    tmdb_id=tid,
                    season=season,
                    episode=episode,
                    languages=language,
                    foreign_parts_only=True,
                )
            pick, is_forced = pick_best_italian_subtitle(entries, prefer_forced=True)
            text = ""
            source = "opensubtitles"
            if pick:
                file_id = _pick_file_id(pick)
                if file_id:
                    text = download_subtitle_file(
                        token=token, api_key=api_key, file_id=file_id
                    )
            # IT forced missing → EN forced/FPO → DeepL Free → .ita.forced.srt
            if not text and prefer_forced and language in {"it", "ita"}:
                ep_label = f"Sottotitoli S{season:02d}E{episode:02d}"
                translated, err = translate_english_forced_to_italian(
                    token=token,
                    api_key=api_key,
                    tmdb_id=tid,
                    config=cfg,
                    season=season,
                    episode=episode,
                    log=log,
                    label=ep_label,
                )
                if translated:
                    text = translated
                    is_forced = True
                    source = "opensubtitles+deepl"
                elif err:
                    _log(log, _forced_fallback_message(ep_label, err))
            if not text:
                entries = search_episode_subtitles(
                    token=token,
                    api_key=api_key,
                    tmdb_id=tid,
                    season=season,
                    episode=episode,
                    languages=language,
                    foreign_parts_only=False,
                )
                pick, is_forced = pick_best_italian_subtitle(entries, prefer_forced=False)
                is_forced = False
                file_id = _pick_file_id(pick) if pick else None
                if file_id:
                    text = download_subtitle_file(
                        token=token, api_key=api_key, file_id=file_id
                    )
                    source = "opensubtitles"
            if text:
                # If we already have full and this isn't forced, skip overwrite.
                if not is_forced and has_any:
                    result["ok"] = True
                    result["skipped"] = True
                    result["forced"] = has_forced
                    result["source"] = "existing"
                    if client is not None and item_id:
                        _publish_http_sub_for_strm(
                            client,
                            item_id=item_id,
                            strm_path=strm_path,
                            season=season,
                            episode=episode,
                            language=language,
                            config=cfg,
                            log=log,
                            verify_playback_info=verify_playback_info,
                            user_id=user_id,
                        )
                    return result
                out = write_sidecar(strm_path, text, forced=bool(is_forced))
                result["ok"] = True
                result["forced"] = bool(is_forced)
                result["path"] = out
                result["source"] = source
                via = " (EN→IT DeepL)" if source == "opensubtitles+deepl" else ""
                _log(
                    log,
                    f"Sottotitoli S{season:02d}E{episode:02d}: "
                    f"{'forced' if is_forced else 'full'} IT{via} → {os.path.basename(out)}",
                )
                if client is not None and item_id:
                    if jellyfin_attach_subtitle_file(
                        client,
                        item_id,
                        out,
                        language=language,
                        forced=bool(is_forced),
                    ):
                        _log(log, f"Sottotitoli S{season:02d}E{episode:02d}: agganciati a Jellyfin")
                    _publish_http_sub_for_strm(
                        client,
                        item_id=item_id,
                        strm_path=strm_path,
                        season=season,
                        episode=episode,
                        language=language,
                        config=cfg,
                        log=log,
                        verify_playback_info=verify_playback_info,
                        user_id=user_id,
                    )
                if refresh_paths and client is not None and not xtream_playback_blocks_extra_streams():
                    try:
                        client.notify_library_paths(
                            [{"Path": os.path.dirname(strm_path), "UpdateType": "Modified"}]
                        )
                    except Exception:
                        pass
                return result
        except (HTTPError, URLError, RuntimeError, OSError, TimeoutError, ValueError) as exc:
            _log(log, f"OpenSubtitles S{season:02d}E{episode:02d}: {exc}")

    # 2) Fallback: Jellyfin remote search (usually full IT only)
    if client is not None and item_id and not xtream_playback_blocks_extra_streams():
        try:
            lang = "ita" if language in {"it", "ita"} else language
            entries = jellyfin_search_remote_subs(client, item_id, lang)
            pick, is_forced = _jellyfin_pick_italian(entries, prefer_forced=prefer_forced)
            if pick and pick.get("Id"):
                # JF writes beside the media item (strm folder) when permitted.
                before = set()
                season_dir = os.path.dirname(strm_path)
                if os.path.isdir(season_dir):
                    before = set(os.listdir(season_dir))
                ok = jellyfin_download_remote_sub(client, item_id, str(pick["Id"]))
                time.sleep(1.0)
                after = set(os.listdir(season_dir)) if os.path.isdir(season_dir) else set()
                new_files = sorted(after - before)
                # Prefer our naming if JF wrote something else / nothing usable.
                has_any2, has_forced2 = has_italian_sidecar(strm_path)
                if has_any2:
                    result["ok"] = True
                    result["forced"] = has_forced2
                    result["source"] = "jellyfin"
                    result["skipped"] = False
                    _log(
                        log,
                        f"Sottotitoli S{season:02d}E{episode:02d}: via Jellyfin "
                        f"({'forced' if has_forced2 else 'full'} IT)",
                    )
                    _publish_http_sub_for_strm(
                        client,
                        item_id=item_id,
                        strm_path=strm_path,
                        season=season,
                        episode=episode,
                        language=language,
                        config=cfg,
                        log=log,
                        verify_playback_info=verify_playback_info,
                        user_id=user_id,
                    )
                    return result
                if ok and new_files:
                    # Rename first new srt to our convention if needed.
                    for name in new_files:
                        if name.lower().endswith((".srt", ".vtt", ".ass")):
                            src = os.path.join(season_dir, name)
                            try:
                                with open(src, "r", encoding="utf-8", errors="replace") as fh:
                                    text = fh.read()
                                out = write_sidecar(strm_path, text, forced=bool(is_forced))
                                if os.path.abspath(src) != os.path.abspath(out):
                                    try:
                                        os.remove(src)
                                    except OSError:
                                        pass
                                result["ok"] = True
                                result["forced"] = bool(is_forced)
                                result["path"] = out
                                result["source"] = "jellyfin"
                                _log(
                                    log,
                                    f"Sottotitoli S{season:02d}E{episode:02d}: Jellyfin → "
                                    f"{os.path.basename(out)}",
                                )
                                return result
                            except OSError:
                                continue
        except Exception as exc:
            _log(log, f"Jellyfin subs S{season:02d}E{episode:02d}: {exc}")

    if has_any:
        result["ok"] = True
        result["skipped"] = True
        result["forced"] = has_forced
        result["source"] = "existing"
        return result
    result["error"] = "not_found"
    _log(log, f"Sottotitoli S{season:02d}E{episode:02d}: nessun IT trovato")
    return result


def episode_in_scope(
    ep_season: int,
    ep_num: int,
    *,
    season: int,
    from_episode: int,
    all_seasons: bool,
) -> bool:
    if all_seasons:
        return True
    return int(ep_season) == int(season) and int(ep_num) >= int(from_episode)


def ensure_season_subtitles(
    client,
    *,
    user_id: str,
    series_id: str,
    season: int,
    config: dict,
    from_episode: int = 1,
    prefer_forced: bool = True,
    language: str = "it",
    log: LogFn | None = None,
    verify_playback_info: bool = True,
    all_seasons: bool = False,
) -> dict:
    summary = {"ok": 0, "skipped": 0, "failed": 0, "episodes": []}
    try:
        episodes = client.get_series_episodes(user_id, series_id)
    except Exception as exc:
        _log(log, f"Sottotitoli: impossibile elencare episodi ({exc})")
        return summary
    for ep in episodes:
        try:
            ep_season = int(ep.get("ParentIndexNumber"))
            ep_num = int(ep.get("IndexNumber"))
        except (TypeError, ValueError):
            continue
        if not episode_in_scope(
            ep_season,
            ep_num,
            season=season,
            from_episode=from_episode,
            all_seasons=all_seasons,
        ):
            continue
        item_id = str(ep.get("Id") or "")
        media_path = str(ep.get("Path") or "")
        strm = resolve_strm_path(
            media_path,
            server=getattr(client, "server_type", "jellyfin"),
            config=config,
        )
        if not strm:
            # Path may already be container-local
            local_guess = media_path
            if local_guess.startswith("/media/tv/"):
                local_guess = STRM_OUTPUT_SERIES_PATH + local_guess[len("/media/tv") :]
            if local_guess.lower().endswith(".strm") and os.path.isfile(local_guess):
                strm = local_guess
        if not strm:
            summary["failed"] += 1
            summary["episodes"].append({"episode": ep_num, "error": "strm_unmapped"})
            continue
        info = ensure_italian_subs_for_strm(
            strm_path=strm,
            season=ep_season,
            episode=ep_num,
            config=config,
            client=client,
            item_id=item_id,
            prefer_forced=prefer_forced,
            language=language,
            log=log,
            verify_playback_info=verify_playback_info,
            user_id=user_id,
        )
        summary["episodes"].append(info)
        if info.get("skipped"):
            summary["skipped"] += 1
        elif info.get("ok"):
            summary["ok"] += 1
        else:
            summary["failed"] += 1
        time.sleep(0.35)  # gentle on OpenSubtitles rate limits
    return summary


def schedule_subs_job(key: str, target, *args, **kwargs) -> bool:
    with _bg_lock:
        if key in _bg_keys:
            return False
        _bg_keys.add(key)

    def _runner() -> None:
        try:
            target(*args, **kwargs)
        finally:
            with _bg_lock:
                _bg_keys.discard(key)

    threading.Thread(target=_runner, daemon=True, name=f"auto-subs-{key[:40]}").start()
    return True


_movie_queue: list[str] = []
_movie_queue_lock = threading.Lock()
_request_lock = threading.Lock()


def fetch_subtitle_variant(
    strm_path: str,
    *,
    language: str,
    forced: bool,
    season: int | None = None,
    episode: int | None = None,
    tmdb_id: int | None = None,
    config: dict | None = None,
    log: LogFn | None = None,
) -> dict:
    """Download one subtitle variant next to a strm. Forced requests ignore full subtitles."""
    result = {
        "ok": False,
        "skipped": False,
        "forced": bool(forced),
        "language": language,
        "path": "",
        "error": "",
    }
    if not strm_path or not os.path.isfile(strm_path):
        result["error"] = "strm_missing"
        return result
    if has_language_sidecar(strm_path, language=language, forced=forced):
        result["ok"] = True
        result["skipped"] = True
        result["path"] = _existing_sidecar(strm_path, language=language, forced=forced)
        return result
    is_episode = season is not None and episode is not None
    tid = tmdb_id or tmdb_id_for_strm(strm_path, episode=is_episode)
    if not tid:
        result["error"] = "no_tmdb"
        _log(log, f"Sottotitoli {os.path.basename(strm_path)}: tmdbid assente")
        return result
    creds = load_opensubtitles_credentials(config or {})
    if not creds.get("username") or not creds.get("password"):
        result["error"] = "no_credentials"
        return result
    label = f"S{int(season):02d}E{int(episode):02d}" if is_episode else os.path.basename(strm_path)
    kind = "forced" if forced else "full"
    try:
        token = opensubtitles_login(creds)
        api_key = creds["api_key"]
        if is_episode:
            entries = search_episode_subtitles(
                token=token,
                api_key=api_key,
                tmdb_id=tid,
                season=int(season or 0),
                episode=int(episode or 0),
                languages=language,
                foreign_parts_only=forced,
            )
        else:
            entries = search_movie_subtitles(
                token=token,
                api_key=api_key,
                tmdb_id=tid,
                languages=language,
                foreign_parts_only=forced,
            )
        pick = choose_subtitle_entry(entries, forced=forced, trust_query=True)
        file_id = _pick_file_id(pick) if pick else None
        text = ""
        source = "opensubtitles"
        if file_id:
            text = download_subtitle_file(token=token, api_key=api_key, file_id=file_id)
        # Forced IT missing → EN forced/FPO translated with DeepL Free.
        if (
            not text
            and forced
            and language in {"it", "ita"}
        ):
            job_label = f"Sottotitoli {label}"
            translated, err = translate_english_forced_to_italian(
                token=token,
                api_key=api_key,
                tmdb_id=tid,
                config=config or {},
                season=season,
                episode=episode,
                log=log,
                label=job_label,
            )
            if translated:
                text = translated
                source = "opensubtitles+deepl"
            elif err:
                _log(log, _forced_fallback_message(job_label, err))
                if err == "deepl_quota":
                    result["error"] = "deepl_quota"
                    return result
        if not text:
            result["error"] = "not_found"
            if not (forced and language in {"it", "ita"}):
                _log(log, f"Sottotitoli {label}: nessun {language} {kind}")
            return result
        out = write_language_sidecar(strm_path, text, language=language, forced=forced)
        result["ok"] = True
        result["path"] = out
        result["source"] = source
        via = " (EN→IT DeepL)" if source == "opensubtitles+deepl" else ""
        _log(
            log,
            f"Sottotitoli {label}: {language} {kind}{via} → {os.path.basename(out)}",
        )
        return result
    except (HTTPError, URLError, RuntimeError, OSError, TimeoutError, ValueError) as exc:
        result["error"] = str(exc)
        _log(log, f"Sottotitoli {label}: {exc}")
        return result


_drain_thread_lock = threading.Lock()
_drain_thread_started = False


def subtitle_jobs_log_path() -> str:
    from core import DATA_DIR

    return os.path.join(DATA_DIR, "subtitle_jobs.log")


def _append_job_log(message: str) -> None:
    path = subtitle_jobs_log_path()
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(message.rstrip() + "\n")
    except OSError:
        pass


def read_subtitle_jobs_log(*, limit: int = 80) -> list[str]:
    """Latest lines from the overnight / queued forced-subtitle job log."""
    path = subtitle_jobs_log_path()
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip("\n") for ln in fh.readlines()]
    except OSError:
        return []
    if limit <= 0:
        return lines
    return lines[-limit:]


def _drain_new_movie_forced(language: str) -> None:
    from core import load_auto_download_config

    global _drain_thread_started
    try:
        while True:
            with _movie_queue_lock:
                strm_path = _movie_queue.pop(0) if _movie_queue else ""
            if not strm_path:
                return
            config = load_auto_download_config()
            fetch_subtitle_variant(
                strm_path,
                language=language,
                forced=True,
                config=config,
                log=_append_job_log,
                **subtitle_job_kwargs(strm_path),
            )
            time.sleep(0.4)
    finally:
        with _drain_thread_lock:
            _drain_thread_started = False
        with _movie_queue_lock:
            more = bool(_movie_queue)
        if more:
            _ensure_movie_drain(language)


def _ensure_movie_drain(language: str) -> bool:
    global _drain_thread_started
    with _drain_thread_lock:
        if _drain_thread_started:
            return False
        _drain_thread_started = True
    threading.Thread(
        target=_drain_new_movie_forced,
        args=(language,),
        daemon=True,
        name="auto-subs-new-movies",
    ).start()
    return True


def subtitle_job_kwargs(strm_path: str) -> dict:
    """Season and episode when the path is an episode strm. Movies stay unscoped."""
    from core import parse_episode_numbers_from_path

    numbers = parse_episode_numbers_from_path(strm_path)
    if not numbers:
        return {}
    season, episode = numbers
    return {"season": season, "episode": episode}


def queue_forced_subs_for_new_movies(
    paths: list[str], *, language: str = "it", kind: str = "movies"
) -> str:
    """Queue a forced-only search for strm files that were just created or updated."""
    label = "episodes" if kind == "episodes" else "movies"
    unique = []
    seen = set()
    for path in paths:
        if path and path not in seen and os.path.isfile(path):
            seen.add(path)
            unique.append(path)
    if not unique:
        return ""
    from core import load_auto_download_config

    config = load_auto_download_config()
    creds = load_opensubtitles_credentials(config)
    if not creds.get("username") or not creds.get("password"):
        return (
            f"Forced subtitle search skipped for {len(unique)} new {label}: "
            "OpenSubtitles credentials missing"
        )
    with _movie_queue_lock:
        queued = set(_movie_queue)
        for path in unique:
            if path not in queued:
                _movie_queue.append(path)
                queued.add(path)
    if not _ensure_movie_drain(language):
        return f"Forced subtitle search already running; added {len(unique)} new {label}"
    return f"Forced subtitle search queued for {len(unique)} new {label}"


_LIBRARY_PREFIXES = (
    "/mnt/union/Movies",
    "/mnt/wsl/union/Movies",
    "/mnt/wsl/HDD1/Movies",
    "/mnt/union/Serie_Tv",
    "/mnt/wsl/union/Serie_Tv",
    "/mnt/wsl/HDD1/Serie_Tv",
)


def resolve_server_media_path(
    path: str,
    *,
    server: str,
    config: dict | None = None,
    movie_roots: tuple[str, ...] | None = None,
    series_roots: tuple[str, ...] | None = None,
) -> str | None:
    """Turn a Silo/Emby/Jellyfin path into a file this container can write beside.

    Silo stores the union path (`/mnt/union/...`). The container mounts the local
    branch and the strm branch separately, so the same relative path is tried
    on both.
    """
    from core import (
        DOWNLOAD_MOVIES_PATH,
        DOWNLOAD_TV_PATH,
        STRM_OUTPUT_MOVIES_PATH,
        STRM_OUTPUT_SERIES_PATH,
    )

    raw = (path or "").replace("\\", "/")
    if raw and os.path.isfile(raw):
        return raw
    mapped = map_media_server_path_to_local(raw, server=server, config=config)
    if mapped and os.path.isfile(mapped):
        return mapped
    movies = movie_roots or (DOWNLOAD_MOVIES_PATH, STRM_OUTPUT_MOVIES_PATH)
    series = series_roots or (DOWNLOAD_TV_PATH, STRM_OUTPUT_SERIES_PATH)
    for prefix in _LIBRARY_PREFIXES:
        if raw != prefix and not raw.startswith(prefix + "/"):
            continue
        rel = raw[len(prefix) :]
        roots = movies if prefix.rstrip("/").endswith("Movies") else series
        for root in roots:
            candidate = root.rstrip("/") + rel
            if os.path.isfile(candidate):
                return candidate
    return None


def search_enabled_libraries(
    query: str,
    *,
    kind: str,
    config: dict | None = None,
    limit: int = 20,
) -> list[dict]:
    """Titles from the libraries of the enabled Emby, Jellyfin and Silo servers."""
    from core import load_auto_download_config

    cfg = config if config is not None else load_auto_download_config()
    servers = (
        ("silo", "silo_enabled", "silo_url", "silo_api_key", "silo_username", "Silo"),
        ("jellyfin", "jellyfin_enabled", "jellyfin_url", "jellyfin_api_key", "jellyfin_username", "Jellyfin"),
        ("emby", "emby_enabled", "emby_url", "emby_api_key", "emby_username", "Emby"),
    )
    hits: list[dict] = []
    for server, enabled, url_key, key_key, user_key, label in servers:
        if not cfg.get(enabled):
            continue
        url = str(cfg.get(url_key) or "").strip()
        api_key = str(cfg.get(key_key) or "").strip()
        if not url or not api_key:
            continue
        try:
            if server == "silo":
                from silo_client import SiloClient

                rows = SiloClient(url, api_key).search_library(kind, query, limit)
            else:
                from emby_watcher import MediaServerClient

                client = MediaServerClient(url, api_key, server)
                user_id = client.resolve_user_id(str(cfg.get(user_key) or ""))
                if not user_id:
                    continue
                rows = client.search_library(user_id, kind, query, limit)
        except Exception:
            continue
        for row in rows:
            item_id = str(row.get("Id") or "").strip()
            title = str(row.get("Name") or "").strip()
            if not item_id or not title:
                continue
            hits.append(
                {
                    "server": server,
                    "server_label": label,
                    "id": item_id,
                    "title": title,
                    "year": row.get("Year") or "",
                }
            )
    return hits


def library_media_targets(
    *,
    server: str,
    item_id: str,
    kind: str,
    config: dict,
) -> list[tuple[str, int | None, int | None]]:
    """Local strm or video files for one library movie or every episode of a series."""
    url_key = f"{server}_url"
    key_key = f"{server}_api_key"
    user_key = f"{server}_username"
    url = str(config.get(url_key) or "").strip()
    api_key = str(config.get(key_key) or "").strip()
    if not url or not api_key or not item_id:
        return []
    if server == "silo":
        from silo_client import SiloClient

        client = SiloClient(url, api_key)
        user_id = ""
    else:
        from emby_watcher import MediaServerClient

        client = MediaServerClient(url, api_key, server)
        user_id = client.resolve_user_id(str(config.get(user_key) or "")) or ""
        if not user_id:
            return []
    targets: list[tuple[str, int | None, int | None]] = []
    if kind == "series":
        try:
            episodes = client.get_series_episodes(user_id, item_id)
        except Exception:
            return []
        for episode in episodes:
            local = resolve_server_media_path(
                str(episode.get("Path") or ""),
                server=server,
                config=config,
            )
            if not local:
                continue
            try:
                season = int(episode.get("ParentIndexNumber"))
                number = int(episode.get("IndexNumber"))
            except (TypeError, ValueError):
                season, number = None, None
            targets.append((local, season, number))
        return targets
    try:
        item = client.get_item(user_id, item_id, "Path")
    except Exception:
        return []
    local = resolve_server_media_path(str((item or {}).get("Path") or ""), server=server, config=config)
    if local:
        targets.append((local, None, None))
    return targets


def list_title_folders(root: str) -> list[str]:
    if not root or not os.path.isdir(root):
        return []
    names = []
    for name in os.listdir(root):
        if os.path.isdir(os.path.join(root, name)):
            names.append(name)
    names.sort(key=str.casefold)
    return names


def movie_strm_in_folder(folder: str) -> str | None:
    if not os.path.isdir(folder):
        return None
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(".strm"):
            return os.path.join(folder, name)
    return None


def episode_strms_in_series(folder: str) -> list[tuple[str, int, int]]:
    found: list[tuple[str, int, int]] = []
    if not os.path.isdir(folder):
        return found
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            match = _SE_RE.search(name)
            if not match:
                continue
            found.append(
                (os.path.join(dirpath, name), int(match.group(1)), int(match.group(2)))
            )
    found.sort(key=lambda item: (item[1], item[2], item[0]))
    return found


def subtitle_request_status_path() -> str:
    from core import DATA_DIR

    return os.path.join(DATA_DIR, "subtitle_request.json")


def load_subtitle_request_status() -> dict:
    path = subtitle_request_status_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_subtitle_request_status(status: dict) -> None:
    path = subtitle_request_status_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(status, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _append_subtitle_log(message: str) -> None:
    with _request_lock:
        status = load_subtitle_request_status()
        lines = list(status.get("log") or [])
        lines.append(message)
        status["log"] = lines[-40:]
        _write_subtitle_request_status(status)


def run_subtitle_request(
    *,
    kind: str,
    folder: str = "",
    languages: list[str],
    want_forced: bool,
    want_full: bool,
    config: dict | None = None,
    targets: list[tuple[str, int | None, int | None]] | None = None,
    title: str = "",
) -> None:
    """Download the requested variants for one movie or every episode of one series."""
    from core import load_auto_download_config

    cfg = config or load_auto_download_config()
    langs = []
    for language in languages:
        code = (language or "").strip().lower()
        if code and code not in langs:
            langs.append(code)
    if targets is None:
        targets = []
        if kind == "series":
            for path, season, episode in episode_strms_in_series(folder):
                targets.append((path, season, episode))
        else:
            strm = movie_strm_in_folder(folder)
            if strm:
                targets.append((strm, None, None))
    label = title or os.path.basename(folder) or "titolo"
    variants = []
    if want_forced:
        variants.append(True)
    if want_full:
        variants.append(False)
    total = len(targets) * len(langs) * len(variants)
    if total == 0:
        _write_subtitle_request_status(
            {
                "running": False,
                "done": 0,
                "total": 0,
                "ok": 0,
                "skipped": 0,
                "failed": 0,
                "title": label,
                "log": [f"Nessun file trovato per {label}"],
            }
        )
        return
    status = {
        "running": True,
        "done": 0,
        "total": total,
        "ok": 0,
        "skipped": 0,
        "failed": 0,
        "title": label,
        "log": [f"Richiesta: {label} ({total} file)"],
        "results": [],
    }
    _write_subtitle_request_status(status)
    done = 0
    for strm_path, season, episode in targets:
        for language in langs:
            for forced in variants:
                info = fetch_subtitle_variant(
                    strm_path,
                    language=language,
                    forced=forced,
                    season=season,
                    episode=episode,
                    config=cfg,
                    log=_append_subtitle_log,
                )
                done += 1
                media_label = (
                    f"S{int(season):02d}E{int(episode):02d}"
                    if season and episode
                    else os.path.basename(strm_path)
                )
                row = subtitle_result_row(info, label=media_label)
                with _request_lock:
                    current = load_subtitle_request_status()
                    current["done"] = done
                    current["total"] = total
                    results = list(current.get("results") or [])
                    results.append(row)
                    current["results"] = results[-80:]
                    if info.get("skipped"):
                        current["skipped"] = int(current.get("skipped") or 0) + 1
                    elif info.get("ok"):
                        current["ok"] = int(current.get("ok") or 0) + 1
                    else:
                        current["failed"] = int(current.get("failed") or 0) + 1
                    _write_subtitle_request_status(current)
                time.sleep(0.4)
    with _request_lock:
        current = load_subtitle_request_status()
        current["running"] = False
        lines = list(current.get("log") or [])
        lines.append(
            f"Finito: ok={current.get('ok', 0)} skip={current.get('skipped', 0)} "
            f"fail={current.get('failed', 0)}"
        )
        current["log"] = lines[-40:]
        _write_subtitle_request_status(current)


def run_library_subtitle_request(
    *,
    server: str,
    item_id: str,
    kind: str,
    title: str,
    languages: list[str],
    want_forced: bool,
    want_full: bool,
    config: dict | None = None,
) -> None:
    from core import load_auto_download_config

    cfg = config or load_auto_download_config()
    targets = library_media_targets(server=server, item_id=item_id, kind=kind, config=cfg)
    run_subtitle_request(
        kind=kind,
        languages=languages,
        want_forced=want_forced,
        want_full=want_full,
        config=cfg,
        targets=targets,
        title=title,
    )


def start_subtitle_request(**kwargs) -> bool:
    return schedule_subs_job("subtitle-request", run_subtitle_request, **kwargs)


def start_library_subtitle_request(**kwargs) -> bool:
    return schedule_subs_job("subtitle-request", run_library_subtitle_request, **kwargs)

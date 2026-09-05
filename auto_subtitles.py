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
    params: dict[str, object] = {
        "languages": languages,
        "parent_tmdb_id": int(tmdb_id),
        "season_number": int(season),
        "episode_number": int(episode),
        "type": "episode",
        "order_by": "download_count",
        "order_direction": "desc",
    }
    if foreign_parts_only is True:
        params["foreign_parts_only"] = "only"
    elif foreign_parts_only is False:
        params["foreign_parts_only"] = "exclude"
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


def sidecar_paths_for_strm(strm_path: str, *, forced: bool) -> list[str]:
    """Possible sidecar names Jellyfin accepts next to a .strm."""
    base = strm_path[:-5] if strm_path.lower().endswith(".strm") else strm_path
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
            if not pick:
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
                text = download_subtitle_file(token=token, api_key=api_key, file_id=file_id)
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
                result["source"] = "opensubtitles"
                _log(
                    log,
                    f"Sottotitoli S{season:02d}E{episode:02d}: "
                    f"{'forced' if is_forced else 'full'} IT → {os.path.basename(out)}",
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
        if ep_season != int(season) or ep_num < int(from_episode):
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

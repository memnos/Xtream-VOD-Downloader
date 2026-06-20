import json
import os
import re
import subprocess
import time
from datetime import datetime
from urllib.parse import urlparse

import requests

DOWNLOAD_MOVIES_PATH = "/download/movies"
DOWNLOAD_TV_PATH = "/download/tv"
DOWNLOAD_TV2_PATH = "/download/tv-2"

STRM_MOVIES_PATH = os.environ.get("STRM_MOVIES_PATH", "/strm/movies")
STRM_SERIES_PATH = os.environ.get("STRM_SERIES_PATH", "/strm/series")

SEASON_DIR_RE = re.compile(r"^Season\s*0*(\d+)\s*$", re.IGNORECASE)

DOWNLOAD_CONFIG = {
    "movies": DOWNLOAD_MOVIES_PATH,
    "tv": DOWNLOAD_TV_PATH,
    "tv2": DOWNLOAD_TV2_PATH,
}

SERIES_DOWNLOAD_PATHS = (DOWNLOAD_TV_PATH, DOWNLOAD_TV2_PATH)
DEFAULT_SERIES_DEST = DOWNLOAD_TV_PATH

DOWNLOAD_PROGRESS_RE = re.compile(r"\[download\]\s+([\d.]+)%")
XTREAM_SERIES_EPISODE_URL_RE = re.compile(
    r"/series/[^/]+/[^/]+/(\d+)\.(\w+)/?\s*$", re.IGNORECASE
)
DATA_DIR = os.environ.get("DATA_DIR", "/app/.data")
CREDENTIALS_FILE = os.environ.get(
    "CREDENTIALS_FILE", os.path.join(DATA_DIR, "xtream_credentials.json")
)
HIDDEN_CATEGORIES_FILE = os.environ.get(
    "HIDDEN_CATEGORIES_FILE", os.path.join(DATA_DIR, "hidden_categories.json")
)
AUTO_DOWNLOAD_FILE = os.environ.get(
    "AUTO_DOWNLOAD_FILE", os.path.join(DATA_DIR, "auto_download.json")
)
WATCHER_STATUS_FILE = os.environ.get(
    "WATCHER_STATUS_FILE", os.path.join(DATA_DIR, "watcher_status.json")
)
PLAYBACK_HISTORY_FILE = os.environ.get(
    "PLAYBACK_HISTORY_FILE", os.path.join(DATA_DIR, "playback_history.json")
)
MAX_PLAYBACK_HISTORY = 10
DOWNLOAD_HISTORY_FILE = os.environ.get(
    "DOWNLOAD_HISTORY_FILE", os.path.join(DATA_DIR, "download_history.json")
)
STREAM_PROBE_CACHE_FILE = os.environ.get(
    "STREAM_PROBE_CACHE_FILE", os.path.join(DATA_DIR, "stream_probe_cache.json")
)
PROBE_CACHE_MAX_AGE = int(os.environ.get("PROBE_CACHE_MAX_AGE", str(7 * 86400)))
MAX_DOWNLOAD_HISTORY = 20
DIR_MODE = 0o777
FILE_MODE = 0o664
DOWNLOAD_ROOTS = (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
    DOWNLOAD_TV2_PATH,
)
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}

# Suffix in downloaded filenames — easy to spot local files vs .strm in the media library UI.
LOCAL_DOWNLOAD_MARKER = " [LOCAL]"


def url_hostname(url: str) -> str:
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        return ""


def read_strm_url(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            line = f.readline().strip()
        return line or None
    except OSError:
        return None


def playback_blocks_xtream_download(item_path: str, xtream_host: str) -> bool:
    xtream_domain = url_hostname(xtream_host)
    if not xtream_domain or not item_path:
        return False

    path_lower = item_path.lower()
    if path_lower.endswith(".strm"):
        strm_url = read_strm_url(item_path)
        if strm_url:
            return url_hostname(strm_url) == xtream_domain
        return True

    ext = os.path.splitext(item_path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return False

    return False


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def normalize_title(name: str) -> str:
    text = name.lower().strip()
    text = re.sub(r"^\|[^|]+\|", "", text)
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


QUALITY_4K_RE = re.compile(
    r"\b(?:4k|uhd|2160\s*p?|ultra[\s-]?hd)\b",
    re.IGNORECASE,
)
QUALITY_RESOLUTION_RE = re.compile(
    r"\b(\d{3,4})\s*p?\b",
    re.IGNORECASE,
)
QUALITY_SOURCE_SCORES = (
    (re.compile(r"\b(?:bluray|blu[\s-]?ray|bdrip|brrip|bdremux)\b", re.I), 40),
    (re.compile(r"\b(?:web[\s-]?dl|webdl)\b", re.I), 30),
    (re.compile(r"\bwebrip\b", re.I), 25),
    (re.compile(r"\bhdtv\b", re.I), 15),
    (re.compile(r"\b(?:cam|ts|telesync|screener)\b", re.I), -20),
)
QUALITY_MARKER_RE = re.compile(
    r"\b(?:4k|uhd|2160\s*p?|ultra[\s-]?hd|1080\s*p?|720\s*p?|480\s*p?|540\s*p?|"
    r"fhd|full[\s-]?hd|hd\b|sd\b|bluray|blu[\s-]?ray|bdrip|brrip|bdremux|"
    r"web[\s-]?dl|webdl|webrip|hdtv|x264|x265|hevc|h264|h265|aac|ac3|dts)\b",
    re.IGNORECASE,
)
NAME_SOURCE_TAGS = (
    (re.compile(r"\b(?:cam|hdcam|telesync|screener)\b", re.I), "CAM"),
    (re.compile(r"\b(?:ts|telecine)\b", re.I), "TS"),
    (re.compile(r"\b(?:bluray|blu[\s-]?ray|bdremux)\b", re.I), "BluRay"),
    (re.compile(r"\b(?:bdrip|brrip)\b", re.I), "BluRay Rip"),
    (re.compile(r"\b(?:web[\s-]?dl|webdl)\b", re.I), "WEB-DL"),
    (re.compile(r"\bwebrip\b", re.I), "WEBRip"),
    (re.compile(r"\bhdtv\b", re.I), "HDTV"),
)
NAME_AUDIO_TAGS = (
    (re.compile(r"\btruehd\b", re.I), "TrueHD"),
    (re.compile(r"\batmos\b", re.I), "Atmos"),
    (re.compile(r"\beac3\b", re.I), "EAC3"),
    (re.compile(r"\bdts[\s-]?hd\b", re.I), "DTS-HD"),
    (re.compile(r"\bdts\b", re.I), "DTS"),
    (re.compile(r"\bac3\b", re.I), "AC3"),
    (re.compile(r"\baac\b", re.I), "AAC"),
    (re.compile(r"\bmd\b", re.I), "MD"),
    (re.compile(r"\bmulti[\s-]?audio\b", re.I), "Multi-audio"),
    (re.compile(r"\bdual[\s-]?audio\b", re.I), "Dual-audio"),
)
NAME_LANG_TAGS = (
    (re.compile(r"\b(?:ita|italian|italiano)\b", re.I), "ITA"),
    (re.compile(r"\b(?:eng|english|inglese)\b", re.I), "ENG"),
    (re.compile(r"\b(?:sub[\s-]?ita|subbed)\b", re.I), "SUB ITA"),
)


def is_4k_title(name: str) -> bool:
    return bool(QUALITY_4K_RE.search(name or ""))


def resolution_score(name: str) -> int:
    text = name or ""
    if is_4k_title(text):
        return 2160
    match = QUALITY_RESOLUTION_RE.search(text)
    if match:
        value = int(match.group(1))
        if value in (480, 540, 576, 720, 1080, 2160):
            return value
    lowered = text.lower()
    if re.search(r"\b(?:1080|fhd|full[\s-]?hd)\b", lowered):
        return 1080
    if re.search(r"\b720\b", lowered):
        return 720
    if re.search(r"\b(?:480|540|576|sd)\b", lowered):
        return 480
    return 0


def source_score(name: str) -> int:
    score = 0
    for pattern, points in QUALITY_SOURCE_SCORES:
        if pattern.search(name or ""):
            score = max(score, points)
    return score


def quality_rank(name: str) -> tuple[int, int, int]:
    return (resolution_score(name), source_score(name), -len(name or ""))


def quality_rank_for_item(name: str, probe: dict | None = None) -> tuple[int, int, int, int]:
    name_res, name_src, name_len = quality_rank(name)
    if name_res > 0:
        audio_bonus = int((probe or {}).get("audio_bitrate") or 0)
        return (name_res, name_src, audio_bonus, name_len)
    if probe:
        height = int(probe.get("height") or 0)
        width = int(probe.get("width") or 0)
        res = max(height, width)
        if height >= 2160 or width >= 3840:
            res = 2160
        video_br = int(probe.get("bitrate") or 0)
        audio_br = int(probe.get("audio_bitrate") or 0)
        return (res, video_br, audio_br, source_score(name))
    return (name_res, name_src, 0, name_len)


def normalize_base_title(name: str) -> str:
    text = name.lower().strip()
    text = re.sub(r"^\|[^|]+\|", "", text)
    text = QUALITY_MARKER_RE.sub(" ", text)
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def catalog_title_key(name: str) -> str:
    return normalize_base_title(name) or normalize_title(name)


def _item_stream_key(item: dict) -> str:
    return str(item.get("stream_id") or item.get("series_id") or "")


def _item_is_4k(item: dict, probes: dict[str, dict] | None, name_key: str = "name") -> bool:
    name = str(item.get(name_key, ""))
    if is_4k_title(name):
        return True
    if probes:
        probe = probes.get(_item_stream_key(item))
        if probe and is_4k_probe(probe):
            return True
    return False


def pick_best_catalog_item(
    items: list[dict],
    *,
    allow_4k: bool = False,
    name_key: str = "name",
    probes: dict[str, dict] | None = None,
) -> dict | None:
    if not items:
        return None
    candidates = items
    if not allow_4k:
        candidates = [
            item for item in items if not _item_is_4k(item, probes, name_key=name_key)
        ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: quality_rank_for_item(
            str(item.get(name_key, "")),
            (probes or {}).get(_item_stream_key(item)),
        ),
    )


def dedupe_catalog_by_quality(
    items: list[dict],
    *,
    allow_4k: bool = False,
    name_key: str = "name",
    probes: dict[str, dict] | None = None,
) -> tuple[list[dict], int]:
    groups = group_catalog_versions(items, name_key=name_key)

    result: list[dict] = []
    for group in groups.values():
        best = pick_best_catalog_item(
            group,
            allow_4k=allow_4k,
            name_key=name_key,
            probes=probes,
        )
        if best:
            result.append(best)
    return result, len(items)


def group_catalog_versions(
    items: list[dict],
    *,
    name_key: str = "name",
) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for item in items:
        key = catalog_title_key(str(item.get(name_key, "")))
        if not key:
            continue
        groups.setdefault(key, []).append(item)
    return groups


def sort_catalog_versions(
    group: list[dict],
    probes: dict[str, dict] | None = None,
    *,
    name_key: str = "name",
) -> list[dict]:
    return sorted(
        group,
        key=lambda item: quality_rank_for_item(
            str(item.get(name_key, "")),
            (probes or {}).get(_item_stream_key(item)),
        ),
        reverse=True,
    )


def extract_name_tags(name: str) -> list[str]:
    text = name or ""
    tags: list[str] = []
    for pattern, label in NAME_SOURCE_TAGS + NAME_AUDIO_TAGS + NAME_LANG_TAGS:
        if pattern.search(text) and label not in tags:
            tags.append(label)
    return tags


def format_quality_label(name: str, probe: dict | None = None) -> str:
    parts: list[str] = extract_name_tags(name)

    if is_4k_title(name) and "4K" not in parts:
        parts.insert(0, "4K")
    elif resolution_score(name) and not any(p.endswith("p") or p == "4K" for p in parts):
        parts.insert(0, f"{resolution_score(name)}p")

    if probe and probe.get("failed"):
        if parts:
            return " · ".join(parts)
        return "—"

    if probe and (probe.get("width") or probe.get("height")):
        width = int(probe.get("width") or 0)
        height = int(probe.get("height") or 0)
        res_label = resolution_label_from_dimensions(width, height)
        if res_label and res_label not in parts:
            parts.append(res_label)
        if width and height:
            dim = f"{width}×{height}"
            if dim not in parts:
                parts.append(dim)
        codec = format_codec_label(str(probe.get("codec") or ""))
        if codec and codec not in parts:
            parts.append(codec)
        bitrate = format_probe_bitrate(probe)
        if bitrate:
            parts.append(bitrate)
        audio = format_audio_probe(probe)
        if audio:
            parts.append(audio)

    if parts:
        return " · ".join(parts)
    return "—"


def format_audio_probe(probe: dict | None) -> str:
    if not probe or probe.get("failed"):
        return ""
    codec = format_codec_label(str(probe.get("audio_codec") or ""))
    channels = int(probe.get("audio_channels") or 0)
    langs = [str(lang).upper() for lang in (probe.get("audio_languages") or []) if lang]
    parts: list[str] = []
    if codec:
        if channels == 6:
            parts.append(f"{codec} 5.1")
        elif channels == 8:
            parts.append(f"{codec} 7.1")
        elif channels == 2:
            parts.append(f"{codec} stereo")
        elif channels > 0:
            parts.append(f"{codec} {channels}ch")
        else:
            parts.append(codec)
    if langs:
        parts.append("+".join(langs[:4]))
    audio_tracks = int(probe.get("audio_tracks") or 0)
    if audio_tracks > 1 and not any("Multi" in p or "Dual" in p for p in parts):
        parts.append(f"{audio_tracks} tracce audio")
    audio_bitrate = int(probe.get("audio_bitrate") or 0)
    if audio_bitrate > 0:
        parts.append(f"audio {audio_bitrate / 1000:.0f} kbps")
    return " · ".join(parts)


def resolution_label_from_dimensions(width: int, height: int) -> str:
    if height >= 2160 or width >= 3840:
        return "4K"
    if height >= 1080 or width >= 1920:
        return "1080p"
    if height >= 720 or width >= 1280:
        return "720p"
    if height >= 480:
        return "480p"
    if height > 0:
        return f"{height}p"
    return ""


def format_codec_label(codec: str) -> str:
    mapping = {
        "hevc": "H.265",
        "h265": "H.265",
        "h264": "H.264",
        "mpeg4": "MPEG-4",
        "av1": "AV1",
    }
    return mapping.get(codec.lower(), codec.upper() if codec else "")


def format_probe_bitrate(probe: dict | None) -> str:
    if not probe:
        return ""
    bitrate = int(probe.get("bitrate") or 0)
    if bitrate <= 0:
        return ""
    return f"{bitrate / 1_000_000:.1f} Mbps"


def format_file_size(size_bytes: int, *, estimated: bool = False) -> str:
    if size_bytes <= 0:
        return ""
    if size_bytes >= 1_073_741_824:
        text = f"{size_bytes / 1_073_741_824:.2f} GB"
    elif size_bytes >= 1_048_576:
        text = f"{size_bytes / 1_048_576:.0f} MB"
    else:
        text = f"{size_bytes / 1024:.0f} KB"
    return f"~{text}" if estimated else text


def item_file_size_bytes(item: dict) -> int:
    for key in ("size", "file_size", "movie_size", "bytes"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            size = int(value)
        except (TypeError, ValueError):
            continue
        if size > 0:
            return size
    return 0


def probe_file_size_bytes(item: dict, probe: dict | None) -> tuple[int, bool]:
    estimated = bool((probe or {}).get("size_estimated"))
    size = item_file_size_bytes(item)
    if size <= 0 and probe:
        size = int(probe.get("size") or 0)
    return size, estimated and size > 0


def is_4k_probe(probe: dict | None) -> bool:
    if not probe:
        return False
    height = int(probe.get("height") or 0)
    width = int(probe.get("width") or 0)
    return height >= 2160 or width >= 3840


def build_movie_stream_url(
    host: str,
    user: str,
    password: str,
    stream_id: str | int,
    ext: str = "mp4",
) -> str:
    ext_clean = str(ext or "mp4").lstrip(".")
    return f"{host.rstrip('/')}/movie/{user}/{password}/{stream_id}.{ext_clean}"


def load_probe_cache() -> dict[str, dict]:
    data = load_json_file(STREAM_PROBE_CACHE_FILE, {})
    return data if isinstance(data, dict) else {}


def save_probe_cache(cache: dict[str, dict]) -> None:
    _save_json_file(STREAM_PROBE_CACHE_FILE, cache)


def get_cached_probe(cache: dict[str, dict], cache_key: str) -> dict | None:
    entry = cache.get(cache_key)
    if not isinstance(entry, dict):
        return None
    if entry.get("failed"):
        probed_at = float(entry.get("probed_at") or 0)
        if probed_at <= 0 or time.time() - probed_at > PROBE_CACHE_MAX_AGE:
            return None
        return entry
    probed_at = float(entry.get("probed_at") or 0)
    if probed_at <= 0 or time.time() - probed_at > PROBE_CACHE_MAX_AGE:
        return None
    return entry


def _parse_ffprobe_payload(payload: dict) -> dict | None:
    streams = payload.get("streams") or []
    video = None
    audios: list[dict] = []
    for stream in streams:
        codec_type = str(stream.get("codec_type") or "")
        if codec_type == "video" and video is None:
            video = stream
        elif codec_type == "audio":
            audios.append(stream)
    if not video:
        return None
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 and height <= 0:
        return None

    primary_audio = None
    if audios:
        primary_audio = max(
            audios,
            key=lambda stream: int(stream.get("bit_rate") or stream.get("bitrate") or 0),
        )
    languages: list[str] = []
    for audio in audios:
        tags = audio.get("tags") or {}
        lang = str(tags.get("language") or tags.get("LANGUAGE") or "").strip()
        if lang and lang not in languages:
            languages.append(lang)

    result = {
        "width": width,
        "height": height,
        "codec": str(video.get("codec_name") or ""),
        "bitrate": int(video.get("bit_rate") or video.get("bitrate") or 0),
        "audio_tracks": len(audios),
        "probed_at": time.time(),
        "size": 0,
        "size_estimated": False,
        "duration": 0.0,
    }
    fmt = payload.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)
    format_bitrate = int(fmt.get("bit_rate") or fmt.get("bitrate") or 0)
    if duration > 0:
        result["duration"] = duration
    if size > 0:
        result["size"] = size
    elif duration > 0:
        total_bps = result["bitrate"] + sum(
            int(audio.get("bit_rate") or audio.get("bitrate") or 0) for audio in audios
        )
        if total_bps <= 0:
            total_bps = format_bitrate
        if total_bps > 0:
            result["size"] = int(duration * total_bps / 8)
            result["size_estimated"] = True
    if primary_audio:
        result.update(
            {
                "audio_codec": str(primary_audio.get("codec_name") or ""),
                "audio_channels": int(primary_audio.get("channels") or 0),
                "audio_bitrate": int(
                    primary_audio.get("bit_rate") or primary_audio.get("bitrate") or 0
                ),
                "audio_languages": languages,
            }
        )
    return result


def fetch_content_length(url: str, timeout: int = 10) -> int:
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True)
        content_length = response.headers.get("Content-Length")
        if content_length:
            return int(content_length)
    except (requests.RequestException, TypeError, ValueError):
        pass
    return 0


def probe_stream_url(url: str, timeout: int = 12) -> dict | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,width,height,bit_rate,channels:stream_tags=language",
        "-show_entries",
        "format=duration,size,bit_rate",
        "-read_intervals",
        "%+#2",
        "-of",
        "json",
        url,
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        probe = _parse_ffprobe_payload(payload)
        if probe and int(probe.get("size") or 0) <= 0:
            content_length = fetch_content_length(url, timeout=timeout)
            if content_length > 0:
                probe["size"] = content_length
                probe["size_estimated"] = False
        return probe
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        return None


def _probe_cache_key(item: dict) -> str:
    stream_id = str(item.get("stream_id") or "")
    ext = str(item.get("container_extension") or "mp4")
    return f"movie:{stream_id}:{ext}"


def _decorate_probe(probe: dict, *, stream_id: str, from_cache: bool) -> dict:
    return {
        **probe,
        "stream_id": stream_id,
        "from_cache": from_cache,
    }


def probe_movie_item(
    host: str,
    user: str,
    password: str,
    item: dict,
    cache: dict[str, dict] | None = None,
) -> dict | None:
    stream_id = str(item.get("stream_id") or "")
    if not stream_id:
        return None
    cache_key = _probe_cache_key(item)
    if cache is None:
        cache = load_probe_cache()
    cached = get_cached_probe(cache, cache_key)
    if cached:
        return _decorate_probe(cached, stream_id=stream_id, from_cache=True)

    url = build_movie_stream_url(
        host,
        user,
        password,
        stream_id,
        str(item.get("container_extension") or "mp4"),
    )
    probe = probe_stream_url(url)
    if probe:
        cache[cache_key] = probe
        return _decorate_probe(probe, stream_id=stream_id, from_cache=False)

    failed = {"failed": True, "probed_at": time.time(), "stream_id": stream_id}
    cache[cache_key] = failed
    return _decorate_probe(failed, stream_id=stream_id, from_cache=False)


def probe_movie_versions(
    versions: list[dict],
    host: str,
    user: str,
    password: str,
    *,
    progress_callback=None,
) -> tuple[dict[str, dict], dict[str, int]]:
    cache = load_probe_cache()
    probes: dict[str, dict] = {}
    stats = {"total": 0, "fresh": 0, "cached": 0, "failed": 0}
    changed = False
    pending = [item for item in versions if str(item.get("stream_id") or "")]
    stats["total"] = len(pending)

    for index, item in enumerate(pending, start=1):
        stream_id = str(item.get("stream_id") or "")
        if progress_callback:
            progress_callback(index, len(pending), stream_id)

        probe = probe_movie_item(host, user, password, item, cache)
        if not probe:
            stats["failed"] += 1
            continue

        probes[stream_id] = probe
        if probe.get("failed"):
            stats["failed"] += 1
            changed = True
        elif probe.get("from_cache"):
            stats["cached"] += 1
        else:
            stats["fresh"] += 1
            changed = True

    if changed:
        save_probe_cache(cache)
    return probes, stats


def clear_probe_cache_for_items(items: list[dict]) -> None:
    cache = load_probe_cache()
    changed = False
    for item in items:
        cache_key = _probe_cache_key(item)
        if cache.pop(cache_key, None) is not None:
            changed = True
    if changed:
        save_probe_cache(cache)


def catalog_category_name(item: dict, category_map: dict[str, str]) -> str:
    category_id = str(item.get("category_id") or "")
    return category_map.get(category_id, "")
    if len(group) <= 1:
        return False
    ranks = [quality_rank(str(item.get(name_key, ""))) for item in group]
    return len(set(ranks)) == 1 and ranks[0][0] == 0


def _path_parts(path: str) -> list[str]:
    return [part for part in path.replace("\\", "/").split("/") if part]


def series_folder_from_strm_path(strm_path: str) -> str | None:
    parts = _path_parts(strm_path)
    if not parts or not parts[-1].lower().endswith(".strm"):
        return None
    if len(parts) < 2:
        return None
    parent = parts[-2]
    if SEASON_DIR_RE.match(parent):
        return parts[-3] if len(parts) >= 3 else None
    return parent


def movie_folder_from_strm_path(strm_path: str) -> str | None:
    parts = _path_parts(strm_path)
    if not parts or not parts[-1].lower().endswith(".strm"):
        return None
    return parts[-2] if len(parts) >= 2 else None


def find_strm_folder_match(root: str, title: str) -> str | None:
    if not root or not os.path.isdir(root):
        return None
    target = normalize_title(title)
    if not target:
        return None

    exact: list[str] = []
    partial: list[str] = []
    for name in os.listdir(root):
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        norm = normalize_title(name)
        if norm == target:
            exact.append(name)
        elif target in norm or norm in target:
            partial.append(name)

    if exact:
        return sorted(exact, key=len)[0]
    if partial:
        return min(partial, key=lambda name: abs(len(normalize_title(name)) - len(target)))
    return None


def resolve_series_folder_name(series_name: str, strm_path: str | None = None) -> str:
    if strm_path:
        from_strm = series_folder_from_strm_path(strm_path)
        if from_strm:
            return sanitize_filename(from_strm)
    match = find_strm_folder_match(STRM_SERIES_PATH, series_name)
    if match:
        return sanitize_filename(match)
    return sanitize_filename(series_name)


def resolve_movie_folder_name(movie_name: str, strm_path: str | None = None) -> str:
    if strm_path:
        from_strm = movie_folder_from_strm_path(strm_path)
        if from_strm:
            return sanitize_filename(from_strm)
    match = find_strm_folder_match(STRM_MOVIES_PATH, movie_name)
    if match:
        return sanitize_filename(match)
    return sanitize_filename(movie_name)


def resolve_season_folder_name(
    series_folder: str,
    season: int,
    strm_path: str | None = None,
) -> str:
    if strm_path:
        parts = _path_parts(strm_path)
        if len(parts) >= 2:
            parent = parts[-2]
            match = SEASON_DIR_RE.match(parent)
            if match and int(match.group(1)) == int(season):
                return parent

    series_path = os.path.join(STRM_SERIES_PATH, series_folder)
    if os.path.isdir(series_path):
        for name in os.listdir(series_path):
            match = SEASON_DIR_RE.match(name)
            if match and int(match.group(1)) == int(season):
                return name
    return f"Season {int(season):02d}"


def owner_ids() -> tuple[int, int]:
    return (
        int(os.environ.get("PUID", "1000")),
        int(os.environ.get("PGID", "1000")),
    )


def _is_under_download_roots(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep) for root in DOWNLOAD_ROOTS)


def _apply_path_permissions(path: str, uid: int, gid: int) -> None:
    try:
        if os.geteuid() == 0:
            os.chown(path, uid, gid)
        if os.path.isdir(path):
            os.chmod(path, DIR_MODE)
        else:
            os.chmod(path, FILE_MODE)
    except OSError:
        pass


def finalize_download_path(path: str, fix_children: bool = False) -> None:
    """Set owner (PUID/PGID) and modes on a file/dir and its parents under /download."""
    if not path or not os.path.exists(path):
        return
    uid, gid = owner_ids()
    chain: list[str] = []
    current = os.path.realpath(path)
    chain.append(current)
    parent = os.path.dirname(current)
    while parent and _is_under_download_roots(parent):
        chain.append(parent)
        if parent in DOWNLOAD_ROOTS:
            break
        parent = os.path.dirname(parent)
    for target in chain:
        _apply_path_permissions(target, uid, gid)
    if fix_children and os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                _apply_path_permissions(os.path.join(root, name), uid, gid)


def ensure_download_tree_permissions() -> None:
    """On startup (as root), fix ownership of existing downloads and .data."""
    uid, gid = owner_ids()
    roots = list(DOWNLOAD_ROOTS) + [DATA_DIR]
    for base in roots:
        os.makedirs(base, mode=DIR_MODE, exist_ok=True)
        for root, dirs, files in os.walk(base):
            for name in [root, *dirs, *files]:
                full = name if name == root else os.path.join(root, name)
                _apply_path_permissions(full, uid, gid)


def set_owner(path: str, recursive: bool = True) -> None:
    finalize_download_path(path, fix_children=recursive)


def set_dir_mode(path: str, recursive: bool = True) -> None:
    if not os.path.isdir(path):
        return
    uid, gid = owner_ids()
    try:
        if recursive:
            for root, dirs, files in os.walk(path):
                _apply_path_permissions(root, uid, gid)
                for name in dirs + files:
                    _apply_path_permissions(os.path.join(root, name), uid, gid)
        else:
            _apply_path_permissions(path, uid, gid)
    except OSError:
        pass


def prepare_output_dir(path: str) -> None:
    os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    finalize_download_path(path)


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    set_owner(DATA_DIR, recursive=True)


def _save_json_file(path: str, data: object) -> None:
    _ensure_data_dir()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)
    set_owner(path, recursive=False)


def load_json_file(path: str, default: object) -> object:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, type(default)):
            return data
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return default


def load_credentials() -> dict:
    data = load_json_file(CREDENTIALS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_credentials(host: str, user: str, password: str) -> None:
    _save_json_file(
        CREDENTIALS_FILE,
        {"host": host, "user": user, "password": password},
    )


def clear_credentials() -> None:
    try:
        os.remove(CREDENTIALS_FILE)
    except OSError:
        pass


def default_auto_download_config() -> dict:
    return {
        "enabled": False,
        "emby_enabled": bool(os.environ.get("EMBY_URL", "")),
        "emby_url": os.environ.get("EMBY_URL", ""),
        "emby_api_key": os.environ.get("EMBY_API_KEY", ""),
        "emby_username": os.environ.get("EMBY_USERNAME", ""),
        "jellyfin_enabled": bool(os.environ.get("JELLYFIN_URL", "")),
        "jellyfin_url": os.environ.get("JELLYFIN_URL", ""),
        "jellyfin_api_key": os.environ.get("JELLYFIN_API_KEY", ""),
        "jellyfin_username": os.environ.get("JELLYFIN_USERNAME", ""),
        "series_dest": DEFAULT_SERIES_DEST,
        "cooldown_seconds": int(os.environ.get("AUTO_COOLDOWN_SECONDS", "90")),
        "poll_interval_seconds": int(os.environ.get("AUTO_POLL_INTERVAL_SECONDS", "20")),
        "prompt_delete_completed": True,
        "allow_4k": False,
    }


def _migrate_auto_download_config(merged: dict, raw: dict | None = None) -> dict:
    """Migrate legacy single-server media_server config to dual Emby + Jellyfin."""
    raw = raw or {}
    if "emby_enabled" not in raw and "jellyfin_enabled" not in raw and merged.get("media_server"):
        legacy_url = str(merged.get("emby_url", "")).strip()
        legacy_key = str(merged.get("emby_api_key", "")).strip()
        legacy_user = str(merged.get("emby_username", "")).strip()
        has_legacy = bool(legacy_url and legacy_key and legacy_user)
        if merged.get("media_server") == "jellyfin" and has_legacy:
            merged["jellyfin_enabled"] = True
            merged["jellyfin_url"] = legacy_url
            merged["jellyfin_api_key"] = legacy_key
            merged["jellyfin_username"] = legacy_user
            merged["emby_enabled"] = False
        elif has_legacy:
            merged["emby_enabled"] = True
            merged.setdefault("jellyfin_enabled", False)
    merged.setdefault("emby_enabled", False)
    merged.setdefault("jellyfin_enabled", False)
    merged.setdefault("jellyfin_url", "")
    merged.setdefault("jellyfin_api_key", "")
    merged.setdefault("jellyfin_username", "")
    merged.setdefault("allow_4k", False)
    merged["emby_enabled"] = bool(merged.get("emby_enabled"))
    merged["jellyfin_enabled"] = bool(merged.get("jellyfin_enabled"))
    merged["allow_4k"] = bool(merged.get("allow_4k"))
    return merged


def load_auto_download_config() -> dict:
    defaults = default_auto_download_config()
    data = load_json_file(AUTO_DOWNLOAD_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    merged = {**defaults, **data}
    if merged["series_dest"] not in DOWNLOAD_CONFIG.values():
        merged["series_dest"] = DEFAULT_SERIES_DEST
    return _migrate_auto_download_config(merged, data)


def save_auto_download_config(config: dict) -> None:
    _save_json_file(AUTO_DOWNLOAD_FILE, config)


def default_watcher_status() -> dict:
    return {
        "running": False,
        "enabled": False,
        "playback_active": False,
        "download_paused": False,
        "downloading": False,
        "current_playing": "",
        "current_download": "",
        "download_progress": 0.0,
        "download_progress_text": "",
        "queue_size": 0,
        "cooldown_remaining": 0,
        "cooldown_until": 0.0,
        "last_action": "",
        "last_error": "",
        "log": [],
    }


def load_watcher_status() -> dict:
    data = load_json_file(WATCHER_STATUS_FILE, default_watcher_status())
    if not isinstance(data, dict):
        return default_watcher_status()
    merged = {**default_watcher_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


def save_watcher_status(data: dict) -> None:
    _save_json_file(WATCHER_STATUS_FILE, data)


def load_playback_history() -> dict:
    data = load_json_file(PLAYBACK_HISTORY_FILE, {"items": []})
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items", [])
    return {"items": items if isinstance(items, list) else []}


def save_playback_history(data: dict) -> None:
    _save_json_file(PLAYBACK_HISTORY_FILE, data)


def append_playback_history(entry: dict) -> None:
    data = load_playback_history()
    items = data.get("items", [])
    if items and items[0].get("key") == entry.get("key"):
        return
    items.insert(0, entry)
    save_playback_history({"items": items[:MAX_PLAYBACK_HISTORY]})


def load_download_history() -> dict:
    data = load_json_file(DOWNLOAD_HISTORY_FILE, {"items": []})
    if not isinstance(data, dict):
        return {"items": []}
    items = data.get("items", [])
    return {"items": items if isinstance(items, list) else []}


def save_download_history(data: dict) -> None:
    _save_json_file(DOWNLOAD_HISTORY_FILE, data)


def append_download_history(entry: dict) -> None:
    data = load_download_history()
    items = data.get("items", [])
    items.insert(0, entry)
    save_download_history({"items": items[:MAX_DOWNLOAD_HISTORY]})


def load_hidden_categories() -> dict[str, list[str]]:
    data = load_json_file(HIDDEN_CATEGORIES_FILE, {"vod": [], "series": []})
    if not isinstance(data, dict):
        return {"vod": [], "series": []}
    return {
        "vod": [str(x) for x in data.get("vod", [])],
        "series": [str(x) for x in data.get("series", [])],
    }


def save_hidden_categories(data: dict[str, list[str]]) -> None:
    _save_json_file(HIDDEN_CATEGORIES_FILE, data)


def hidden_category_ids(kind: str) -> set[str]:
    return set(load_hidden_categories().get(kind, []))


def exclude_hidden_items(items: list, kind: str) -> list:
    hidden = hidden_category_ids(kind)
    if not hidden:
        return items
    return [i for i in items if str(i.get("category_id", "")) not in hidden]


def request_xtream_api(
    host: str,
    params: dict,
    timeout: int = 60,
    retries: int = 5,
) -> object | None:
    url = f"{host.rstrip('/')}/player_api.php"
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": "Xtream-VOD-Downloader/1.0",
        "Connection": "close",
    }
    last_exc = None

    for attempt in range(retries):
        try:
            response = requests.get(
                url,
                params=params,
                headers=headers,
                timeout=(10, timeout),
            )
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))

    if last_exc is not None:
        raise RuntimeError(f"Errore API Xtream: {last_exc}") from last_exc
    return None


def fetch_series_catalog(host: str, user: str, password: str) -> list:
    params = {"username": user, "password": password, "action": "get_series"}
    data = request_xtream_api(host, params, timeout=180)
    if not isinstance(data, list):
        raise RuntimeError("catalogo serie non valido")
    return exclude_hidden_items(data, "series")


def get_series_info(host: str, user: str, password: str, series_id: str | int) -> dict | None:
    params = {
        "username": user,
        "password": password,
        "action": "get_series_info",
        "series_id": series_id,
    }
    data = request_xtream_api(host, params, timeout=90)
    return data if isinstance(data, dict) else None


def find_xtream_series(
    host: str,
    user: str,
    password: str,
    series_name: str,
    *,
    allow_4k: bool = False,
) -> dict | None:
    target = catalog_title_key(series_name)
    if not target:
        return None

    catalog = fetch_series_catalog(host, user, password)
    exact = [s for s in catalog if catalog_title_key(s.get("name", "")) == target]
    if exact:
        return pick_best_catalog_item(exact, allow_4k=allow_4k)

    partial = [
        s
        for s in catalog
        if target in catalog_title_key(s.get("name", ""))
        or catalog_title_key(s.get("name", "")) in target
    ]
    if not partial:
        return None

    groups: dict[str, list[dict]] = {}
    for item in partial:
        key = catalog_title_key(item.get("name", ""))
        groups.setdefault(key, []).append(item)
    best_group_key = min(
        groups.keys(),
        key=lambda key: abs(len(key) - len(target)),
    )
    return pick_best_catalog_item(groups[best_group_key], allow_4k=allow_4k)


def resolve_episode_from_strm_path(strm_path: str, xtream_host: str) -> dict | None:
    if not strm_path.lower().endswith(".strm"):
        return None
    url = read_strm_url(strm_path)
    if not url:
        return None
    if url_hostname(url) != url_hostname(xtream_host):
        return None
    match = XTREAM_SERIES_EPISODE_URL_RE.search(url.strip())
    if not match:
        return None
    return {
        "url": url.strip(),
        "ext": match.group(2),
        "episode_id": match.group(1),
    }


def live_cooldown_remaining(status: dict) -> int:
    cooldown_until = float(status.get("cooldown_until") or 0)
    if cooldown_until > 0:
        return max(0, int(cooldown_until - time.time()))
    return max(0, int(status.get("cooldown_remaining") or 0))


def find_subsequent_xtream_episodes(
    host: str,
    user: str,
    password: str,
    series_name: str,
    season: int,
    episode: int,
    *,
    allow_4k: bool = False,
) -> list[dict]:
    series = find_xtream_series(
        host, user, password, series_name, allow_4k=allow_4k
    )
    if not series:
        return []

    info = get_series_info(host, user, password, series["series_id"])
    if not info or "episodes" not in info:
        return []

    results = []
    for season_key, eps in info["episodes"].items():
        try:
            season_i = int(season_key)
        except (TypeError, ValueError):
            continue
        for ep in eps:
            ep_num = int(ep.get("episode_num", -1))
            if ep_num < 0:
                continue
            if (season_i, ep_num) <= (season, episode):
                continue
            ext = ep.get("container_extension", "mp4")
            url = f"{host.rstrip('/')}/series/{user}/{password}/{ep['id']}.{ext}"
            results.append(
                {
                    "season": season_i,
                    "episode": ep_num,
                    "url": url,
                    "ext": ext,
                }
            )
    results.sort(key=lambda item: (item["season"], item["episode"]))
    return results


def find_xtream_episode(
    host: str,
    user: str,
    password: str,
    series_name: str,
    season: int,
    episode: int,
    *,
    allow_4k: bool = False,
) -> dict | None:
    series = find_xtream_series(
        host, user, password, series_name, allow_4k=allow_4k
    )
    if not series:
        return None

    info = get_series_info(host, user, password, series["series_id"])
    if not info or "episodes" not in info:
        return None

    season_eps = info["episodes"].get(str(season), [])
    for ep in season_eps:
        if int(ep.get("episode_num", -1)) == episode:
            ext = ep.get("container_extension", "mp4")
            url = (
                f"{host.rstrip('/')}/series/{user}/{password}/"
                f"{ep['id']}.{ext}"
            )
            return {
                "series": series,
                "episode": ep,
                "url": url,
                "ext": ext,
            }
    return None


def build_episode_output(
    series_name: str,
    season: int,
    episode: int,
    ext: str,
    dest_root: str,
    *,
    strm_path: str | None = None,
) -> tuple[str, str]:
    safe_series = resolve_series_folder_name(series_name, strm_path)
    season_folder = resolve_season_folder_name(safe_series, season, strm_path)
    path = os.path.join(dest_root, safe_series, season_folder)
    ext_clean = ext.lstrip(".")
    filename = (
        f"{safe_series} - S{int(season):02d}E{int(episode):02d}"
        f"{LOCAL_DOWNLOAD_MARKER}.{ext_clean}"
    )
    return path, os.path.join(path, filename)


def build_movie_output(
    movie_name: str,
    ext: str,
    dest_root: str,
    *,
    strm_path: str | None = None,
) -> tuple[str, str]:
    safe_name = resolve_movie_folder_name(movie_name, strm_path)
    path = os.path.join(dest_root, safe_name)
    ext_clean = ext.lstrip(".")
    return path, os.path.join(path, f"{safe_name}{LOCAL_DOWNLOAD_MARKER}.{ext_clean}")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def describe_existing_file(path: str) -> dict:
    stat = os.stat(path)
    return {
        "path": path,
        "size": human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M"),
    }


class DownloadCancelled(Exception):
    pass


def run_ytdlp(
    url: str,
    output_path: str,
    progress_callback=None,
    label: str = "",
    should_cancel=None,
    resume: bool = False,
    history_entry: dict | None = None,
) -> bool:
    cmd = [
        "yt-dlp",
        url,
        "-o",
        output_path,
        "--fixup",
        "detect_or_warn",
        "--newline",
        "--progress",
    ]
    if resume or (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
        cmd.append("--continue")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    last_pct = 0.0
    output_lines = []
    prefix = f"{label} — " if label else ""

    assert proc.stdout is not None
    for line in proc.stdout:
        if should_cancel and should_cancel():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise DownloadCancelled("download interrotto per riproduzione attiva")

        line = line.rstrip()
        if line:
            output_lines.append(line)
        match = DOWNLOAD_PROGRESS_RE.search(line)
        if match:
            last_pct = float(match.group(1))
            if progress_callback is not None:
                progress_callback(min(last_pct / 100.0, 1.0), f"{prefix}{last_pct:.1f}%")

    try:
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("\n".join(output_lines[-15:]) or "Download fallito")
    finally:
        if output_path and os.path.exists(output_path):
            finalize_download_path(output_path)

    if progress_callback is not None:
        progress_callback(1.0, f"{prefix}100% — Completato")
    if history_entry is not None:
        try:
            append_download_history(
                {
                    **history_entry,
                    "path": output_path,
                    "downloaded_at": datetime.now().strftime("%d/%m/%Y %H:%M"),
                }
            )
        except OSError:
            pass
    return True

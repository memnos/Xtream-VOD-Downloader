import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

import requests

DOWNLOAD_MOVIES_PATH = "/download/movies"
DOWNLOAD_TV_PATH = "/download/tv"
STRM_MOVIES_PATH = os.environ.get("STRM_MOVIES_PATH", "/strm/movies")
STRM_SERIES_PATH = os.environ.get("STRM_SERIES_PATH", "/strm/series")

SEASON_DIR_RE = re.compile(r"^Season\s*0*(\d+)\s*$", re.IGNORECASE)
EPISODE_TAG_RE = re.compile(r"S(\d{1,2})E(\d{1,2})", re.IGNORECASE)

DOWNLOAD_CONFIG = {
    "movies": DOWNLOAD_MOVIES_PATH,
    "tv": DOWNLOAD_TV_PATH,
}

SERIES_DOWNLOAD_PATHS = (DOWNLOAD_TV_PATH,)
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
STRM_SYNC_FILE = os.environ.get(
    "STRM_SYNC_FILE", os.path.join(DATA_DIR, "strm_sync.json")
)
STRM_SYNC_STATUS_FILE = os.environ.get(
    "STRM_SYNC_STATUS_FILE", os.path.join(DATA_DIR, "strm_sync_status.json")
)
STRM_OUTPUT_MOVIES_PATH = os.environ.get("STRM_OUTPUT_MOVIES_PATH", STRM_MOVIES_PATH)
STRM_OUTPUT_SERIES_PATH = os.environ.get("STRM_OUTPUT_SERIES_PATH", STRM_SERIES_PATH)
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
STRM_DURATION_ERRORS_FILE = os.environ.get(
    "STRM_DURATION_ERRORS_FILE",
    os.path.join(DATA_DIR, "strm_duration_errors.json"),
)
STRM_DURATION_AUDIT_STATUS_FILE = os.environ.get(
    "STRM_DURATION_AUDIT_STATUS_FILE",
    os.path.join(DATA_DIR, "strm_duration_audit_status.json"),
)
DISCARDED_MOVIE_STREAMS_FILE = os.environ.get(
    "DISCARDED_MOVIE_STREAMS_FILE",
    os.path.join(DATA_DIR, "discarded_movie_streams.json"),
)
UI_PREFS_FILE = os.environ.get(
    "UI_PREFS_FILE", os.path.join(DATA_DIR, "ui_prefs.json")
)
PROBE_CACHE_MAX_AGE = int(os.environ.get("PROBE_CACHE_MAX_AGE", str(7 * 86400)))
MAX_DOWNLOAD_HISTORY = 20
DIR_MODE = 0o777
FILE_MODE = 0o664
DOWNLOAD_ROOTS = (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
)
STRM_OUTPUT_ROOTS = (
    STRM_OUTPUT_MOVIES_PATH,
    STRM_OUTPUT_SERIES_PATH,
)
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}

# Suffix in downloaded filenames — easy to spot local files vs .strm in the media library UI.
LOCAL_DOWNLOAD_MARKER = " [LOCAL]"
# Sidecar next to a movie [LOCAL] download: preserves Xtream URL so .strm can be restored.
MOVIE_STRM_URL_SIDECAR = ".xtream-strm-url"


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
    if not item_path:
        return False

    path_lower = item_path.lower()
    if path_lower.endswith(".strm"):
        strm_url = read_strm_url(item_path)
        if strm_url:
            # Proxy .strm counts as remote playback (pause background downloads).
            if "/p/movie/" in strm_url.lower() or "/p/episode/" in strm_url.lower():
                return True
            if xtream_domain and url_hostname(strm_url) == xtream_domain:
                return True
            return False
        return True

    ext = os.path.splitext(item_path)[1].lower()
    if ext in VIDEO_EXTENSIONS:
        return False

    return False


def xtream_playback_blocks_extra_streams() -> bool:
    """True while a .strm is playing — extra Xtream connections stall GuamaFlix."""
    try:
        status = load_watcher_status()
    except Exception:
        return False
    if not status.get("playback_active"):
        return False
    label = str(status.get("current_playing") or "").lower()
    return "(strm)" in label


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "", name).strip()


def format_elapsed_seconds(seconds: float) -> str:
    """Human-readable duration for sync summaries (e.g. 2m 15s, 1h 5m)."""
    total = max(0, int(round(float(seconds or 0))))
    if total < 60:
        return f"{total}s"
    mins, secs = divmod(total, 60)
    if mins < 60:
        return f"{mins}m {secs}s" if secs else f"{mins}m"
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m" if mins else f"{hours}h"


_LOG_TIME_RE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")


def _log_line_seconds(line: str) -> int | None:
    match = _LOG_TIME_RE.match(str(line).strip())
    if not match:
        return None
    hours, mins, secs = (int(part) for part in match.groups())
    return hours * 3600 + mins * 60 + secs


def estimate_sync_timing_from_log(log: list) -> dict[str, float]:
    """Estimate per-phase durations from sync log timestamps (legacy runs)."""
    markers = {
        "movies_start": "Loading movie catalog...",
        "movies_end": "Movies done:",
        "series_start": "Loading series catalog...",
        "series_end": "Series done:",
    }
    end_markers = ("--- Sync summary ---", "Sync completed", "Cleanup:")
    times: dict[str, int] = {}
    sync_end: int | None = None

    for line in log:
        if not isinstance(line, str):
            continue
        ts = _log_line_seconds(line)
        if ts is None:
            continue
        for key, marker in markers.items():
            if marker in line:
                times[key] = ts
        for marker in end_markers:
            if marker in line:
                sync_end = ts

    result = {
        "movies_elapsed_sec": 0.0,
        "series_elapsed_sec": 0.0,
        "total_elapsed_sec": 0.0,
    }
    if "movies_start" in times and "movies_end" in times:
        result["movies_elapsed_sec"] = float(max(0, times["movies_end"] - times["movies_start"]))
    if "series_start" in times and "series_end" in times:
        result["series_elapsed_sec"] = float(max(0, times["series_end"] - times["series_start"]))
    if sync_end is not None and "movies_start" in times:
        result["total_elapsed_sec"] = float(max(0, sync_end - times["movies_start"]))
    elif result["movies_elapsed_sec"] or result["series_elapsed_sec"]:
        result["total_elapsed_sec"] = result["movies_elapsed_sec"] + result["series_elapsed_sec"]
    return result


TMDB_ID_TAG_RE = re.compile(r"\s*\[tmdbid-\d+\]\s*", re.IGNORECASE)
TITLE_YEAR_SUFFIX_RE = re.compile(r"\((\d{4})\)\s*$")


def strip_tmdb_id_tag(name: str) -> str:
    return TMDB_ID_TAG_RE.sub(" ", name or "").strip()


def normalize_title(name: str) -> str:
    text = name.lower().strip()
    text = re.sub(r"^\|[^|]+\|", "", text)
    # Strip Emby/Jellyfin TMDB folder tags so plain titles match
    # "Title (2021) [tmdbid-123]" folders.
    text = strip_tmdb_id_tag(text)
    text = re.sub(r"\s*\(\d{4}\)\s*$", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_title_year(name: str) -> int | None:
    text = strip_tmdb_id_tag(name.strip())
    match = TITLE_YEAR_SUFFIX_RE.search(text)
    return int(match.group(1)) if match else None


def folder_has_tmdb_id(name: str) -> bool:
    return bool(TMDB_ID_TAG_RE.search(name or ""))


def titles_match_loosely(left: str, right: str) -> bool:
    """Match titles that differ only by a leading article (e.g. Matrix vs The Matrix)."""
    left_tokens = normalize_title(left).split()
    right_tokens = normalize_title(right).split()
    if not left_tokens or not right_tokens:
        return False
    if left_tokens == right_tokens:
        return True
    if left_tokens[0] == "the" and left_tokens[1:] == right_tokens:
        return True
    if right_tokens[0] == "the" and right_tokens[1:] == left_tokens:
        return True
    return False


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
    text = strip_tmdb_id_tag(text)
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
    skip_item=None,
) -> dict | None:
    if not items:
        return None
    candidates = items
    if not allow_4k:
        candidates = [
            item for item in items if not _item_is_4k(item, probes, name_key=name_key)
        ]
    if skip_item is not None:
        candidates = [item for item in candidates if not skip_item(item)]
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
    skip_item=None,
) -> tuple[list[dict], int]:
    groups = group_catalog_versions(items, name_key=name_key)

    result: list[dict] = []
    for group in groups.values():
        best = pick_best_catalog_item(
            group,
            allow_4k=allow_4k,
            name_key=name_key,
            probes=probes,
            skip_item=skip_item,
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


def normalize_episodes_map(episodes: object) -> dict[str, list]:
    """Normalize Xtream get_series_info episodes to {season: [ep, ...]}."""
    if isinstance(episodes, dict):
        return episodes
    if not isinstance(episodes, list):
        return {}
    grouped: dict[str, list] = {}
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        season = ep.get("season")
        if season is None:
            info = ep.get("info") or {}
            season = info.get("season") or info.get("season_number")
        if season is None:
            continue
        grouped.setdefault(str(season), []).append(ep)
    return grouped


def build_episode_stream_url(
    host: str,
    user: str,
    password: str,
    episode_id: str | int,
    ext: str = "mp4",
) -> str:
    ext_clean = str(ext or "mp4").lstrip(".")
    return (
        f"{host.rstrip('/')}/series/{user}/{password}/"
        f"{episode_id}.{ext_clean}"
    )


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


def probe_stream_duration(url: str, timeout: int = 45) -> float | None:
    """Probe remote stream duration in seconds (no short read_intervals).

    Returns None when duration cannot be determined.
    """
    info = probe_stream_media_info(url, timeout=timeout)
    if not info:
        return None
    duration = float(info.get("duration") or 0)
    return duration if duration > 0 else None


def probe_stream_media_info(url: str, timeout: int = 45) -> dict | None:
    """Full remote probe: duration, container, and stream list for Jellyfin import.

    Avoids short -read_intervals so duration is available for HTTP VOD.
    """
    if not url:
        return None
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        (
            "stream=index,codec_type,codec_name,width,height,bit_rate,channels,"
            "sample_rate,avg_frame_rate,profile,level,pix_fmt:"
            "stream_tags=language,title:"
            "format=duration,size,bit_rate,format_name"
        ),
        "-probesize",
        "5000000",
        "-analyzeduration",
        "10000000",
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
    except (subprocess.TimeoutExpired, json.JSONDecodeError, ValueError, OSError):
        return None

    fmt = payload.get("format") or {}
    duration = float(fmt.get("duration") or 0)
    size = int(fmt.get("size") or 0)
    format_bitrate = int(fmt.get("bit_rate") or fmt.get("bitrate") or 0)
    container = str(fmt.get("format_name") or "").split(",")[0].strip()

    streams_out: list[dict] = []
    width = 0
    height = 0
    video_codec = ""
    video_bitrate = 0
    for stream in payload.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        codec_type = str(stream.get("codec_type") or "").lower()
        if codec_type == "video":
            stream_type = "Video"
        elif codec_type == "audio":
            stream_type = "Audio"
        elif codec_type == "subtitle":
            stream_type = "Subtitle"
        else:
            continue
        tags = stream.get("tags") or {}
        entry = {
            "index": int(stream.get("index") or len(streams_out)),
            "type": stream_type,
            "codec": str(stream.get("codec_name") or ""),
            "profile": str(stream.get("profile") or ""),
            "bit_rate": int(stream.get("bit_rate") or stream.get("bitrate") or 0),
            "width": int(stream.get("width") or 0),
            "height": int(stream.get("height") or 0),
            "channels": int(stream.get("channels") or 0),
            "sample_rate": int(stream.get("sample_rate") or 0),
            "average_frame_rate": str(stream.get("avg_frame_rate") or ""),
            "pixel_format": str(stream.get("pix_fmt") or ""),
            "language": str(tags.get("language") or tags.get("LANGUAGE") or "").strip(),
            "title": str(tags.get("title") or tags.get("TITLE") or "").strip(),
            "is_default": False,
            "is_external": False,
        }
        streams_out.append(entry)
        if stream_type == "Video" and width <= 0:
            width = entry["width"]
            height = entry["height"]
            video_codec = entry["codec"]
            video_bitrate = entry["bit_rate"]

    if duration <= 0 and width <= 0 and not streams_out:
        return None

    if size <= 0:
        content_length = fetch_content_length(url, timeout=min(timeout, 15))
        if content_length > 0:
            size = content_length

    return {
        "duration": duration,
        "size": size,
        "bitrate": video_bitrate or format_bitrate,
        "container": container,
        "width": width,
        "height": height,
        "codec": video_codec,
        "streams": streams_out,
        "probed_at": time.time(),
    }


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


def series_folder_from_media_path(path: str) -> str | None:
    parts = _path_parts(path)
    if len(parts) < 2:
        return None
    parent = parts[-2]
    if SEASON_DIR_RE.match(parent):
        return parts[-3] if len(parts) >= 3 else None
    return parent


def parse_episode_numbers_from_path(path: str) -> tuple[int, int] | None:
    """Parse season/episode from SxxExx in the filename, else from Season folder + filename."""
    parts = _path_parts(path)
    if not parts:
        return None
    match = EPISODE_TAG_RE.search(parts[-1])
    if match:
        return int(match.group(1)), int(match.group(2))
    if len(parts) >= 2:
        season_match = SEASON_DIR_RE.match(parts[-2])
        if season_match:
            ep_only = re.search(r"(?:^|[\s._-])E(\d{1,2})(?:\D|$)", parts[-1], re.IGNORECASE)
            if ep_only:
                return int(season_match.group(1)), int(ep_only.group(1))
    return None


def _local_episode_file(directory: str, season: int, episode: int) -> str | None:
    """Return a local video for season/episode, preferring [LOCAL] downloads.

    Accepts any video whose name contains a matching SxxExx tag (scene releases,
    renames, etc.), not only files marked with LOCAL_DOWNLOAD_MARKER.
    """
    if not directory or not os.path.isdir(directory):
        return None
    season_i = int(season)
    episode_i = int(episode)
    preferred: str | None = None
    fallback: str | None = None
    for filename in os.listdir(directory):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            continue
        match = EPISODE_TAG_RE.search(filename)
        if not match:
            continue
        if int(match.group(1)) != season_i or int(match.group(2)) != episode_i:
            continue
        full = os.path.join(directory, filename)
        if not (os.path.isfile(full) and os.path.getsize(full) > 0):
            continue
        if LOCAL_DOWNLOAD_MARKER in filename:
            preferred = full
            break
        if fallback is None:
            fallback = full
    return preferred or fallback


def _local_movie_file(directory: str, movie_folder: str) -> str | None:
    """Return a local movie video in directory, preferring [LOCAL] downloads."""
    if not directory or not os.path.isdir(directory):
        return None
    preferred: str | None = None
    fallback: str | None = None
    prefix = f"{movie_folder}{LOCAL_DOWNLOAD_MARKER}."
    for filename in os.listdir(directory):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in VIDEO_EXTENSIONS:
            continue
        full = os.path.join(directory, filename)
        if not (os.path.isfile(full) and os.path.getsize(full) > 0):
            continue
        if filename.startswith(prefix) or LOCAL_DOWNLOAD_MARKER in filename:
            preferred = full
            break
        if fallback is None:
            fallback = full
    return preferred or fallback


def _candidate_series_folders(series_folder: str, root: str) -> list[str]:
    folders: list[str] = []
    if series_folder:
        sanitized = sanitize_filename(series_folder)
        folders.append(sanitized)
    match = find_strm_folder_match(root, series_folder)
    if match and match not in folders:
        folders.append(match)
    return folders


def find_local_files_for_strm(strm_path: str) -> list[str]:
    """Return local download files that supersede a .strm entry."""
    strm_path = os.path.realpath(strm_path)
    if not strm_path.lower().endswith(".strm"):
        return []

    found: list[str] = []
    episode_numbers = parse_episode_numbers_from_path(strm_path)
    if episode_numbers is not None:
        season, episode = episode_numbers
        series_folder = series_folder_from_media_path(strm_path) or ""
        search_dirs: list[str] = [os.path.dirname(strm_path)]
        for dl_root in SERIES_DOWNLOAD_PATHS:
            for folder_name in _candidate_series_folders(series_folder, dl_root):
                season_folder = resolve_season_folder_name(
                    folder_name, season, strm_path=strm_path
                )
                search_dirs.append(os.path.join(dl_root, folder_name, season_folder))
        seen_dirs: set[str] = set()
        for directory in search_dirs:
            real_dir = os.path.realpath(directory)
            if real_dir in seen_dirs:
                continue
            seen_dirs.add(real_dir)
            hit = _local_episode_file(real_dir, season, episode)
            if hit:
                found.append(hit)
        return list(dict.fromkeys(found))

    movie_folder = movie_folder_from_strm_path(strm_path) or ""
    search_dirs = [os.path.dirname(strm_path)]
    if movie_folder:
        exact_dl = os.path.join(DOWNLOAD_MOVIES_PATH, movie_folder)
        search_dirs.append(exact_dl)
        if not os.path.isdir(exact_dl):
            match = find_strm_folder_match(DOWNLOAD_MOVIES_PATH, movie_folder)
            if match:
                search_dirs.append(os.path.join(DOWNLOAD_MOVIES_PATH, match))
        # Title language can differ (IT strm vs EN Radarr folder); match by tmdbid.
        tmdb_id = _extract_tmdb_id_from_path(strm_path) or _extract_tmdb_id_from_path(
            movie_folder
        )
        tmdb_match = find_folder_by_tmdb_id(DOWNLOAD_MOVIES_PATH, tmdb_id)
        if tmdb_match:
            search_dirs.append(os.path.join(DOWNLOAD_MOVIES_PATH, tmdb_match))
    seen_dirs = set()
    for directory in search_dirs:
        real_dir = os.path.realpath(directory)
        if real_dir in seen_dirs:
            continue
        seen_dirs.add(real_dir)
        folder_name = os.path.basename(real_dir)
        hit = _local_movie_file(real_dir, folder_name)
        if hit:
            found.append(hit)
    return list(dict.fromkeys(found))


def local_download_exists_for_strm(strm_path: str) -> bool:
    return bool(find_local_files_for_strm(strm_path))


def find_strm_files_for_local(local_path: str) -> list[str]:
    """Find .strm library files replaced by a local download."""
    local_path = os.path.realpath(local_path)
    if LOCAL_DOWNLOAD_MARKER not in os.path.basename(local_path):
        return []

    found: list[str] = []
    episode_numbers = parse_episode_numbers_from_path(local_path)
    if episode_numbers is not None:
        season, episode = episode_numbers
        series_folder = series_folder_from_media_path(local_path) or ""
        se_tag = f"S{int(season):02d}E{int(episode):02d}"
        search_roots = [STRM_OUTPUT_SERIES_PATH]
        for root in search_roots:
            for folder_name in _candidate_series_folders(series_folder, root):
                series_dir = os.path.join(root, folder_name)
                if not os.path.isdir(series_dir):
                    continue
                for dirpath, _dirs, files in os.walk(series_dir):
                    for filename in files:
                        if not filename.lower().endswith(".strm"):
                            continue
                        if se_tag not in filename.upper():
                            continue
                        found.append(os.path.join(dirpath, filename))
        strm_dir = os.path.dirname(local_path)
        for filename in os.listdir(strm_dir):
            if filename.lower().endswith(".strm") and se_tag in filename.upper():
                found.append(os.path.join(strm_dir, filename))
        return list(dict.fromkeys(found))

    movie_folder = os.path.basename(os.path.dirname(local_path))
    search_dirs = [os.path.join(STRM_OUTPUT_MOVIES_PATH, movie_folder)]
    if not os.path.isdir(search_dirs[0]):
        match = find_strm_folder_match(STRM_OUTPUT_MOVIES_PATH, movie_folder)
        if match:
            search_dirs.append(os.path.join(STRM_OUTPUT_MOVIES_PATH, match))
    for directory in search_dirs:
        if not os.path.isdir(directory):
            continue
        for filename in os.listdir(directory):
            if filename.lower().endswith(".strm"):
                found.append(os.path.join(directory, filename))
    return list(dict.fromkeys(found))


_MEDIA_SIDECAR_EXTS = (".nfo", ".jpg", ".jpeg", ".png", ".webp")
_TMDB_ID_IN_PATH_RE = re.compile(r"\[tmdbid-(\d+)\]", re.IGNORECASE)
_EPISODE_NFO_SKIP_NAMES = {"season.nfo", "tvshow.nfo", "folder.nfo"}


def _remove_path_and_sidecars(media_path: str) -> list[str]:
    """Delete a media file and same-basename sidecars (.nfo / images)."""
    removed: list[str] = []
    if not media_path:
        return removed
    real = os.path.realpath(media_path)
    if os.path.isfile(real):
        try:
            os.remove(real)
            removed.append(real)
        except OSError:
            pass
    base, _ext = os.path.splitext(real)
    for sidecar_ext in _MEDIA_SIDECAR_EXTS:
        sidecar = base + sidecar_ext
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
                removed.append(sidecar)
            except OSError:
                pass
    return removed


def movie_download_dir(local_path: str) -> str:
    """Return the movie folder for a local download or any path inside it.

    Always treats a path that looks like a media/sidecar file as a file path even
    when it does not exist yet (so we never mkdir the ``.mkv`` basename by mistake).
    """
    path = os.path.realpath(local_path) if local_path else ""
    if not path:
        return ""
    if os.path.isdir(path):
        # Guard: a previous bug created dirs named like ``Title [LOCAL].mkv``.
        base = os.path.basename(path).lower()
        if any(base.endswith(ext) for ext in VIDEO_EXTENSIONS) or base.endswith(".strm"):
            return os.path.dirname(path)
        return path
    return os.path.dirname(path)


def ensure_movie_output_is_file(output_file: str) -> None:
    """If output_file wrongly exists as a directory, remove it so yt-dlp can write."""
    if not output_file:
        return
    try:
        if os.path.isdir(output_file):
            import shutil

            shutil.rmtree(output_file)
    except OSError:
        pass


def growing_download_bytes(output_path: str) -> int:
    """Bytes on disk for an in-progress yt-dlp download (final file and/or ``.part``)."""
    if not output_path:
        return 0
    total = 0
    for path in (output_path, f"{output_path}.part"):
        try:
            if os.path.isfile(path):
                total = max(total, os.path.getsize(path))
        except OSError:
            continue
    return total


def write_movie_strm_url_sidecar(
    local_path: str,
    url: str,
    *,
    strm_path: str | None = None,
) -> str | None:
    """Persist Xtream URL so a movie .strm can be restored after the local file is removed."""
    url = (url or "").strip()
    if not url:
        return None
    directory = movie_download_dir(local_path)
    if not directory:
        return None
    try:
        prepare_output_dir(directory)
    except OSError:
        pass
    payload = {
        "url": url,
        "strm_path": os.path.realpath(strm_path) if strm_path else "",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    sidecar = os.path.join(directory, MOVIE_STRM_URL_SIDECAR)
    try:
        with open(sidecar, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return sidecar
    except OSError:
        return None


def read_movie_strm_url_sidecar(local_path: str) -> dict:
    """Load sidecar written by write_movie_strm_url_sidecar (empty dict if missing)."""
    directory = movie_download_dir(local_path)
    sidecar = os.path.join(directory, MOVIE_STRM_URL_SIDECAR)
    data = load_json_file(sidecar, {})
    return data if isinstance(data, dict) else {}


def resolve_movie_strm_restore_path(
    local_path: str,
    *,
    strm_path: str | None = None,
) -> str:
    """Preferred .strm path to recreate for a movie local download."""
    if strm_path:
        return os.path.realpath(strm_path)
    meta = read_movie_strm_url_sidecar(local_path)
    saved = str(meta.get("strm_path") or "").strip()
    if saved:
        return os.path.realpath(saved)
    movie_folder = os.path.basename(movie_download_dir(local_path))
    folder = os.path.join(STRM_OUTPUT_MOVIES_PATH, movie_folder)
    if not os.path.isdir(folder):
        match = find_strm_folder_match(STRM_OUTPUT_MOVIES_PATH, movie_folder)
        if match:
            folder = os.path.join(STRM_OUTPUT_MOVIES_PATH, match)
            movie_folder = match
    return os.path.join(folder, f"{movie_folder}.strm")


def is_media_considered_watched(
    *,
    played: bool | None = None,
    position_ticks: int = 0,
    run_time_ticks: int = 0,
    threshold: float = 0.90,
) -> bool:
    """True when Emby/JF marked Played, or playhead is past the watched threshold."""
    if played:
        return True
    runtime = int(run_time_ticks or 0)
    position = int(position_ticks or 0)
    if runtime <= 0 or position <= 0:
        return False
    try:
        cut = float(threshold)
    except (TypeError, ValueError):
        cut = 0.90
    cut = min(0.99, max(0.5, cut))
    return (position / runtime) >= cut


def delete_strm_after_local_download(
    local_path: str,
    *,
    strm_path: str | None = None,
    strm_url: str | None = None,
) -> list[str]:
    """Delete .strm files (and sidecars) superseded by a completed local download."""
    candidates: list[str] = []
    if strm_path:
        candidates.append(os.path.realpath(strm_path))
    candidates.extend(find_strm_files_for_local(local_path))

    deleted: list[str] = []
    seen: set[str] = set()
    saved_url = (strm_url or "").strip()
    for path in candidates:
        real = os.path.realpath(path)
        if real in seen or not real.lower().endswith(".strm"):
            continue
        seen.add(real)
        if not os.path.isfile(real):
            continue
        if not saved_url:
            saved_url = read_strm_url(real) or ""
        # Prefer Xtream URL from sidecar if .strm already points at the local proxy.
        if saved_url and "/p/movie/" in saved_url.lower():
            meta = read_movie_strm_url_sidecar(local_path)
            remote = str(meta.get("url") or "").strip()
            if remote and "/p/movie/" not in remote.lower():
                saved_url = remote
        # Movies only: keep URL so we can restore .strm after the title is watched.
        if saved_url and parse_episode_numbers_from_path(local_path) is None:
            if "/p/movie/" not in saved_url.lower():
                write_movie_strm_url_sidecar(local_path, saved_url, strm_path=real)
        deleted.extend(_remove_path_and_sidecars(real))
    if saved_url and parse_episode_numbers_from_path(local_path) is None:
        if "/p/movie/" not in saved_url.lower():
            write_movie_strm_url_sidecar(local_path, saved_url, strm_path=strm_path)
    return deleted


def delete_movie_local_and_restore_strm(
    local_path: str,
    *,
    strm_url: str | None = None,
    strm_path: str | None = None,
    notify: bool = True,
) -> dict:
    """Delete a movie [LOCAL] download and recreate its .strm (HDD cleanup after watched)."""
    result: dict = {
        "local_deleted": [],
        "strm_path": "",
        "strm_restored": False,
        "notify": [],
        "errors": [],
    }
    local_path = os.path.realpath(local_path)
    if not local_path or not os.path.exists(local_path):
        # Still try restore if only sidecars / folder remain.
        pass
    if parse_episode_numbers_from_path(local_path) is not None:
        result["errors"].append("not_a_movie")
        return result

    meta = read_movie_strm_url_sidecar(local_path)
    remote = (strm_url or str(meta.get("url") or "")).strip()
    # Sidecar must keep the Xtream URL, not a proxy URL.
    if remote and "/p/movie/" in remote.lower():
        remote = ""
    target_strm = resolve_movie_strm_restore_path(
        local_path, strm_path=strm_path or str(meta.get("strm_path") or "") or None
    )
    result["strm_path"] = target_strm

    if not remote:
        result["errors"].append("strm_url_missing")
        return result

    # Restore .strm to progressive proxy URL when enabled, else direct Xtream URL.
    folder_name = os.path.basename(movie_download_dir(local_path))
    try:
        from stream_proxy import resolve_movie_play_url, stream_proxy_enabled

        play_url = resolve_movie_play_url(
            folder_name=folder_name,
            remote_url=remote,
            strm_path=target_strm,
            ext=os.path.splitext(local_path)[1].lstrip(".") or "mkv",
        )
    except Exception:
        play_url = remote

    try:
        prepare_output_dir(os.path.dirname(target_strm))
        write_strm(target_strm, play_url)
        write_movie_strm_url_sidecar(local_path, remote, strm_path=target_strm)
        result["strm_restored"] = True
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(f"strm_restore:{exc}")
        return result

    removed: list[str] = []
    directory = movie_download_dir(local_path)
    # Delete video + same-basename sidecars.
    if os.path.isfile(local_path):
        removed.extend(_remove_path_and_sidecars(local_path))
    if os.path.isdir(directory):
        try:
            for name in os.listdir(directory):
                full = os.path.join(directory, name)
                if not os.path.isfile(full):
                    continue
                lower = name.lower()
                if lower == MOVIE_STRM_URL_SIDECAR.lower():
                    try:
                        os.remove(full)
                        removed.append(full)
                    except OSError:
                        pass
                    continue
                ext = os.path.splitext(lower)[1]
                if ext in VIDEO_EXTENSIONS or LOCAL_DOWNLOAD_MARKER.lower() in lower:
                    removed.extend(_remove_path_and_sidecars(full))
        except OSError as exc:
            result["errors"].append(f"local_cleanup:{exc}")
        # Remove empty movie folder under /download/movies.
        try:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
                removed.append(directory)
        except OSError:
            pass

    result["local_deleted"] = removed
    if notify:
        result["notify"] = notify_media_servers_after_local_download(
            target_strm, deleted_paths=removed
        )
    return result


def _episode_nfo_matches(filename: str, season: int, episode: int) -> bool:
    lower = filename.lower()
    if not lower.endswith(".nfo"):
        return False
    if lower in _EPISODE_NFO_SKIP_NAMES:
        return False
    match = EPISODE_TAG_RE.search(filename)
    if not match:
        return False
    return int(match.group(1)) == int(season) and int(match.group(2)) == int(episode)


def _episode_nfo_search_dirs(media_path: str, season: int) -> list[str]:
    """Season dirs on download + strm sides where leftover episode NFOs may live."""
    dirs: list[str] = [os.path.dirname(media_path)]
    series_folder = series_folder_from_media_path(media_path) or ""
    search_roots = (STRM_OUTPUT_SERIES_PATH, *SERIES_DOWNLOAD_PATHS)
    for root in search_roots:
        for folder_name in _candidate_series_folders(series_folder, root):
            series_dir = os.path.join(root, folder_name)
            if not os.path.isdir(series_dir):
                continue
            found_season = False
            try:
                for name in os.listdir(series_dir):
                    match = SEASON_DIR_RE.match(name)
                    if match and int(match.group(1)) == int(season):
                        dirs.append(os.path.join(series_dir, name))
                        found_season = True
            except OSError:
                pass
            if not found_season:
                dirs.append(os.path.join(series_dir, f"Season {int(season):02d}"))
    seen: set[str] = set()
    out: list[str] = []
    for directory in dirs:
        try:
            real = os.path.realpath(directory)
        except OSError:
            continue
        if real in seen or not os.path.isdir(real):
            continue
        seen.add(real)
        out.append(real)
    return out


def align_episode_nfo_to_media(media_path: str) -> dict:
    """Make episode .nfo match the media basename; drop stale same-SxxExx NFOs.

    Works for both [LOCAL] videos and restored .strm files. If the target .nfo is
    missing and an older episode .nfo exists, rename the best candidate onto the
    new basename. Always delete other same-episode leftovers.
    """
    result: dict = {
        "target_nfo": "",
        "renamed_from": "",
        "deleted": [],
        "skipped": False,
    }
    media_path = os.path.realpath(media_path)
    episode_numbers = parse_episode_numbers_from_path(media_path)
    if episode_numbers is None:
        result["skipped"] = True
        return result
    season, episode = episode_numbers
    target_nfo = os.path.splitext(media_path)[0] + ".nfo"
    result["target_nfo"] = target_nfo

    candidates: list[str] = []
    seen: set[str] = set()
    for directory in _episode_nfo_search_dirs(media_path, season):
        try:
            names = os.listdir(directory)
        except OSError:
            continue
        for name in names:
            if not _episode_nfo_matches(name, season, episode):
                continue
            full = os.path.realpath(os.path.join(directory, name))
            if full in seen:
                continue
            seen.add(full)
            candidates.append(full)

    target_real = os.path.realpath(target_nfo)
    target_exists = os.path.isfile(target_real)
    others = [path for path in candidates if path != target_real]

    if not target_exists and others:
        media_dir = os.path.realpath(os.path.dirname(media_path))
        preferred = next(
            (path for path in others if os.path.dirname(path) == media_dir),
            others[0],
        )
        try:
            os.replace(preferred, target_nfo)
            result["renamed_from"] = preferred
            others = [path for path in others if path != preferred]
            target_exists = True
        except OSError:
            pass

    deleted: list[str] = []
    for path in others:
        try:
            os.remove(path)
            deleted.append(path)
        except OSError:
            pass
    result["deleted"] = deleted
    return result


def align_episode_nfo_after_local_download(local_path: str) -> dict:
    """Make episode .nfo match the [LOCAL] video basename; drop stale SxxExx NFOs."""
    basename = os.path.basename(local_path)
    if LOCAL_DOWNLOAD_MARKER not in basename:
        return {
            "target_nfo": "",
            "renamed_from": "",
            "deleted": [],
            "skipped": True,
        }
    return align_episode_nfo_to_media(local_path)


def _media_server_path_mappings(
    *,
    server: str,
    config: dict | None = None,
) -> list[tuple[str, str]]:
    """Pairs of (local_root, server_root) for Emby/Jellyfin path translation."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    server_key = (server or "").lower()
    if server_key == "jellyfin":
        series_dst = str(cfg.get("jellyfin_series_root") or "/media/tv").rstrip("/")
        movies_dst = str(cfg.get("jellyfin_movies_root") or "/media/movies").rstrip("/")
    else:
        series_dst = str(cfg.get("emby_series_root") or "/data/tv").rstrip("/")
        movies_dst = str(cfg.get("emby_movies_root") or "/data/movies").rstrip("/")
    return [
        (DOWNLOAD_TV_PATH.rstrip("/"), series_dst),
        (STRM_OUTPUT_SERIES_PATH.rstrip("/"), series_dst),
        (DOWNLOAD_MOVIES_PATH.rstrip("/"), movies_dst),
        (STRM_OUTPUT_MOVIES_PATH.rstrip("/"), movies_dst),
    ]


def map_local_path_to_media_server(
    path: str,
    *,
    server: str,
    config: dict | None = None,
) -> str | None:
    """Map xtream-downloader library path to Emby/Jellyfin container path."""
    if not path:
        return None
    normalized = path.replace("\\", "/")
    for src, dst in _media_server_path_mappings(server=server, config=config):
        if not src or not dst:
            continue
        if normalized == src:
            return dst
        if normalized.startswith(src + "/"):
            return dst + normalized[len(src) :]
        try:
            real_src = os.path.realpath(src).replace("\\", "/")
            real_path = os.path.realpath(path).replace("\\", "/")
        except OSError:
            continue
        if real_path == real_src:
            return dst
        if real_path.startswith(real_src + "/"):
            return dst + real_path[len(real_src) :]
    return None


def map_media_server_path_to_local(
    path: str,
    *,
    server: str,
    config: dict | None = None,
) -> str | None:
    """Map Emby/Jellyfin item Path back to a path readable in this container."""
    if not path:
        return None
    normalized = path.replace("\\", "/")
    if os.path.isfile(normalized):
        return normalized
    # Prefer strm roots before download roots when both could match.
    mappings = _media_server_path_mappings(server=server, config=config)
    ordered = sorted(
        mappings,
        key=lambda pair: 0 if "strm" in pair[0].lower() else 1,
    )
    for src, dst in ordered:
        if not src or not dst:
            continue
        if normalized == dst:
            candidate = src
        elif normalized.startswith(dst + "/"):
            candidate = src + normalized[len(dst) :]
        else:
            continue
        if os.path.exists(candidate):
            return candidate
    return None


def _extract_tmdb_id_from_path(path: str) -> int | None:
    match = _TMDB_ID_IN_PATH_RE.search(path or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def notify_media_servers_after_local_download(
    local_path: str,
    *,
    deleted_paths: list[str] | None = None,
) -> list[str]:
    """Notify Emby/Jellyfin of created local media and deleted .strm paths."""
    notes: list[str] = []
    try:
        from emby_watcher import MediaServerClient
    except ImportError:
        return ["media_client_unavailable"]

    config = load_auto_download_config()
    servers: list[tuple[str, str, str, str]] = []
    if config.get("emby_enabled"):
        servers.append(
            (
                "emby",
                str(config.get("emby_url") or "").strip(),
                str(config.get("emby_api_key") or "").strip(),
                "emby",
            )
        )
    if config.get("jellyfin_enabled"):
        servers.append(
            (
                "jellyfin",
                str(config.get("jellyfin_url") or "").strip(),
                str(config.get("jellyfin_api_key") or "").strip(),
                "jellyfin",
            )
        )
    if not servers:
        return ["no_media_servers"]

    local_path = os.path.realpath(local_path)
    series_folder = series_folder_from_media_path(local_path)
    series_dir = ""
    if series_folder:
        parent = os.path.dirname(local_path)
        # Season XX -> series folder
        if SEASON_DIR_RE.match(os.path.basename(parent) or ""):
            series_dir = os.path.dirname(parent)
        else:
            series_dir = parent

    tmdb_id = _extract_tmdb_id_from_path(local_path) or _extract_tmdb_id_from_path(
        series_folder or ""
    )
    is_episode = parse_episode_numbers_from_path(local_path) is not None

    for _name, url, api_key, server_type in servers:
        if not url or not api_key:
            notes.append(f"{server_type}:not_configured")
            continue
        try:
            client = MediaServerClient(url, api_key, server_type)
            updates: list[dict] = []
            mapped_local = map_local_path_to_media_server(
                local_path, server=server_type, config=config
            )
            if mapped_local:
                updates.append({"Path": mapped_local, "UpdateType": "Created"})
            if series_dir:
                mapped_series = map_local_path_to_media_server(
                    series_dir, server=server_type, config=config
                )
                if mapped_series:
                    updates.append({"Path": mapped_series, "UpdateType": "Modified"})
            else:
                mapped_series = None
            for deleted in deleted_paths or []:
                mapped_del = map_local_path_to_media_server(
                    deleted, server=server_type, config=config
                )
                if mapped_del:
                    updates.append({"Path": mapped_del, "UpdateType": "Deleted"})
            # Deduplicate while preserving order
            seen_upd: set[tuple[str, str]] = set()
            unique_updates: list[dict] = []
            for upd in updates:
                key = (str(upd.get("Path") or ""), str(upd.get("UpdateType") or ""))
                if not key[0] or key in seen_upd:
                    continue
                seen_upd.add(key)
                unique_updates.append(upd)
            if unique_updates:
                client.notify_library_paths(unique_updates)

            refreshed = 0
            if is_episode and series_dir:
                series_items: list = []
                if mapped_series:
                    try:
                        series_items = client.find_series_near_path(
                            mapped_series, tmdb_id=tmdb_id
                        )
                    except Exception:
                        series_items = []
                if not series_items and tmdb_id:
                    try:
                        series_items = client.find_series_by_tmdb_id(tmdb_id)
                    except Exception:
                        series_items = []
                for item in series_items:
                    item_id = str(item.get("Id") or "")
                    if not item_id:
                        continue
                    try:
                        client.refresh_item_metadata(item_id, replace_all=False)
                        refreshed += 1
                    except Exception:
                        continue
            notes.append(
                f"{server_type}:ok updates={len(unique_updates)} refreshed={refreshed}"
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{server_type}:error:{exc}")
    return notes


def finalize_after_local_download(
    local_path: str,
    *,
    strm_path: str | None = None,
    strm_url: str | None = None,
    notify: bool = True,
) -> dict:
    """Post-download cleanup: remove superseded .strm, align NFOs, notify JF/Emby."""
    deleted = delete_strm_after_local_download(
        local_path, strm_path=strm_path, strm_url=strm_url
    )
    nfo = align_episode_nfo_after_local_download(local_path)
    notify_notes: list[str] = []
    if notify:
        # Include removed leftover NFOs so servers drop orphans quickly.
        deleted_for_notify = list(deleted) + list(nfo.get("deleted") or [])
        notify_notes = notify_media_servers_after_local_download(
            local_path, deleted_paths=deleted_for_notify
        )
    return {
        "deleted": deleted,
        "nfo": nfo,
        "notify": notify_notes,
    }


# Per-root folder listing cache for find_strm_folder_match.
# Without this, each sync call re-listdir's /download/movies (~20k dirs) per title.
_folder_index_lock = threading.Lock()


class _FolderIndex:
    __slots__ = ("mtime", "exact", "loose", "by_tmdb")

    def __init__(
        self,
        mtime: float,
        exact: dict[str, list[tuple[str, int | None]]],
        loose: dict[str, list[tuple[str, int | None]]],
        by_tmdb: dict[int, list[str]] | None = None,
    ) -> None:
        self.mtime = mtime
        # normalized_title -> [(folder_name, year)]
        self.exact = exact
        # normalized_title without leading "the" -> [(folder_name, year)]
        self.loose = loose
        # tmdb_id -> [folder_name, ...]
        self.by_tmdb = by_tmdb or {}


_folder_index: dict[str, _FolderIndex] = {}


def clear_folder_match_cache(root: str | None = None) -> None:
    """Drop cached folder listings (all roots, or one). Useful in tests."""
    with _folder_index_lock:
        if root is None:
            _folder_index.clear()
            return
        keys = {root}
        try:
            keys.add(os.path.realpath(root))
        except OSError:
            pass
        for key in keys:
            _folder_index.pop(key, None)


def _loose_title_key(normalized: str) -> str:
    tokens = normalized.split()
    if tokens and tokens[0] == "the":
        return " ".join(tokens[1:])
    return normalized


def _build_folder_index(root: str, mtime: float) -> _FolderIndex:
    exact: dict[str, list[tuple[str, int | None]]] = {}
    loose: dict[str, list[tuple[str, int | None]]] = {}
    by_tmdb: dict[int, list[str]] = {}
    try:
        names = os.listdir(root)
    except OSError:
        return _FolderIndex(mtime, exact, loose, by_tmdb)
    for name in names:
        folder = os.path.join(root, name)
        if not os.path.isdir(folder):
            continue
        tmdb_id = _extract_tmdb_id_from_path(name)
        if tmdb_id is not None:
            by_tmdb.setdefault(tmdb_id, []).append(name)
        norm = normalize_title(name)
        if not norm:
            continue
        year = extract_title_year(name)
        entry = (name, year)
        exact.setdefault(norm, []).append(entry)
        loose.setdefault(_loose_title_key(norm), []).append(entry)
    return _FolderIndex(mtime, exact, loose, by_tmdb)


def _get_folder_index(root: str) -> _FolderIndex | None:
    """Return a mtime-cached index of directories under root."""
    try:
        mtime = os.path.getmtime(root)
        key = os.path.realpath(root)
    except OSError:
        return None

    with _folder_index_lock:
        cached = _folder_index.get(key)
        if cached is not None and cached.mtime == mtime:
            return cached

    built = _build_folder_index(root, mtime)
    with _folder_index_lock:
        try:
            mtime_now = os.path.getmtime(root)
        except OSError:
            mtime_now = mtime
        # Keep the listing we built; stamp with current mtime for next hit.
        built.mtime = mtime_now
        _folder_index[key] = built
        return built


def _pick_folder_match(
    candidates: list[tuple[str, int | None]],
    target_year: int | None,
    *,
    target_norm: str,
    prefer_shortest_exact: bool,
) -> str | None:
    # Same year filter as before: drop only when both sides have years and they differ.
    names = [
        name
        for name, folder_year in candidates
        if not (target_year and folder_year and target_year != folder_year)
    ]
    if not names:
        return None
    # Prefer Emby/Jellyfin TMDB-named folders over plain duplicates.
    tmdb_names = [name for name in names if folder_has_tmdb_id(name)]
    pool = tmdb_names or names
    if prefer_shortest_exact:
        return sorted(pool, key=len)[0]
    return min(
        pool,
        key=lambda name: abs(len(normalize_title(name)) - len(target_norm)),
    )


def find_strm_folder_match(root: str, title: str) -> str | None:
    if not root or not os.path.isdir(root):
        return None
    target = normalize_title(title)
    if not target:
        return None
    target_year = extract_title_year(title)
    index = _get_folder_index(root)
    if index is None:
        return None

    exact = index.exact.get(target)
    if exact:
        return _pick_folder_match(
            exact, target_year, target_norm=target, prefer_shortest_exact=True
        )

    loose = index.loose.get(_loose_title_key(target))
    if loose:
        # Exclude exact-normalized duplicates already handled above; loose
        # matching only covers leading-article differences.
        return _pick_folder_match(
            loose, target_year, target_norm=target, prefer_shortest_exact=False
        )
    return None


def find_folder_by_tmdb_id(root: str, tmdb_id: int | str | None) -> str | None:
    """Return a directory name under root that carries [tmdbid-N]."""
    if root is None or tmdb_id is None:
        return None
    try:
        tid = int(tmdb_id)
    except (TypeError, ValueError):
        return None
    if not os.path.isdir(root):
        return None
    index = _get_folder_index(root)
    if index is None:
        return None
    names = index.by_tmdb.get(tid) or []
    if not names:
        return None
    # Prefer TMDB-tagged folders; shortest name wins when duplicates exist.
    return sorted(names, key=len)[0]


def resolve_series_folder_name(series_name: str, strm_path: str | None = None) -> str:
    if strm_path:
        from_strm = series_folder_from_strm_path(strm_path)
        if from_strm:
            return sanitize_filename(from_strm)
    match = find_strm_folder_match(STRM_SERIES_PATH, series_name)
    if match:
        return sanitize_filename(match)
    # After .strm deletion the library path is gone; reuse an existing download
    # folder (preferring [tmdbid-…] names) instead of creating a plain duplicate.
    for root in SERIES_DOWNLOAD_PATHS:
        match = find_strm_folder_match(root, series_name)
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
    match = find_strm_folder_match(DOWNLOAD_MOVIES_PATH, movie_name)
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


def _is_under_strm_output_roots(path: str) -> bool:
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep) for root in STRM_OUTPUT_ROOTS)


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
    """Ensure download/.data roots exist and have correct owner/mode.

    Only touches the root directories themselves. A full recursive walk over the
    library (tens of thousands of folders on HDD/WSL) used to run on every
    Streamlit rerun and made menu changes take minutes. Per-file ownership is
    still applied when downloads finish via finalize_download_path().
    """
    uid, gid = owner_ids()
    for base in list(DOWNLOAD_ROOTS) + [DATA_DIR]:
        os.makedirs(base, mode=DIR_MODE, exist_ok=True)
        _apply_path_permissions(base, uid, gid)


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


def finalize_strm_path(path: str, fix_children: bool = False) -> None:
    if not path or not os.path.exists(path):
        return
    uid, gid = owner_ids()
    current = os.path.realpath(path)
    # Always fix the target itself, then walk up parents under known roots.
    _apply_path_permissions(current, uid, gid)
    parent = os.path.dirname(current)
    while parent and _is_under_strm_output_roots(parent):
        _apply_path_permissions(parent, uid, gid)
        if parent in STRM_OUTPUT_ROOTS:
            break
        parent = os.path.dirname(parent)
    if fix_children and os.path.isdir(path):
        for root, dirs, files in os.walk(path):
            for name in dirs + files:
                _apply_path_permissions(os.path.join(root, name), uid, gid)


def prepare_strm_dir(path: str) -> None:
    if not path:
        return
    path = os.path.normpath(path)
    # Track which directory levels we actually create so we can fix their
    # ownership even when the output is an arbitrary path (e.g. a test folder)
    # outside the known STRM output roots.
    created: list[str] = []
    cur = path
    while cur and not os.path.isdir(cur):
        created.append(cur)
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    os.makedirs(path, mode=DIR_MODE, exist_ok=True)
    uid, gid = owner_ids()
    for level in reversed(created):
        _apply_path_permissions(level, uid, gid)


def write_strm(path: str, url: str) -> bool:
    """Write a .strm file. Returns True if created/updated, False if unchanged."""
    url = url.strip()
    if not url:
        return False
    existing = read_strm_url(path)
    if existing == url and os.path.isfile(path):
        return False
    parent = os.path.dirname(path)
    prepare_strm_dir(parent)
    with open(path, "w", encoding="utf-8") as f:
        f.write(url + "\n")
    finalize_strm_path(path)
    return True


def build_movie_strm_path(movie_name: str, dest_root: str) -> tuple[str, str]:
    safe_name = sanitize_filename(movie_name)
    folder = os.path.join(dest_root, safe_name)
    return folder, os.path.join(folder, f"{safe_name}.strm")


def move_strm_library(src_root: str, dst_root: str, *, overwrite: bool = True) -> dict:
    """Move every .strm (and sibling .nfo) from src_root into dst_root.

    Mirrors the relative folder structure so a library generated in a test
    directory can be promoted to the working directory without regenerating.
    """
    import shutil

    result = {"moved": 0, "skipped": 0, "removed_dirs": 0}
    if not src_root or not os.path.isdir(src_root):
        return result
    src_root = os.path.realpath(src_root)
    prepare_strm_dir(dst_root)

    for dirpath, _dirs, files in os.walk(src_root):
        for filename in files:
            if not filename.lower().endswith((".strm", ".nfo")):
                continue
            src_path = os.path.join(dirpath, filename)
            rel = os.path.relpath(src_path, src_root)
            dst_path = os.path.join(dst_root, rel)
            if os.path.exists(dst_path) and not overwrite:
                result["skipped"] += 1
                continue
            prepare_strm_dir(os.path.dirname(dst_path))
            shutil.move(src_path, dst_path)
            finalize_strm_path(dst_path)
            result["moved"] += 1

    for dirpath, dirs, files in os.walk(src_root, topdown=False):
        if dirpath == src_root:
            continue
        if not os.listdir(dirpath):
            try:
                os.rmdir(dirpath)
                result["removed_dirs"] += 1
            except OSError:
                pass
    return result


def build_episode_strm_path(
    series_name: str,
    season: int,
    episode: int,
    dest_root: str,
    *,
    strm_path: str | None = None,
) -> tuple[str, str]:
    safe_series = resolve_series_folder_name(series_name, strm_path)
    season_folder = resolve_season_folder_name(safe_series, season, strm_path)
    folder = os.path.join(dest_root, safe_series, season_folder)
    filename = f"{safe_series} - S{int(season):02d}E{int(episode):02d}.strm"
    return folder, os.path.join(folder, filename)


def tmdb_movie_folder_name(title: str, year: int | None, tmdb_id: int | str | None) -> str:
    base = sanitize_filename(str(title).strip())
    if year:
        base = f"{base} ({int(year)})"
    if tmdb_id:
        base = f"{base} [tmdbid-{tmdb_id}]"
    return base


def tmdb_series_folder_name(title: str, year: int | None, tmdb_id: int | str | None) -> str:
    return tmdb_movie_folder_name(title, year, tmdb_id)


def build_movie_strm_path_tmdb(
    title: str,
    year: int | None,
    tmdb_id: int | str | None,
    dest_root: str,
) -> tuple[str, str]:
    folder_name = tmdb_movie_folder_name(title, year, tmdb_id)
    folder = os.path.join(dest_root, folder_name)
    return folder, os.path.join(folder, f"{folder_name}.strm")


def build_episode_strm_path_tmdb(
    title: str,
    year: int | None,
    tmdb_id: int | str | None,
    season: int,
    episode: int,
    dest_root: str,
) -> tuple[str, str]:
    folder_name = tmdb_series_folder_name(title, year, tmdb_id)
    season_folder = f"Season {int(season):02d}"
    folder = os.path.join(dest_root, folder_name, season_folder)
    safe_title = sanitize_filename(str(title).strip())
    if year:
        safe_title = f"{safe_title} ({int(year)})"
    filename = f"{safe_title} - S{int(season):02d}E{int(episode):02d}.strm"
    return folder, os.path.join(folder, filename)


DEFAULT_ADULT_TERMS = [
    "xxx",
    "porn",
    "porno",
    "hardcore",
    "hard core",
    "softcore",
    "erotic",
    "erotico",
    "erotica",
    "sesso",
    "sexo",
    "adult",
    "adulti",
    "+18",
    "18+",
    "vietato ai minori",
    "brazzers",
    "onlyfans",
    "milf",
    "hentai",
    "creampie",
    "anal",
    "blowjob",
    "fetish",
    "bdsm",
    "camgirl",
    "playboy",
    "naughty",
]

_ADULT_CATEGORY_RE = re.compile(
    r"(?:\bxxx\b|\bporn|\badult|\bhard\b|\berotic|\bsex\b|\bsexo\b|\bsesso\b|"
    r"\bhentai\b|\+18\b|\b18\+|\bvm18\b|\bvietat)",
    re.IGNORECASE,
)


def is_adult_category(category_name: str) -> bool:
    return bool(_ADULT_CATEGORY_RE.search(category_name or ""))


def _term_matches(text: str, term: str) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    haystack = text.lower()
    if not re.search(r"[a-z0-9]", term):
        return term in haystack
    if re.fullmatch(r"[a-z0-9 ]+", term):
        return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack) is not None
    return term in haystack


def title_matches_terms(name: str, terms: list[str]) -> bool:
    for term in terms:
        if _term_matches(name, term):
            return True
    return False


def _ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Non-recursive: this runs on every JSON save; .data can grow large (TMDB cache).
    finalize_download_path(DATA_DIR, fix_children=False)


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


def default_ui_prefs() -> dict:
    return {"mode": "manual", "content": "movies"}


def load_ui_prefs() -> dict:
    defaults = default_ui_prefs()
    data = load_json_file(UI_PREFS_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    mode = data.get("mode", defaults["mode"])
    content = data.get("content", defaults["content"])
    if mode not in ("manual", "strm", "duration", "auto", "assist"):
        mode = defaults["mode"]
    if content not in ("movies", "series"):
        content = defaults["content"]
    return {"mode": mode, "content": content}


def save_ui_prefs(prefs: dict) -> None:
    current = load_ui_prefs()
    mode = prefs.get("mode", current["mode"])
    content = prefs.get("content", current["content"])
    if mode not in ("manual", "strm", "duration", "auto", "assist"):
        mode = current["mode"]
    if content not in ("movies", "series"):
        content = current["content"]
    _save_json_file(UI_PREFS_FILE, {"mode": mode, "content": content})


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
        "continue_download_incomplete": True,
        "allow_4k": False,
        "prefetch_playing_strm": False,
        "prefetch_buffer_mb": 20,
        "prefetch_buffer_seconds": 120,
        "prefetch_min_speed_ratio": 1.3,
        "prefetch_max_wait_seconds": 180,
        "prefetch_auto_switch": True,
        "cleanup_watched_movie_downloads": True,
        "watched_movie_threshold": 0.90,
        "stream_proxy_enabled": True,
        "stream_proxy_host": os.environ.get("STREAM_PROXY_HOST", ""),
        "stream_proxy_port": int(os.environ.get("STREAM_PROXY_PORT", "8510")),
        "stream_proxy_download": False,
        "jellyfin_series_root": "/media/tv",
        "jellyfin_movies_root": "/media/movies",
        "emby_series_root": "/data/tv",
        "emby_movies_root": "/data/movies",
        "auto_intro_skip_enabled": False,
        "auto_intro_skip_download": True,
        "auto_intro_skip_keep_until_watched": False,
        "auto_subs_enabled": False,
        "auto_subs_prefer_forced": True,
        "auto_subs_language": "it",
        "opensubtitles_username": os.environ.get("OPENSUBTITLES_USERNAME", ""),
        "opensubtitles_password": os.environ.get("OPENSUBTITLES_PASSWORD", ""),
        "opensubtitles_api_key": os.environ.get(
            "OPENSUBTITLES_API_KEY", "gUCLWGoAg2PmyseoTM0INFFVPcDCeDlT"
        ),
        "opensubtitles_jf_config": os.environ.get(
            "OPENSUBTITLES_JF_CONFIG",
            "/config/Jellyfin.Plugin.OpenSubtitles.xml",
        ),
    }


def watcher_should_run(config: dict | None = None) -> bool:
    """True when auto-download or playback-assist features need the watcher."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    return bool(
        cfg.get("enabled")
        or cfg.get("auto_intro_skip_enabled")
        or cfg.get("auto_subs_enabled")
    )


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
    merged.setdefault("continue_download_incomplete", True)
    merged.setdefault("prefetch_playing_strm", False)
    merged.setdefault("prefetch_buffer_mb", 20)
    merged.setdefault("prefetch_buffer_seconds", 120)
    merged.setdefault("prefetch_min_speed_ratio", 1.3)
    merged.setdefault("prefetch_max_wait_seconds", 180)
    merged.setdefault("prefetch_auto_switch", True)
    merged.setdefault("cleanup_watched_movie_downloads", True)
    merged.setdefault("watched_movie_threshold", 0.90)
    merged.setdefault("stream_proxy_enabled", True)
    merged.setdefault("stream_proxy_host", "")
    merged.setdefault("stream_proxy_port", 8510)
    merged.setdefault("stream_proxy_download", False)
    merged.setdefault("jellyfin_series_root", "/media/tv")
    merged.setdefault("jellyfin_movies_root", "/media/movies")
    merged.setdefault("emby_series_root", "/data/tv")
    merged.setdefault("emby_movies_root", "/data/movies")
    merged.setdefault("auto_intro_skip_enabled", False)
    merged.setdefault("auto_intro_skip_download", True)
    merged.setdefault("auto_intro_skip_keep_until_watched", False)
    merged.setdefault("auto_subs_enabled", False)
    merged.setdefault("auto_subs_prefer_forced", True)
    merged.setdefault("auto_subs_language", "it")
    merged.setdefault("opensubtitles_username", "")
    merged.setdefault("opensubtitles_password", "")
    merged.setdefault(
        "opensubtitles_api_key",
        "gUCLWGoAg2PmyseoTM0INFFVPcDCeDlT",
    )
    merged.setdefault(
        "opensubtitles_jf_config",
        "/config/Jellyfin.Plugin.OpenSubtitles.xml",
    )
    merged["emby_enabled"] = bool(merged.get("emby_enabled"))
    merged["jellyfin_enabled"] = bool(merged.get("jellyfin_enabled"))
    merged["allow_4k"] = bool(merged.get("allow_4k"))
    merged["continue_download_incomplete"] = bool(merged.get("continue_download_incomplete", True))
    merged["prefetch_playing_strm"] = bool(merged.get("prefetch_playing_strm"))
    merged["prefetch_auto_switch"] = bool(merged.get("prefetch_auto_switch"))
    merged["cleanup_watched_movie_downloads"] = bool(
        merged.get("cleanup_watched_movie_downloads", True)
    )
    merged["stream_proxy_enabled"] = bool(merged.get("stream_proxy_enabled", True))
    merged["stream_proxy_host"] = str(merged.get("stream_proxy_host") or "").strip()
    merged["stream_proxy_download"] = bool(merged.get("stream_proxy_download", False))
    merged["auto_intro_skip_enabled"] = bool(merged.get("auto_intro_skip_enabled"))
    merged["auto_intro_skip_download"] = bool(merged.get("auto_intro_skip_download", True))
    merged["auto_intro_skip_keep_until_watched"] = bool(
        merged.get("auto_intro_skip_keep_until_watched")
    )
    merged["auto_subs_enabled"] = bool(merged.get("auto_subs_enabled"))
    merged["auto_subs_prefer_forced"] = bool(merged.get("auto_subs_prefer_forced", True))
    merged["auto_subs_language"] = str(merged.get("auto_subs_language") or "it").strip() or "it"
    merged["opensubtitles_username"] = str(merged.get("opensubtitles_username") or "").strip()
    merged["opensubtitles_password"] = str(merged.get("opensubtitles_password") or "").strip()
    merged["opensubtitles_api_key"] = str(
        merged.get("opensubtitles_api_key") or "gUCLWGoAg2PmyseoTM0INFFVPcDCeDlT"
    ).strip()
    merged["opensubtitles_jf_config"] = str(merged.get("opensubtitles_jf_config") or "").strip()
    try:
        merged["stream_proxy_port"] = max(
            1, int(merged.get("stream_proxy_port") or 8510)
        )
    except (TypeError, ValueError):
        merged["stream_proxy_port"] = 8510
    try:
        merged["watched_movie_threshold"] = min(
            0.99, max(0.5, float(merged.get("watched_movie_threshold") or 0.90))
        )
    except (TypeError, ValueError):
        merged["watched_movie_threshold"] = 0.90
    try:
        merged["prefetch_buffer_mb"] = max(10, int(merged.get("prefetch_buffer_mb") or 20))
    except (TypeError, ValueError):
        merged["prefetch_buffer_mb"] = 20
    try:
        merged["prefetch_buffer_seconds"] = max(30, int(merged.get("prefetch_buffer_seconds") or 120))
    except (TypeError, ValueError):
        merged["prefetch_buffer_seconds"] = 120
    try:
        merged["prefetch_max_wait_seconds"] = max(60, int(merged.get("prefetch_max_wait_seconds") or 180))
    except (TypeError, ValueError):
        merged["prefetch_max_wait_seconds"] = 180
    try:
        merged["prefetch_min_speed_ratio"] = max(1.05, float(merged.get("prefetch_min_speed_ratio") or 1.3))
    except (TypeError, ValueError):
        merged["prefetch_min_speed_ratio"] = 1.3
    for key in (
        "jellyfin_series_root",
        "jellyfin_movies_root",
        "emby_series_root",
        "emby_movies_root",
    ):
        merged[key] = str(merged.get(key) or "").strip() or {
            "jellyfin_series_root": "/media/tv",
            "jellyfin_movies_root": "/media/movies",
            "emby_series_root": "/data/tv",
            "emby_movies_root": "/data/movies",
        }[key]
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


def default_strm_sync_config() -> dict:
    return {
        "sync_movies": True,
        "sync_series": True,
        "vod_category_ids": [],
        "series_category_ids": [],
        "series_source": "api",
        "movies_output": STRM_OUTPUT_MOVIES_PATH,
        "series_output": STRM_OUTPUT_SERIES_PATH,
        "allow_4k": False,
        "update_existing": True,
        "remove_missing": False,
        "cleanup_min_ratio": 0.5,
        "refresh_emby": False,
        "refresh_jellyfin": False,
        "use_tmdb": False,
        "filter_tmdb_episodes": True,
        "tmdb_api_key": os.environ.get("TMDB_API_KEY", ""),
        "tmdb_language": os.environ.get("TMDB_LANGUAGE", "it-IT"),
        "tmdb_rate_limit": int(os.environ.get("TMDB_RATE_LIMIT", "40")),
        "exclude_terms": [],
        "exclude_adult": True,
        "adult_terms": list(DEFAULT_ADULT_TERMS),
        "schedule_enabled": False,
        "schedule_mode": "interval",
        "schedule_interval_hours": 24,
        "schedule_hour": 3,
        "schedule_minute": 0,
        "audit_new_movies_on_sync": True,
        "push_new_movies_to_jellyfin": True,
        "jellyfin_movies_root": "/media/movies",
    }


def load_strm_sync_config() -> dict:
    defaults = default_strm_sync_config()
    data = load_json_file(STRM_SYNC_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    merged = {**defaults, **data}
    for key in ("sync_movies", "sync_series", "allow_4k", "update_existing", "remove_missing"):
        merged[key] = bool(merged.get(key))
    for key in (
        "refresh_emby",
        "refresh_jellyfin",
        "use_tmdb",
        "filter_tmdb_episodes",
        "exclude_adult",
        "schedule_enabled",
        "audit_new_movies_on_sync",
        "push_new_movies_to_jellyfin",
    ):
        merged[key] = bool(merged.get(key))
    merged["jellyfin_movies_root"] = str(
        merged.get("jellyfin_movies_root") or "/media/movies"
    ).strip() or "/media/movies"
    vod_ids = merged.get("vod_category_ids", [])
    series_ids = merged.get("series_category_ids", [])
    merged["vod_category_ids"] = vod_ids if isinstance(vod_ids, list) else []
    merged["series_category_ids"] = series_ids if isinstance(series_ids, list) else []
    series_source = str(merged.get("series_source") or "api")
    if series_source not in {"api", "m3u", "m3u_api_fallback"}:
        series_source = "api"
    merged["series_source"] = series_source
    exclude_terms = merged.get("exclude_terms", [])
    merged["exclude_terms"] = [str(t) for t in exclude_terms] if isinstance(exclude_terms, list) else []
    adult_terms = merged.get("adult_terms", [])
    if not isinstance(adult_terms, list) or not adult_terms:
        adult_terms = list(DEFAULT_ADULT_TERMS)
    merged["adult_terms"] = [str(t) for t in adult_terms]
    try:
        merged["tmdb_rate_limit"] = max(1, int(merged.get("tmdb_rate_limit", 40)))
    except (TypeError, ValueError):
        merged["tmdb_rate_limit"] = 40
    try:
        merged["cleanup_min_ratio"] = max(0.05, min(1.0, float(merged.get("cleanup_min_ratio", 0.5))))
    except (TypeError, ValueError):
        merged["cleanup_min_ratio"] = 0.5
    mode = str(merged.get("schedule_mode") or "interval")
    merged["schedule_mode"] = mode if mode in {"interval", "daily"} else "interval"
    try:
        merged["schedule_interval_hours"] = max(1.0, float(merged.get("schedule_interval_hours", 24)))
    except (TypeError, ValueError):
        merged["schedule_interval_hours"] = 24.0
    try:
        merged["schedule_hour"] = max(0, min(23, int(merged.get("schedule_hour", 3))))
    except (TypeError, ValueError):
        merged["schedule_hour"] = 3
    try:
        merged["schedule_minute"] = max(0, min(59, int(merged.get("schedule_minute", 0))))
    except (TypeError, ValueError):
        merged["schedule_minute"] = 0
    return merged


def save_strm_sync_config(config: dict) -> None:
    _save_json_file(STRM_SYNC_FILE, config)


def default_strm_sync_status() -> dict:
    return {
        "running": False,
        "phase": "",
        "progress": 0.0,
        "progress_text": "",
        "movies_created": 0,
        "movies_updated": 0,
        "movies_skipped": 0,
        "movies_removed": 0,
        "movies_excluded": 0,
        "movies_unmatched": 0,
        "episodes_created": 0,
        "episodes_updated": 0,
        "episodes_skipped": 0,
        "episodes_removed": 0,
        "episodes_tmdb_filtered": 0,
        "dirs_removed": 0,
        "cleanup_skipped": False,
        "movies_errors": 0,
        "series_created": 0,
        "series_updated": 0,
        "series_excluded": 0,
        "series_unmatched": 0,
        "series_errors": 0,
        "series_from_m3u": 0,
        "series_from_api": 0,
        "series_m3u_missing": 0,
        "tmdb_lookups": 0,
        "tmdb_cache_hits": 0,
        "schedule_last_run": "",
        "schedule_next_run": "",
        "last_error": "",
        "last_sync": "",
        "movies_elapsed_sec": 0.0,
        "series_elapsed_sec": 0.0,
        "total_elapsed_sec": 0.0,
        "heartbeat_unix": 0.0,
        "heartbeat_at": "",
        "log": [],
    }


def load_strm_sync_status() -> dict:
    data = load_json_file(STRM_SYNC_STATUS_FILE, default_strm_sync_status())
    if not isinstance(data, dict):
        return default_strm_sync_status()
    merged = {**default_strm_sync_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


_TMDB_FOLDER_SUFFIX_RE = re.compile(r"\s*\[tmdbid-\d+\]\s*$", re.IGNORECASE)


def clean_strm_folder_title(folder_name: str) -> str:
    return _TMDB_FOLDER_SUFFIX_RE.sub("", folder_name).strip() or folder_name


def _newest_strm_mtime(folder: str) -> tuple[float, int]:
    """Return (newest .strm mtime, count). (0.0, 0) if none."""
    newest = 0.0
    count = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            count += 1
            path = os.path.join(root, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
    return newest, count


def list_recent_strm_titles(root: str, limit: int = 50) -> list[dict]:
    """Title folders under a STRM library root, newest .strm activity first."""
    if limit < 1 or not root or not os.path.isdir(root):
        return []
    rows: list[tuple[float, int, str, str]] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                newest, count = _newest_strm_mtime(entry.path)
                if count < 1 or newest <= 0:
                    continue
                rows.append((newest, count, entry.name, entry.path))
    except OSError:
        return []
    rows.sort(key=lambda item: item[0], reverse=True)
    out: list[dict] = []
    for mtime, count, name, _path in rows[:limit]:
        out.append(
            {
                "title": clean_strm_folder_title(name),
                "folder": name,
                "added": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                "added_ts": mtime,
                "strm_count": count,
            }
        )
    return out


def save_strm_sync_status(data: dict) -> None:
    _save_json_file(STRM_SYNC_STATUS_FILE, data)


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


@dataclass
class PrefetchSwitchDecision:
    action: str  # wait | switch_local | stay_strm
    reason: str
    buffer_seconds: float = 0.0
    ahead_seconds: float = 0.0
    position_seconds: float = 0.0
    speed_bps: float = 0.0
    bitrate_bps: float = 0.0
    speed_ratio: float = 0.0
    downloaded_bytes: int = 0
    bytes_gained: int = 0
    speed_sample_ok: bool = False


def evaluate_prefetch_switch(
    *,
    downloaded_bytes: int,
    elapsed_seconds: float,
    bitrate_bps: int | float,
    bytes_gained: int | None = None,
    position_seconds: float = 0.0,
    target_buffer_seconds: float = 120,
    min_speed_ratio: float = 1.3,
    min_bytes: int = 20 * 1024 * 1024,
    max_wait_seconds: float = 180,
    min_speed_sample_bytes: int = 5 * 1024 * 1024,
    min_speed_sample_seconds: float = 15.0,
) -> PrefetchSwitchDecision:
    """Decide whether a growing local download is safe to switch to from .strm playback.

    ``downloaded_bytes`` is the current local file size (coverage from the start of the file).
    ``bytes_gained`` is used only for speed (bytes written since this download attempt started).
    Switch requires the local file to cover *current playback position + target buffer*.
    """
    bitrate = float(bitrate_bps or 0)
    if bitrate < 500_000:
        bitrate = 1_500_000.0  # conservative fallback (~1.5 Mbps)
    elapsed = max(float(elapsed_seconds or 0), 0.5)
    downloaded = max(int(downloaded_bytes or 0), 0)
    gained = downloaded if bytes_gained is None else max(int(bytes_gained or 0), 0)
    position = max(float(position_seconds or 0), 0.0)
    target = max(float(target_buffer_seconds or 0), 1.0)

    speed_bps = gained * 8.0 / elapsed
    ratio = speed_bps / bitrate if bitrate > 0 else 0.0
    covered_seconds = downloaded * 8.0 / bitrate
    ahead_seconds = covered_seconds - position
    need_seconds = position + target
    speed_sample_ok = gained >= int(min_speed_sample_bytes) or (
        elapsed >= float(min_speed_sample_seconds) and gained >= 1 * 1024 * 1024
    )

    base = dict(
        buffer_seconds=covered_seconds,
        ahead_seconds=ahead_seconds,
        position_seconds=position,
        speed_bps=speed_bps,
        bitrate_bps=bitrate,
        speed_ratio=ratio,
        downloaded_bytes=downloaded,
        bytes_gained=gained,
        speed_sample_ok=speed_sample_ok,
    )

    # Local file must cover playhead + target buffer (from byte 0 of the file).
    covers_playback = (
        covered_seconds >= need_seconds
        and ahead_seconds >= target
        and downloaded >= int(min_bytes)
    )

    if covers_playback and speed_sample_ok and ratio >= float(min_speed_ratio):
        return PrefetchSwitchDecision(
            action="switch_local",
            reason=(
                f"locale copre pos {position:.0f}s + {target:.0f}s "
                f"(ahead {ahead_seconds:.0f}s) e download {ratio:.2f}× bitrate"
            ),
            **base,
        )

    if covers_playback and not speed_sample_ok:
        # File already covers the playhead (e.g. nearly complete / fast catch-up).
        return PrefetchSwitchDecision(
            action="switch_local",
            reason=(
                f"locale già oltre la posizione ({covered_seconds:.0f}s ≥ {need_seconds:.0f}s); "
                "switch senza attendere campione velocità"
            ),
            **base,
        )

    if elapsed < float(max_wait_seconds):
        if not speed_sample_ok:
            return PrefetchSwitchDecision(
                action="wait",
                reason=(
                    f"campione velocità insufficiente "
                    f"(guadagnati {gained / (1024 * 1024):.1f}MB in {elapsed:.0f}s)"
                ),
                **base,
            )
        return PrefetchSwitchDecision(
            action="wait",
            reason=(
                f"in attesa copertura "
                f"(locale {covered_seconds:.0f}s / serve {need_seconds:.0f}s, "
                f"ahead {ahead_seconds:.0f}s, velocità {ratio:.2f}×)"
            ),
            **base,
        )

    # Max wait reached.
    if covers_playback and ratio >= float(min_speed_ratio):
        return PrefetchSwitchDecision(
            action="switch_local",
            reason=(
                f"attesa max ma locale ok (ahead {ahead_seconds:.0f}s, {ratio:.2f}×)"
            ),
            **base,
        )

    if not speed_sample_ok and gained <= 0:
        # Still connecting / writing to a temp name — do not lock stay_strm.
        return PrefetchSwitchDecision(
            action="wait",
            reason=(
                "nessun byte utile ancora su disco "
                f"dopo {elapsed:.0f}s — continuo ad attendere"
            ),
            **base,
        )

    if speed_sample_ok and ratio < float(min_speed_ratio):
        return PrefetchSwitchDecision(
            action="stay_strm",
            reason=(
                f"download troppo lento ({ratio:.2f}× bitrate, "
                f"ahead {ahead_seconds:.0f}s / serve {target:.0f}s) "
                "— meglio restare sullo .strm"
            ),
            **base,
        )

    # Still catching up to playhead but not clearly too slow: keep waiting
    # (caller may keep evaluating; do not lock stay_strm prematurely).
    return PrefetchSwitchDecision(
        action="wait",
        reason=(
            f"attesa max ma ancora in catch-up verso pos {position:.0f}s "
            f"(locale {covered_seconds:.0f}s, {ratio:.2f}×) — continuo a scaricare"
        ),
        **base,
    )


def run_ytdlp(
    url: str,
    output_path: str,
    progress_callback=None,
    label: str = "",
    should_cancel=None,
    resume: bool = False,
    history_entry: dict | None = None,
    strm_path: str | None = None,
    delete_strm_on_success: bool = True,
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
        # Write into the final path so size/progress and mid-play switch see real bytes
        # (default .part files are invisible to size checks and to the media library).
        "--no-part",
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
    if delete_strm_on_success:
        finalize_after_local_download(
            output_path, strm_path=strm_path, strm_url=url
        )
    return True

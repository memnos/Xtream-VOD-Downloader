"""Continue auto-downloading new episodes for series that already have local files.

After strm sync (or on a periodic watcher scan), find .strm episodes that are
newer than the latest local download for each series under /download/tv and
queue them for the auto-download watcher via a JSON file (cross-process).
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime

from core import (
    DATA_DIR,
    EPISODE_TAG_RE,
    SERIES_DOWNLOAD_PATHS,
    STRM_OUTPUT_SERIES_PATH,
    VIDEO_EXTENSIONS,
    find_strm_folder_match,
    load_auto_download_config,
    load_json_file,
    load_strm_sync_config,
    local_download_exists_for_strm,
    parse_episode_numbers_from_path,
)

PENDING_AUTO_DOWNLOADS_FILE = os.environ.get(
    "PENDING_AUTO_DOWNLOADS_FILE",
    os.path.join(DATA_DIR, "pending_auto_downloads.json"),
)

_TMDB_SUFFIX_RE = re.compile(r"\s*\[tmdbid-\d+\]\s*$", re.IGNORECASE)
_pending_lock = threading.Lock()


def _default_pending() -> dict:
    return {"items": []}


def load_pending_auto_downloads() -> dict:
    data = load_json_file(PENDING_AUTO_DOWNLOADS_FILE, _default_pending())
    if not isinstance(data, dict):
        return _default_pending()
    items = data.get("items", [])
    return {"items": items if isinstance(items, list) else []}


def save_pending_auto_downloads(data: dict) -> None:
    import json

    path = PENDING_AUTO_DOWNLOADS_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _item_key(item: dict) -> str:
    return (
        f"{item.get('series_name')}:{int(item.get('season') or 0)}:"
        f"{int(item.get('episode') or 0)}:{item.get('strm_path') or ''}"
    )


def enqueue_pending_auto_downloads(items: list[dict]) -> int:
    """Merge new episode download requests into the pending file. Returns added count."""
    if not items:
        return 0
    with _pending_lock:
        data = load_pending_auto_downloads()
        existing = {_item_key(it): it for it in data.get("items", []) if isinstance(it, dict)}
        added = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            try:
                season = int(raw.get("season"))
                episode = int(raw.get("episode"))
            except (TypeError, ValueError):
                continue
            series_name = str(raw.get("series_name") or "").strip()
            strm_path = str(raw.get("strm_path") or "").strip()
            if not series_name or season < 1 or episode < 1:
                continue
            entry = {
                "series_name": series_name,
                "season": season,
                "episode": episode,
                "strm_path": strm_path,
                "label": str(raw.get("label") or f"{series_name} S{season:02d}E{episode:02d}"),
                "queued_at": datetime.now().isoformat(timespec="seconds"),
            }
            key = _item_key(entry)
            if key in existing:
                continue
            existing[key] = entry
            added += 1
        data["items"] = list(existing.values())
        save_pending_auto_downloads(data)
        return added


def take_pending_auto_downloads() -> list[dict]:
    """Atomically read and clear the pending download file."""
    with _pending_lock:
        data = load_pending_auto_downloads()
        items = list(data.get("items") or [])
        save_pending_auto_downloads(_default_pending())
        return items


def continue_download_enabled(config: dict | None = None) -> bool:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    return bool(cfg.get("enabled")) and bool(cfg.get("continue_download_incomplete", True))


def iter_downloaded_series_folders(
    download_roots: tuple[str, ...] | list[str] | None = None,
) -> list[str]:
    """Series folders under download roots that still contain local video files."""
    roots = list(download_roots or SERIES_DOWNLOAD_PATHS)
    found: list[str] = []
    seen: set[str] = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir(follow_symlinks=True):
                continue
            real = os.path.realpath(entry.path)
            if real in seen:
                continue
            if _folder_has_videos(entry.path):
                seen.add(real)
                found.append(entry.path)
    return found


def _folder_has_videos(folder: str) -> bool:
    if not os.path.isdir(folder):
        return False
    for dirpath, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                return True
    return False


def local_episode_watermark(series_folder: str) -> tuple[int, int] | None:
    """Highest (season, episode) among local video files in the series folder."""
    best: tuple[int, int] | None = None
    if not os.path.isdir(series_folder):
        return None
    for dirpath, _dirs, files in os.walk(series_folder):
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            nums = parse_episode_numbers_from_path(os.path.join(dirpath, name))
            if not nums:
                continue
            if best is None or nums > best:
                best = nums
    return best


def _series_display_name(folder_name: str) -> str:
    name = _TMDB_SUFFIX_RE.sub("", folder_name or "").strip()
    return name or folder_name


def find_newer_strm_episodes_for_series(
    series_folder: str,
    *,
    strm_root: str | None = None,
) -> list[dict]:
    """Return strm episodes newer than the local watermark, without a local file yet."""
    watermark = local_episode_watermark(series_folder)
    if watermark is None:
        return []

    folder_name = os.path.basename(os.path.realpath(series_folder))
    series_name = _series_display_name(folder_name)
    root = strm_root
    if not root:
        cfg = load_strm_sync_config()
        root = str(cfg.get("series_output") or STRM_OUTPUT_SERIES_PATH).strip() or STRM_OUTPUT_SERIES_PATH

    match = find_strm_folder_match(root, folder_name) or find_strm_folder_match(root, series_name)
    if not match:
        candidate = os.path.join(root, folder_name)
        if os.path.isdir(candidate):
            match = folder_name
        else:
            return []
    strm_series = os.path.join(root, match)
    if not os.path.isdir(strm_series):
        return []

    results: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for dirpath, _dirs, files in os.walk(strm_series):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            path = os.path.join(dirpath, name)
            tag = EPISODE_TAG_RE.search(name)
            if not tag:
                continue
            try:
                season = int(tag.group(1))
                episode = int(tag.group(2))
            except ValueError:
                continue
            key = (season, episode)
            if key in seen:
                continue
            if key <= watermark:
                continue
            if local_download_exists_for_strm(path):
                continue
            seen.add(key)
            results.append(
                {
                    "series_name": series_name,
                    "season": season,
                    "episode": episode,
                    "strm_path": path,
                    "label": f"{series_name} S{season:02d}E{episode:02d}",
                    "series_folder": series_folder,
                }
            )
    results.sort(key=lambda item: (item["season"], item["episode"]))
    return results


def scan_and_enqueue_continue_downloads(
    *,
    download_roots: tuple[str, ...] | list[str] | None = None,
    strm_root: str | None = None,
    config: dict | None = None,
) -> dict:
    """Scan downloaded series for newer .strm episodes and enqueue them.

    Returns ``{"series": N, "episodes": M, "queued": K}``.
    """
    if not continue_download_enabled(config):
        return {"series": 0, "episodes": 0, "queued": 0, "skipped": True}

    folders = iter_downloaded_series_folders(download_roots)
    all_items: list[dict] = []
    for folder in folders:
        all_items.extend(
            find_newer_strm_episodes_for_series(folder, strm_root=strm_root)
        )
    queued = enqueue_pending_auto_downloads(all_items)
    return {
        "series": len(folders),
        "episodes": len(all_items),
        "queued": queued,
        "skipped": False,
    }

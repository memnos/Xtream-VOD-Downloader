"""Analyze STRM seasons that are complete and watched on Jellyfin.

Phase 1 only: for series with at least one JF played episode, check TMDB
completeness of those seasons and merge into a persistent history. After each
STRM sync, newly completed seasons are appended; existing history is kept.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime

from core import (
    DATA_DIR,
    EPISODE_TAG_RE,
    SEASON_DIR_RE,
    STRM_OUTPUT_SERIES_PATH,
    _save_json_file,
    clean_strm_folder_title,
    load_auto_download_config,
    load_json_file,
    load_strm_sync_config,
)
from emby_watcher import MediaServerClient
from tmdb import TmdbClient

SEASON_STATUS_FILE = os.environ.get(
    "STRM_SEASON_STATUS_FILE", os.path.join(DATA_DIR, "strm_season_status.json")
)
TOUCHED_SEASONS_FILE = os.environ.get(
    "STRM_TOUCHED_SEASONS_FILE", os.path.join(DATA_DIR, "strm_touched_seasons.json")
)
TMDB_ID_RE = re.compile(r"\[tmdbid-(\d+)\]", re.IGNORECASE)

_analysis_lock = threading.Lock()
_touched_lock = threading.Lock()
_analysis_thread: threading.Thread | None = None


def default_season_status() -> dict:
    return {
        "running": False,
        "last_error": "",
        "updated_at": "",
        "series_scanned": 0,
        "seasons_scanned": 0,
        "candidates_checked": 0,
        "touched_seasons_checked": 0,
        "complete_watched_seasons": 0,
        "newly_added_count": 0,
        "completed_by_new_count": 0,
        "newly_completed_by_new_count": 0,
        "tmdb_lookups": 0,
        "tmdb_cache_hits": 0,
        "jf_played_episodes": 0,
        "watched_complete_seasons": [],
        "newly_added": [],
        "completed_by_new_episodes": [],
        "newly_completed_by_new_episodes": [],
        "log": [],
    }


def load_season_status() -> dict:
    data = load_json_file(SEASON_STATUS_FILE, default_season_status())
    if not isinstance(data, dict):
        return default_season_status()
    merged = {**default_season_status(), **data}
    for key in (
        "watched_complete_seasons",
        "newly_added",
        "completed_by_new_episodes",
        "newly_completed_by_new_episodes",
        "log",
    ):
        value = merged.get(key, [])
        merged[key] = value if isinstance(value, list) else []
    return merged


def save_season_status(data: dict) -> None:
    _save_json_file(SEASON_STATUS_FILE, data)


def record_touched_season(folder: str, season: int) -> None:
    """Remember a season that received a newly created .strm during sync."""
    folder = str(folder or "").strip()
    try:
        season_num = int(season)
    except (TypeError, ValueError):
        return
    if not folder or season_num < 1:
        return
    with _touched_lock:
        data = load_json_file(TOUCHED_SEASONS_FILE, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        key = f"{folder.lower()}::S{season_num}"
        existing = {
            f"{str(it.get('folder') or '').lower()}::S{int(it.get('season') or 0)}"
            for it in items
            if isinstance(it, dict)
        }
        if key not in existing:
            items.append({"folder": folder, "season": season_num})
        _save_json_file(TOUCHED_SEASONS_FILE, {"items": items})


def record_touched_season_from_strm(strm_path: str) -> None:
    from core import parse_episode_numbers_from_path, series_folder_from_strm_path

    folder = series_folder_from_strm_path(strm_path)
    nums = parse_episode_numbers_from_path(strm_path)
    if not folder or not nums:
        return
    record_touched_season(folder, nums[0])


def load_and_clear_touched_seasons() -> list[dict]:
    with _touched_lock:
        data = load_json_file(TOUCHED_SEASONS_FILE, {"items": []})
        items = data.get("items", []) if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        cleaned = []
        seen: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            folder = str(item.get("folder") or "").strip()
            try:
                season = int(item.get("season") or 0)
            except (TypeError, ValueError):
                continue
            if not folder or season < 1:
                continue
            key = f"{folder.lower()}::S{season}"
            if key in seen:
                continue
            seen.add(key)
            cleaned.append({"folder": folder, "season": season})
        _save_json_file(TOUCHED_SEASONS_FILE, {"items": []})
        return cleaned


def extract_tmdb_id(text: str) -> int | None:
    match = TMDB_ID_RE.search(text or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def season_history_key(row: dict) -> str:
    tmdb_id = row.get("tmdb_id")
    season = int(row.get("season") or 0)
    if tmdb_id:
        return f"tmdb:{int(tmdb_id)}:S{season}"
    title = str(row.get("title") or "").strip().lower()
    return f"title:{title}:S{season}"


def _title_season_key(row: dict) -> str:
    title = str(row.get("title") or "").strip().lower()
    season = int(row.get("season") or 0)
    return f"title:{title}:S{season}"


def season_is_complete(present: set[int], expected: int | None) -> bool:
    """True if episodes 1..expected are all present (or gapless 1..max as fallback)."""
    episodes = {int(ep) for ep in present if str(ep).isdigit()}
    episodes = {ep for ep in episodes if ep > 0}
    if not episodes:
        return False
    if expected and int(expected) > 0:
        needed = set(range(1, int(expected) + 1))
        return needed.issubset(episodes)
    maximum = max(episodes)
    return episodes >= set(range(1, maximum + 1))


def scan_strm_series_seasons(root: str) -> list[dict]:
    """Scan title folders → seasons → episode numbers and newest strm mtime."""
    if not root or not os.path.isdir(root):
        return []
    series_rows: list[dict] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return []

    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        seasons: dict[int, dict] = {}
        try:
            children = list(os.scandir(entry.path))
        except OSError:
            continue
        for child in children:
            if not child.is_dir(follow_symlinks=False):
                continue
            season_match = SEASON_DIR_RE.match(child.name)
            if not season_match:
                continue
            season_num = int(season_match.group(1))
            if season_num < 1:
                continue
            episode_nums: set[int] = set()
            newest = 0.0
            try:
                for root_dir, _dirs, files in os.walk(child.path):
                    for name in files:
                        if not name.lower().endswith(".strm"):
                            continue
                        path = os.path.join(root_dir, name)
                        try:
                            mtime = os.path.getmtime(path)
                        except OSError:
                            mtime = 0.0
                        if mtime > newest:
                            newest = mtime
                        tag = EPISODE_TAG_RE.search(name)
                        if tag:
                            try:
                                episode_nums.add(int(tag.group(2)))
                            except ValueError:
                                pass
            except OSError:
                continue
            if not episode_nums:
                continue
            seasons[season_num] = {
                "episodes": sorted(episode_nums),
                "episode_count": len(episode_nums),
                "newest_ts": newest,
                "newest": (
                    datetime.fromtimestamp(newest).strftime("%Y-%m-%d %H:%M")
                    if newest > 0
                    else ""
                ),
            }
        if not seasons:
            continue
        series_rows.append(
            {
                "folder": entry.name,
                "title": clean_strm_folder_title(entry.name),
                "tmdb_id": extract_tmdb_id(entry.name),
                "seasons": seasons,
            }
        )
    return series_rows


def _build_tmdb_client(strm_config: dict | None = None) -> TmdbClient | None:
    config = strm_config if isinstance(strm_config, dict) else load_strm_sync_config()
    api_key = str(config.get("tmdb_api_key") or "").strip() or os.environ.get(
        "TMDB_API_KEY", ""
    ).strip()
    if not api_key:
        return None
    return TmdbClient(
        api_key,
        language=str(config.get("tmdb_language") or "it-IT"),
        rate_limit=int(config.get("tmdb_rate_limit") or 40),
    )


def _build_jellyfin_client(
    auto_config: dict | None = None,
) -> tuple[MediaServerClient | None, str]:
    config = auto_config if isinstance(auto_config, dict) else load_auto_download_config()
    if not config.get("jellyfin_enabled"):
        return None, ""
    url = str(config.get("jellyfin_url") or "").strip()
    api_key = str(config.get("jellyfin_api_key") or "").strip()
    username = str(config.get("jellyfin_username") or "").strip()
    if not (url and api_key and username):
        return None, ""
    client = MediaServerClient(url, api_key, server_type="jellyfin")
    try:
        user_id = client.resolve_user_id(username) or ""
    except Exception:
        return None, ""
    if not user_id:
        return None, ""
    return client, user_id


def _played_season_keys(played_items: list) -> set[tuple[int | None, str, int]]:
    """Keys for seasons with ≥1 played episode: (tmdb_id, folder_hint, season)."""
    keys: set[tuple[int | None, str, int]] = set()
    for item in played_items:
        season = item.get("ParentIndexNumber")
        if season is None:
            continue
        try:
            season_num = int(season)
        except (TypeError, ValueError):
            continue
        if season_num < 1:
            continue
        path = str(item.get("Path") or "")
        tmdb_id = extract_tmdb_id(path)
        folder_hint = ""
        if path:
            parts = path.replace("\\", "/").split("/")
            for idx, part in enumerate(parts):
                if SEASON_DIR_RE.match(part) and idx > 0:
                    folder_hint = parts[idx - 1]
                    break
        if not folder_hint:
            folder_hint = str(item.get("SeriesName") or "").strip()
        keys.add((tmdb_id, folder_hint.lower(), season_num))
    return keys


def _season_was_watched(
    played_keys: set[tuple[int | None, str, int]],
    tmdb_id: int | None,
    folder: str,
    title: str,
    season_num: int,
) -> bool:
    folder_l = folder.lower()
    title_l = title.lower()
    for played_tmdb, hint, played_season in played_keys:
        if played_season != season_num:
            continue
        if tmdb_id and played_tmdb and played_tmdb == tmdb_id:
            return True
        if hint and (
            hint == folder_l or hint == title_l or hint in folder_l or title_l in hint
        ):
            return True
    return False


def _merge_watched_history(
    previous: list[dict], current: list[dict], now: str
) -> tuple[list[dict], list[dict]]:
    """Merge newly found complete+watched seasons into persistent history.

    Returns (merged_history newest-first, newly_added this run).
    Matches by tmdb_id+season when possible, else title+season.
    """
    by_primary: dict[str, dict] = {}
    by_title: dict[str, str] = {}

    for row in previous:
        if not isinstance(row, dict):
            continue
        primary = season_history_key(row)
        entry = dict(row)
        entry.setdefault("first_seen", entry.get("updated") or now)
        existing = by_primary.get(primary)
        if existing is None or (entry.get("tmdb_id") and not existing.get("tmdb_id")):
            by_primary[primary] = entry
        by_title.setdefault(_title_season_key(entry), primary)

    newly_added: list[dict] = []
    for row in current:
        primary = season_history_key(row)
        title_key = _title_season_key(row)
        existing_key = primary if primary in by_primary else by_title.get(title_key)
        existing = by_primary.get(existing_key) if existing_key else None

        if existing is None:
            entry = {
                "title": row.get("title", ""),
                "tmdb_id": row.get("tmdb_id"),
                "season": int(row.get("season") or 0),
                "episodes": int(row.get("episodes") or 0),
                "expected": int(row.get("expected") or 0),
                "updated": row.get("updated") or now,
                "first_seen": now,
            }
            by_primary[primary] = entry
            by_title[title_key] = primary
            newly_added.append(dict(entry))
            continue

        existing["title"] = row.get("title") or existing.get("title", "")
        if row.get("tmdb_id"):
            existing["tmdb_id"] = row.get("tmdb_id")
        existing["season"] = int(row.get("season") or existing.get("season") or 0)
        existing["episodes"] = int(row.get("episodes") or existing.get("episodes") or 0)
        existing["expected"] = int(row.get("expected") or existing.get("expected") or 0)
        if row.get("updated"):
            existing["updated"] = row["updated"]
        existing.setdefault("first_seen", existing.get("updated") or now)

        new_primary = season_history_key(existing)
        if existing_key != new_primary:
            by_primary.pop(existing_key, None)
        by_primary[new_primary] = existing
        by_title[title_key] = new_primary

    collapsed: dict[str, dict] = {}
    for entry in by_primary.values():
        title_key = _title_season_key(entry)
        other = collapsed.get(title_key)
        if other is None:
            collapsed[title_key] = entry
            continue
        prefer = entry if entry.get("tmdb_id") and not other.get("tmdb_id") else other
        firsts = [
            str(prefer.get("first_seen") or ""),
            str(entry.get("first_seen") or ""),
            str(other.get("first_seen") or ""),
        ]
        firsts = [f for f in firsts if f]
        if firsts:
            prefer["first_seen"] = min(firsts)
        if entry.get("tmdb_id"):
            prefer["tmdb_id"] = entry["tmdb_id"]
        elif other.get("tmdb_id"):
            prefer["tmdb_id"] = other["tmdb_id"]
        prefer["episodes"] = max(
            int(prefer.get("episodes") or 0),
            int(entry.get("episodes") or 0),
            int(other.get("episodes") or 0),
        )
        prefer["expected"] = max(
            int(prefer.get("expected") or 0),
            int(entry.get("expected") or 0),
            int(other.get("expected") or 0),
        )
        prefer["updated"] = max(
            str(prefer.get("updated") or ""),
            str(entry.get("updated") or ""),
            str(other.get("updated") or ""),
        )
        collapsed[title_key] = prefer

    merged = list(collapsed.values())

    def _sort_key(item: dict) -> tuple:
        return (
            str(item.get("first_seen") or ""),
            str(item.get("updated") or ""),
            str(item.get("title") or ""),
            int(item.get("season") or 0),
        )

    merged.sort(key=_sort_key, reverse=True)
    newly_added.sort(key=_sort_key, reverse=True)
    return merged, newly_added


def analyze_complete_seasons(
    series_root: str | None = None,
    *,
    strm_config: dict | None = None,
    auto_config: dict | None = None,
    progress_cb=None,
) -> dict:
    """Phase 1: JF watched complete seasons → history.

    Phase 2: only seasons that received newly created .strm files; if a season
    becomes complete, append it to completed_by_new_episodes (separate list).
    """
    previous = load_season_status()
    previous_history = list(previous.get("watched_complete_seasons") or [])
    previous_by_new = list(previous.get("completed_by_new_episodes") or [])

    status = default_season_status()
    status["running"] = True
    status["watched_complete_seasons"] = previous_history
    status["complete_watched_seasons"] = len(previous_history)
    status["completed_by_new_episodes"] = previous_by_new
    status["completed_by_new_count"] = len(previous_by_new)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status["updated_at"] = now
    save_season_status(status)

    def _log(msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        status["log"] = ([f"[{stamp}] {msg}"] + list(status.get("log") or []))[:40]
        if progress_cb:
            progress_cb(msg)
        save_season_status(status)

    strm_config = strm_config if isinstance(strm_config, dict) else load_strm_sync_config()
    auto_config = (
        auto_config if isinstance(auto_config, dict) else load_auto_download_config()
    )
    root = (
        (series_root or strm_config.get("series_output") or STRM_OUTPUT_SERIES_PATH)
        or ""
    ).strip()

    tmdb_client = _build_tmdb_client(strm_config)
    jf_client, user_id = _build_jellyfin_client(auto_config)

    try:
        _log(f"Scanning series library: {root}")
        library = scan_strm_series_seasons(root)
        status["series_scanned"] = len(library)
        status["seasons_scanned"] = sum(len(row["seasons"]) for row in library)
        library_by_folder = {row["folder"]: row for row in library}
        _log(
            f"Found {status['series_scanned']} series / {status['seasons_scanned']} seasons"
        )

        played_keys: set[tuple[int | None, str, int]] = set()
        if jf_client and user_id:
            _log("Loading played episodes from Jellyfin...")
            try:
                played = jf_client.get_played_episodes(user_id)
                status["jf_played_episodes"] = len(played)
                played_keys = _played_season_keys(played)
                _log(
                    f"Jellyfin: {status['jf_played_episodes']} played episodes, "
                    f"{len(played_keys)} season keys"
                )
            except Exception as exc:
                status["last_error"] = f"Jellyfin: {exc}"
                _log(status["last_error"])
        else:
            _log("Jellyfin not configured — no watched seasons to check")

        expected_cache: dict[int, dict[int, int]] = {}

        def _expected_for(row: dict) -> dict[int, int]:
            tmdb_id = row.get("tmdb_id")
            if not tmdb_client or not tmdb_id:
                return {}
            tid = int(tmdb_id)
            if tid not in expected_cache:
                expected_cache[tid] = tmdb_client.get_tv_season_episode_counts(tid) or {}
            return expected_cache[tid]

        def _season_entry(row: dict, season_num: int) -> dict | None:
            info = row["seasons"].get(int(season_num))
            if not info:
                return None
            expected_by_season = _expected_for(row)
            expected = expected_by_season.get(int(season_num))
            present = set(info["episodes"])
            if not expected or not season_is_complete(present, expected):
                return None
            return {
                "title": row["title"],
                "tmdb_id": row.get("tmdb_id"),
                "season": int(season_num),
                "episodes": int(info["episode_count"]),
                "expected": int(expected),
                "updated": info.get("newest") or "",
            }

        def _complete_seasons_for(row: dict) -> list[dict]:
            rows: list[dict] = []
            for season_num in sorted(row["seasons"]):
                entry = _season_entry(row, int(season_num))
                if entry:
                    rows.append(entry)
            return rows

        # --- Phase 1: watched on JF → cumulative history ---
        watched_tmdb_ids = {tid for tid, _hint, _season in played_keys if tid}
        watched_candidates = [
            row
            for row in library
            if (row.get("tmdb_id") and int(row["tmdb_id"]) in watched_tmdb_ids)
            or any(
                _season_was_watched(
                    played_keys,
                    row.get("tmdb_id"),
                    row["folder"],
                    row["title"],
                    int(season_num),
                )
                for season_num in row["seasons"]
            )
        ]
        status["candidates_checked"] = len(watched_candidates)
        _log(
            f"Phase 1: {len(watched_candidates)} series with JF watch "
            f"(history: {len(previous_history)})"
        )

        found_now: list[dict] = []
        for index, row in enumerate(watched_candidates, start=1):
            for entry in _complete_seasons_for(row):
                if _season_was_watched(
                    played_keys,
                    row.get("tmdb_id"),
                    row["folder"],
                    row["title"],
                    int(entry["season"]),
                ):
                    found_now.append(entry)
            if index % 25 == 0 and tmdb_client is not None:
                status["tmdb_lookups"] = tmdb_client.lookups
                status["tmdb_cache_hits"] = tmdb_client.cache_hits
                save_season_status(status)

        merged, newly_added = _merge_watched_history(previous_history, found_now, now)
        status["watched_complete_seasons"] = merged
        status["complete_watched_seasons"] = len(merged)
        status["newly_added"] = newly_added
        status["newly_added_count"] = len(newly_added)
        _log(
            f"Phase 1 done: +{len(newly_added)} new · {len(merged)} in JF history"
        )
        save_season_status(status)

        # --- Phase 2: only seasons touched by newly created .strm ---
        touched = load_and_clear_touched_seasons()
        status["touched_seasons_checked"] = len(touched)
        found_by_new: list[dict] = []
        if not touched:
            _log("Phase 2: no newly created episodes — skip")
            status["newly_completed_by_new_episodes"] = []
            status["newly_completed_by_new_count"] = 0
        else:
            _log(f"Phase 2: checking {len(touched)} seasons with new episodes")
            for item in touched:
                folder = item["folder"]
                season_num = int(item["season"])
                row = library_by_folder.get(folder)
                if row is None:
                    # Folder name from sync may differ slightly; try case-insensitive.
                    row = next(
                        (
                            candidate
                            for candidate in library
                            if candidate["folder"].lower() == folder.lower()
                        ),
                        None,
                    )
                if row is None:
                    continue
                entry = _season_entry(row, season_num)
                if entry:
                    found_by_new.append(entry)

            merged_new, newly_by_new = _merge_watched_history(
                previous_by_new, found_by_new, now
            )
            status["completed_by_new_episodes"] = merged_new
            status["completed_by_new_count"] = len(merged_new)
            status["newly_completed_by_new_episodes"] = newly_by_new
            status["newly_completed_by_new_count"] = len(newly_by_new)
            _log(
                f"Phase 2 done: {len(found_by_new)} complete among touched · "
                f"+{len(newly_by_new)} new · {len(merged_new)} in new-eps list"
            )

        if tmdb_client is not None:
            status["tmdb_lookups"] = tmdb_client.lookups
            status["tmdb_cache_hits"] = tmdb_client.cache_hits
            tmdb_client.save_cache()

        if not status.get("last_error"):
            status["last_error"] = ""
        _log(
            f"Done: JF history {status['complete_watched_seasons']} "
            f"(+{status['newly_added_count']}) · "
            f"new-eps complete {status['completed_by_new_count']} "
            f"(+{status['newly_completed_by_new_count']})"
        )
    except Exception as exc:
        status["last_error"] = str(exc)
        _log(f"Error: {exc}")
        status["watched_complete_seasons"] = previous_history
        status["complete_watched_seasons"] = len(previous_history)
        status["completed_by_new_episodes"] = previous_by_new
        status["completed_by_new_count"] = len(previous_by_new)
    finally:
        status["running"] = False
        status["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_season_status(status)
    return status


def is_season_analysis_running() -> bool:
    status = load_season_status()
    if not status.get("running"):
        return False
    with _analysis_lock:
        if _analysis_thread is not None and _analysis_thread.is_alive():
            return True
    return bool(status.get("running"))


def start_season_analysis(
    series_root: str | None = None,
    *,
    strm_config: dict | None = None,
    auto_config: dict | None = None,
) -> bool:
    global _analysis_thread
    with _analysis_lock:
        if _analysis_thread is not None and _analysis_thread.is_alive():
            return False
        status = load_season_status()
        # Recover from a previous process that died while running=True.
        if status.get("running"):
            status["running"] = False
            status["last_error"] = "Previous analysis interrupted"
            save_season_status(status)

        def _worker() -> None:
            try:
                analyze_complete_seasons(
                    series_root, strm_config=strm_config, auto_config=auto_config
                )
            finally:
                global _analysis_thread
                with _analysis_lock:
                    _analysis_thread = None

        _analysis_thread = threading.Thread(
            target=_worker, name="strm-season-analysis", daemon=True
        )
        _analysis_thread.start()
        return True

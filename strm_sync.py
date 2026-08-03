"""Generate Jellyfin-ready .strm libraries from the Xtream API.

Supports optional TMDB matching (for clean Jellyfin naming), title/term
exclusions and automatic adult-content filtering. TMDB lookups are cached in
.data/tmdb_cache.json so a library generated into a test directory can later be
promoted to the working directory without redoing the matching.
"""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from urllib.parse import quote, urlparse

import requests

from core import (
    build_episode_strm_path,
    build_episode_strm_path_tmdb,
    build_movie_strm_path,
    build_movie_strm_path_tmdb,
    build_episode_stream_url,
    build_movie_stream_url,
    default_strm_sync_status,
    exclude_hidden_items,
    format_elapsed_seconds,
    get_series_info,
    group_catalog_versions,
    is_adult_category,
    item_file_size_bytes,
    load_auto_download_config,
    load_strm_sync_config,
    load_strm_sync_status,
    local_download_exists_for_strm,
    normalize_episodes_map,
    probe_stream_media_info,
    read_strm_url,
    request_xtream_api,
    save_strm_sync_status,
    title_matches_terms,
    write_strm,
)
from tmdb import TmdbClient, clean_title

_sync_lock = threading.Lock()
_sync_thread: threading.Thread | None = None
M3U_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
M3U_SERIES_URL_RE = re.compile(
    r"/series/[^/]+/[^/]+/(\d+)\.([A-Za-z0-9]+)(?:[?#].*)?$",
    re.IGNORECASE,
)
EPISODE_PATTERNS = (
    re.compile(r"(.+?)\b[Ss](\d{1,2})\s*[Ee]\s*(\d{1,3})\b"),
    re.compile(r"(.+?)\b(\d{1,2})\s*x\s*(\d{1,3})\b", re.IGNORECASE),
    re.compile(
        r"(.+?)\b(?:season|stagione)\s*(\d{1,2}).*?\b(?:episode|episodio|ep)\s*(\d{1,3})\b",
        re.IGNORECASE,
    ),
)


def _append_log(status: dict, message: str, *, limit: int = 80) -> None:
    log = status.setdefault("log", [])
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {message}")
    status["log"] = log[-limit:]


def _append_sync_summary(status: dict, config: dict) -> None:
    """Write a final count + timing summary split by content type."""
    lines: list[str] = ["--- Sync summary ---"]
    if config.get("sync_movies"):
        duration = format_elapsed_seconds(status.get("movies_elapsed_sec", 0))
        lines.append(
            f"Movies ({duration}): "
            f"{status['movies_created']} created, "
            f"{status['movies_updated']} updated, "
            f"{status['movies_skipped']} skipped, "
            f"{status['movies_excluded']} excluded, "
            f"{status['movies_unmatched']} unmatched, "
            f"{status['movies_errors']} errors"
        )
        if status.get("movies_removed"):
            lines.append(f"  removed: {status['movies_removed']}")
        if status.get("dirs_removed") and not config.get("sync_series"):
            lines.append(f"  empty dirs removed: {status['dirs_removed']}")
    if config.get("sync_series"):
        duration = format_elapsed_seconds(status.get("series_elapsed_sec", 0))
        lines.append(
            f"Series ({duration}): "
            f"{status.get('series_created', 0)} series created, "
            f"{status.get('series_updated', 0)} series updated, "
            f"{status['episodes_created']} episodes created, "
            f"{status['episodes_updated']} updated, "
            f"{status['episodes_skipped']} skipped, "
            f"{status['series_excluded']} series excluded, "
            f"{status['series_unmatched']} unmatched, "
            f"{status['series_errors']} errors"
        )
        if status.get("episodes_tmdb_filtered"):
            lines.append(
                f"  TMDB filter: {status['episodes_tmdb_filtered']} phantom episodes skipped/removed"
            )
        if status.get("episodes_removed"):
            lines.append(f"  removed: {status['episodes_removed']} episodes")
        if status.get("dirs_removed"):
            lines.append(f"  empty dirs removed: {status['dirs_removed']}")
    total = format_elapsed_seconds(status.get("total_elapsed_sec", 0))
    lines.append(f"Total time: {total}")
    for line in lines:
        _append_log(status, line)


def _save_status(status: dict) -> None:
    save_strm_sync_status(status)


def _fetch_categories(host: str, user: str, password: str, action: str) -> dict[str, str]:
    data = request_xtream_api(
        host,
        {"username": user, "password": password, "action": action},
        timeout=30,
    )
    if not isinstance(data, list):
        return {}
    return {
        str(c.get("category_id")): str(c.get("category_name") or "")
        for c in data
        if c.get("category_id") is not None
    }


def _fetch_vod_streams(
    host: str,
    user: str,
    password: str,
    category_id: str | None,
) -> list[dict]:
    params: dict = {
        "username": user,
        "password": password,
        "action": "get_vod_streams",
    }
    if category_id is not None:
        params["category_id"] = category_id
    timeout = 180 if category_id is None else 90
    data = request_xtream_api(host, params, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError("invalid_vod_catalog")
    return data


def _fetch_series_catalog(
    host: str,
    user: str,
    password: str,
    category_id: str | None,
) -> list[dict]:
    params: dict = {
        "username": user,
        "password": password,
        "action": "get_series",
    }
    if category_id is not None:
        params["category_id"] = category_id
    timeout = 180 if category_id is None else 90
    data = request_xtream_api(host, params, timeout=timeout)
    if not isinstance(data, list):
        raise RuntimeError("invalid_series_catalog")
    return data


def _m3u_url(host: str, user: str, password: str) -> str:
    return (
        f"{host.rstrip('/')}/get.php?username={quote(user, safe='')}"
        f"&password={quote(password, safe='')}"
        "&type=m3u_plus&output=ts"
    )


def _parse_m3u_attrs(line: str) -> tuple[dict[str, str], str]:
    attrs = {key.lower(): value for key, value in M3U_ATTR_RE.findall(line)}
    display = line.rsplit(",", 1)[-1].strip() if "," in line else ""
    return attrs, display


def _series_key(name: str) -> str:
    cleaned = clean_title(name) or name
    return re.sub(r"[^a-z0-9]+", "", cleaned.lower())


def _parse_episode_title(title: str) -> tuple[str, int, int] | None:
    text = (title or "").strip()
    for pattern in EPISODE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        series_name = re.sub(r"[-_.\s]+$", "", match.group(1)).strip()
        if not series_name:
            continue
        try:
            return series_name, int(match.group(2)), int(match.group(3))
        except (TypeError, ValueError):
            return None
    return None


def _episode_ext_from_url(url: str, fallback: str = "mp4") -> str:
    try:
        path = urlparse(url).path
        ext = os.path.splitext(path)[1].lstrip(".")
        return ext or fallback
    except Exception:
        return fallback


def _download_m3u_series_index(
    host: str,
    user: str,
    password: str,
) -> tuple[dict[str, list[dict]], int]:
    headers = {
        "Accept-Encoding": "identity",
        "User-Agent": "Xtream-VOD-Downloader/1.0",
        "Connection": "close",
    }
    response = requests.get(
        _m3u_url(host, user, password),
        headers=headers,
        timeout=(10, 300),
    )
    response.raise_for_status()

    index: dict[str, list[dict]] = {}
    total = 0
    pending_attrs: dict[str, str] = {}
    pending_display = ""
    for raw_line in response.text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            pending_attrs, pending_display = _parse_m3u_attrs(line)
            continue
        if line.startswith("#"):
            continue

        match = M3U_SERIES_URL_RE.search(line)
        if not match:
            pending_attrs, pending_display = {}, ""
            continue
        title = (
            pending_attrs.get("tvg-name")
            or pending_attrs.get("name")
            or pending_display
        )
        parsed = _parse_episode_title(title)
        if parsed is None:
            pending_attrs, pending_display = {}, ""
            continue
        series_name, season, episode = parsed
        entry = {
            "series_name": series_name,
            "season": season,
            "episode": episode,
            "url": line,
            "ext": match.group(2) or _episode_ext_from_url(line),
            "group_title": pending_attrs.get("group-title", ""),
            "raw_title": title,
        }
        index.setdefault(_series_key(series_name), []).append(entry)
        total += 1
        pending_attrs, pending_display = {}, ""
    return index, total


def _m3u_episodes_for_series(index: dict[str, list[dict]], series_name: str) -> list[dict]:
    key = _series_key(series_name)
    if not key:
        return []
    episodes = index.get(key, [])
    if episodes:
        return episodes

    # Some playlists include extra country/language tags in either source.
    # Use a conservative one-way containment fallback to avoid matching short titles broadly.
    if len(key) < 6:
        return []
    for candidate_key, candidate_episodes in index.items():
        if key in candidate_key or candidate_key in key:
            return candidate_episodes
    return []


def _exclusion_reason(
    name: str,
    category_name: str,
    *,
    exclude_terms: list[str],
    exclude_adult: bool,
    adult_terms: list[str],
) -> str | None:
    """Pre-TMDB exclusion (cheap). Returns 'term', 'adult', or None."""
    if exclude_terms and title_matches_terms(name, exclude_terms):
        return "term"
    if exclude_adult:
        if is_adult_category(category_name):
            return "adult"
        if title_matches_terms(name, adult_terms):
            return "adult"
    return None


def _resolve_movie_paths(
    item: dict,
    movies_output: str,
    tmdb_client: TmdbClient | None,
    config: dict,
) -> tuple[str | None, str]:
    """Returns (strm_path, status_hint). status_hint in '', 'unmatched', 'adult'."""
    name = str(item.get("name") or "").strip()
    if tmdb_client is not None:
        match = tmdb_client.search_movie(name)
        if match:
            if config.get("exclude_adult") and match.get("adult"):
                return None, "adult"
            _folder, strm_path = build_movie_strm_path_tmdb(
                match.get("title") or name,
                match.get("year"),
                match.get("tmdb_id"),
                movies_output,
            )
            return strm_path, ""
        return None, "unmatched"
    _folder, strm_path = build_movie_strm_path(name, movies_output)
    return strm_path, ""


def _skip_strm_when_local_exists(strm_path: str) -> str | None:
    """If a local download exists, remove the .strm and skip sync. Returns action label."""
    if not local_download_exists_for_strm(strm_path):
        return None
    if os.path.isfile(strm_path):
        try:
            os.remove(strm_path)
            return "removed"
        except OSError:
            return "skipped"
    return "skipped"


def _sync_movie_item(
    item: dict,
    host: str,
    user: str,
    password: str,
    movies_output: str,
    *,
    update_existing: bool,
    tmdb_client: TmdbClient | None,
    config: dict,
    probe_on_write: bool = False,
) -> tuple[str, str | None]:
    """Returns (status, strm_path). status: created/updated/skipped/excluded/unmatched/probe_failed."""
    name = str(item.get("name") or "").strip()
    stream_id = item.get("stream_id")
    if not name or stream_id is None:
        return "skipped", None

    strm_path, hint = _resolve_movie_paths(item, movies_output, tmdb_client, config)
    if hint == "adult":
        return "excluded", None
    if strm_path is None:
        return "unmatched", None

    ext = str(item.get("container_extension") or "mp4")
    url = build_movie_stream_url(host, user, password, stream_id, ext)

    local_action = _skip_strm_when_local_exists(strm_path)
    if local_action:
        return local_action, strm_path

    if os.path.isfile(strm_path):
        existing_url = read_strm_url(strm_path)
        if existing_url == url:
            return "skipped", strm_path
        if not update_existing:
            return "skipped", strm_path
        if probe_on_write:
            media = probe_stream_media_info(url, timeout=45)
            if media is None or float(media.get("duration") or 0) <= 0:
                return "probe_failed", strm_path
        if write_strm(strm_path, url):
            return "updated", strm_path
        return "skipped", strm_path

    # Create: always verify new / replacement streams before writing.
    if probe_on_write:
        media = probe_stream_media_info(url, timeout=45)
        if media is None or float(media.get("duration") or 0) <= 0:
            return "probe_failed", strm_path

    if write_strm(strm_path, url):
        return "created", strm_path
    return "skipped", strm_path


def _sync_movie_version_group(
    versions: list[dict],
    host: str,
    user: str,
    password: str,
    movies_output: str,
    *,
    update_existing: bool,
    tmdb_client: TmdbClient | None,
    config: dict,
    allow_4k: bool,
    discarded_store: dict | None = None,
) -> tuple[str, str | None]:
    """Try catalog versions in quality order; probe before create/update.

    New versions (not discarded) are probed; failures are discarded and the next
    alternate is tried. Existing working STRMs are left alone when update_existing
    is False.
    """
    from discarded_movie_streams import (
        iter_alternate_catalog_versions,
        mark_movie_stream_discarded,
    )

    candidates = iter_alternate_catalog_versions(
        versions,
        allow_4k=allow_4k,
        store=discarded_store,
    )
    if not candidates:
        return "skipped", None

    saw_unmatched = False
    last_path: str | None = None

    for item in candidates:
        # If a STRM already exists for this title, keep it (unless updating).
        # Resolve path first so we can short-circuit without probing.
        strm_path, hint = _resolve_movie_paths(item, movies_output, tmdb_client, config)
        if hint == "adult":
            return "excluded", None
        if strm_path is None:
            saw_unmatched = True
            continue
        last_path = strm_path

        if os.path.isfile(strm_path) and not update_existing:
            existing_url = read_strm_url(strm_path)
            ext = str(item.get("container_extension") or "mp4")
            url = build_movie_stream_url(
                host, user, password, item.get("stream_id"), ext
            )
            if existing_url == url:
                return "skipped", strm_path
            # Different catalog winner but file exists — do not replace.
            return "skipped", strm_path

        result, path = _sync_movie_item(
            item,
            host,
            user,
            password,
            movies_output,
            update_existing=update_existing,
            tmdb_client=tmdb_client,
            config=config,
            probe_on_write=True,
        )
        if result == "probe_failed":
            mark_movie_stream_discarded(
                stream_id=item.get("stream_id") or "",
                ext=str(item.get("container_extension") or "mp4"),
                size=item_file_size_bytes(item),
                name=str(item.get("name") or ""),
                title=str(item.get("name") or ""),
                reason="probe_failed_sync",
                url=build_movie_stream_url(
                    host,
                    user,
                    password,
                    item.get("stream_id") or "",
                    str(item.get("container_extension") or "mp4"),
                ),
            )
            if discarded_store is not None:
                # Keep in-memory store in sync for subsequent candidates.
                sid = str(item.get("stream_id") or "")
                if sid:
                    discarded_store.setdefault("streams", {})[sid] = {
                        "stream_id": sid,
                        "fingerprint": (
                            f"{sid}|{str(item.get('container_extension') or 'mp4').lstrip('.').lower()}|"
                            f"{item_file_size_bytes(item)}"
                        ),
                        "reason": "probe_failed_sync",
                    }
            continue
        if result in {"created", "updated", "removed", "skipped", "excluded"}:
            return result, path
        if result == "unmatched":
            saw_unmatched = True
            continue

    if saw_unmatched:
        return "unmatched", last_path
    return "skipped", last_path


def _resolve_series_naming(
    series_name: str,
    tmdb_client: TmdbClient | None,
    config: dict,
) -> tuple[dict | None, str]:
    """Returns (tmdb_match_or_none, hint). hint in '', 'unmatched', 'adult'."""
    if tmdb_client is None:
        return None, ""
    match = tmdb_client.search_series(series_name)
    if match:
        if config.get("exclude_adult") and match.get("adult"):
            return None, "adult"
        return match, ""
    return None, "unmatched"


def _series_episode_path(
    series_name: str,
    match: dict | None,
    use_tmdb: bool,
    season: int,
    episode: int,
    series_output: str,
) -> str:
    if use_tmdb and match:
        _folder, strm_path = build_episode_strm_path_tmdb(
            match.get("title") or series_name,
            match.get("year"),
            match.get("tmdb_id"),
            season,
            episode,
            series_output,
        )
        return strm_path
    name = series_name
    if use_tmdb:
        name = clean_title(series_name) or series_name
    _folder, strm_path = build_episode_strm_path(name, season, episode, series_output)
    return strm_path


def _remove_strm_and_sidecars(strm_path: str) -> bool:
    """Delete a .strm and common Jellyfin sidecars (.nfo / images). Returns True if .strm removed."""
    removed = False
    if os.path.isfile(strm_path):
        try:
            os.remove(strm_path)
            removed = True
        except OSError:
            pass
    base, _ext = os.path.splitext(strm_path)
    for sidecar_ext in (".nfo", ".jpg", ".jpeg", ".png", ".webp"):
        sidecar = base + sidecar_ext
        if os.path.isfile(sidecar):
            try:
                os.remove(sidecar)
            except OSError:
                pass
    return removed


def _tmdb_episode_filter_enabled(config: dict, match: dict | None, tmdb_client: TmdbClient | None) -> bool:
    return bool(
        config.get("filter_tmdb_episodes", True)
        and config.get("use_tmdb")
        and tmdb_client is not None
        and match
        and match.get("tmdb_id") is not None
    )


def _apply_tmdb_episode_filter(
    *,
    tmdb_client: TmdbClient,
    match: dict,
    season: int,
    episode_num: int,
    strm_path: str,
    counts: dict[str, int],
) -> bool:
    """Return True if the episode must be skipped (not on TMDB). Removes existing phantom files."""
    valid = tmdb_client.is_valid_tv_episode(match.get("tmdb_id"), season, episode_num)
    if valid is not False:
        return False
    _remove_strm_and_sidecars(strm_path)
    counts["tmdb_filtered"] += 1
    return True


def _sync_series_item(
    series: dict,
    host: str,
    user: str,
    password: str,
    series_output: str,
    *,
    update_existing: bool,
    tmdb_client: TmdbClient | None,
    config: dict,
    expected_paths: set[str] | None = None,
) -> dict[str, int]:
    counts = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "removed": 0,
        "excluded": 0,
        "unmatched": 0,
        "errors": 0,
        "tmdb_filtered": 0,
    }
    series_id = series.get("series_id")
    series_name = str(series.get("name") or "").strip()
    if not series_id or not series_name:
        return counts

    match, hint = _resolve_series_naming(series_name, tmdb_client, config)
    if hint == "adult":
        counts["excluded"] = 1
        return counts
    if hint == "unmatched":
        counts["unmatched"] = 1
        return counts
    use_tmdb = tmdb_client is not None and bool(match)
    filter_tmdb = _tmdb_episode_filter_enabled(config, match, tmdb_client)

    try:
        info = get_series_info(host, user, password, series_id)
    except RuntimeError:
        # Provider error on this series (e.g. 404 for a removed series_id).
        # Skip it without aborting the whole scan.
        counts["errors"] = 1
        return counts
    if not info or "episodes" not in info:
        return counts

    episodes_map = normalize_episodes_map(info.get("episodes"))
    for season_key, season_eps in episodes_map.items():
        try:
            season = int(season_key)
        except (TypeError, ValueError):
            continue
        if not isinstance(season_eps, list):
            continue
        for ep in season_eps:
            episode_num = int(ep.get("episode_num", -1))
            ep_id = ep.get("id")
            if episode_num < 0 or ep_id is None:
                continue
            ext = str(ep.get("container_extension") or "mp4")
            url = build_episode_stream_url(host, user, password, ep_id, ext)
            strm_path = _series_episode_path(
                series_name, match, use_tmdb, season, episode_num, series_output
            )
            if filter_tmdb and _apply_tmdb_episode_filter(
                tmdb_client=tmdb_client,
                match=match,
                season=season,
                episode_num=episode_num,
                strm_path=strm_path,
                counts=counts,
            ):
                continue
            local_action = _skip_strm_when_local_exists(strm_path)
            if local_action:
                counts[local_action] += 1
                continue
            action = "skipped"
            if os.path.isfile(strm_path):
                existing_url = read_strm_url(strm_path)
                if existing_url == url:
                    action = "skipped"
                elif update_existing and write_strm(strm_path, url):
                    action = "updated"
            elif write_strm(strm_path, url):
                action = "created"
            counts[action] += 1
            if action == "created":
                try:
                    from strm_seasons import record_touched_season_from_strm

                    record_touched_season_from_strm(strm_path)
                except Exception:
                    pass
            if expected_paths is not None and os.path.isfile(strm_path):
                expected_paths.add(os.path.realpath(strm_path))

    return counts


def _sync_series_m3u_item(
    series: dict,
    episodes: list[dict],
    series_output: str,
    *,
    update_existing: bool,
    tmdb_client: TmdbClient | None,
    config: dict,
    expected_paths: set[str] | None = None,
) -> dict[str, int]:
    counts = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "removed": 0,
        "excluded": 0,
        "unmatched": 0,
        "errors": 0,
        "tmdb_filtered": 0,
    }
    series_name = str(series.get("name") or "").strip()
    if not series_name:
        return counts

    match, hint = _resolve_series_naming(series_name, tmdb_client, config)
    if hint == "adult":
        counts["excluded"] = 1
        return counts
    if hint == "unmatched":
        counts["unmatched"] = 1
        return counts
    use_tmdb = tmdb_client is not None and bool(match)
    filter_tmdb = _tmdb_episode_filter_enabled(config, match, tmdb_client)

    seen_paths: set[str] = set()
    for ep in episodes:
        try:
            season = int(ep.get("season", -1))
            episode_num = int(ep.get("episode", -1))
        except (TypeError, ValueError):
            continue
        if season < 0 or episode_num < 0:
            continue
        url = str(ep.get("url") or "").strip()
        if not url:
            continue
        strm_path = _series_episode_path(
            series_name, match, use_tmdb, season, episode_num, series_output
        )
        real_path = os.path.realpath(strm_path)
        if real_path in seen_paths:
            continue
        seen_paths.add(real_path)
        if filter_tmdb and _apply_tmdb_episode_filter(
            tmdb_client=tmdb_client,
            match=match,
            season=season,
            episode_num=episode_num,
            strm_path=strm_path,
            counts=counts,
        ):
            continue
        local_action = _skip_strm_when_local_exists(strm_path)
        if local_action:
            counts[local_action] += 1
            continue
        action = "skipped"
        if os.path.isfile(strm_path):
            existing_url = read_strm_url(strm_path)
            if existing_url == url:
                action = "skipped"
            elif update_existing and write_strm(strm_path, url):
                action = "updated"
        elif write_strm(strm_path, url):
            action = "created"
        counts[action] += 1
        if action == "created":
            try:
                from strm_seasons import record_touched_season_from_strm

                record_touched_season_from_strm(strm_path)
            except Exception:
                pass
        if expected_paths is not None and os.path.isfile(strm_path):
            expected_paths.add(real_path)

    return counts


def _remove_orphan_strm_files(root: str, expected_paths: set[str]) -> int:
    removed = 0
    if not root or not os.path.isdir(root):
        return removed
    for dirpath, _dirs, files in os.walk(root):
        for filename in files:
            if not filename.lower().endswith(".strm"):
                continue
            full = os.path.realpath(os.path.join(dirpath, filename))
            if full not in expected_paths:
                try:
                    os.remove(full)
                    removed += 1
                except OSError:
                    pass
    return removed


def _prune_dirs_without_strm(root: str) -> int:
    """Remove directories under root that contain no .strm (and leftover sidecars).

    Walks bottom-up so season folders go first, then empty series folders.
    Does not remove root itself.
    """
    removed = 0
    if not root or not os.path.isdir(root):
        return removed
    root = os.path.realpath(root)
    for dirpath, _dirs, files in os.walk(root, topdown=False):
        if os.path.realpath(dirpath) == root:
            continue
        has_strm = any(filename.lower().endswith(".strm") for filename in files)
        if has_strm:
            continue
        for filename in files:
            try:
                os.remove(os.path.join(dirpath, filename))
            except OSError:
                pass
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                removed += 1
        except OSError:
            pass
    return removed


def _count_strm_files(root: str) -> int:
    if not root or not os.path.isdir(root):
        return 0
    total = 0
    for _dirpath, _dirs, files in os.walk(root):
        total += sum(1 for filename in files if filename.lower().endswith(".strm"))
    return total


def _cleanup_guard_allows(root: str, expected_paths: set[str], min_ratio: float) -> tuple[bool, str]:
    existing_count = _count_strm_files(root)
    expected_count = len(expected_paths)
    if existing_count == 0:
        return True, "no existing .strm"
    if expected_count == 0:
        return False, f"0 expected paths for {existing_count} existing .strm"
    ratio = expected_count / max(existing_count, 1)
    if ratio < min_ratio:
        return False, (
            f"expected {expected_count} paths for {existing_count} existing .strm "
            f"({ratio:.1%} < {min_ratio:.0%})"
        )
    return True, f"expected {expected_count} paths for {existing_count} existing .strm"


def _refresh_media_libraries(config: dict) -> None:
    from emby_watcher import MediaServerClient

    auto = load_auto_download_config()
    if config.get("refresh_emby") and auto.get("emby_enabled"):
        client = MediaServerClient(
            str(auto.get("emby_url", "")),
            str(auto.get("emby_api_key", "")),
            "emby",
        )
        client.refresh_libraries()
    if config.get("refresh_jellyfin") and auto.get("jellyfin_enabled"):
        client = MediaServerClient(
            str(auto.get("jellyfin_url", "")),
            str(auto.get("jellyfin_api_key", "")),
            "jellyfin",
        )
        client.refresh_libraries()


def _run_post_sync_movie_audit_and_push(
    created_paths: list[str],
    *,
    config: dict,
    status: dict,
    movies_output: str,
) -> None:
    """Audit newly created/updated movie STRMs, then push media info to Jellyfin."""
    paths = [p for p in created_paths if p and os.path.isfile(p)]
    if not paths:
        return

    status["phase"] = "movie_audit"
    status["progress_text"] = f"Auditing {len(paths)} new/updated movies..."
    _append_log(status, status["progress_text"])
    _save_status(status)

    try:
        from strm_duration_audit import (
            is_duration_audit_running,
            run_duration_audit,
        )

        if is_duration_audit_running():
            _append_log(
                status,
                "Duration audit already running — new movies will be picked up later",
            )
            return

        audit_status = run_duration_audit(
            movies_root=movies_output or config.get("movies_output"),
            config=config,
            only_paths=paths,
        )
        _append_log(
            status,
            "Post-sync movie audit: "
            f"ok={audit_status.get('ok', 0)}, "
            f"mismatch={audit_status.get('mismatch', 0)}, "
            f"probe_failed={audit_status.get('probe_failed', 0)}, "
            f"deleted={int(audit_status.get('deleted_probe_failed') or 0) + int(audit_status.get('deleted_no_italian') or 0)}",
        )
        if audit_status.get("last_error"):
            _append_log(status, f"Post-sync audit note: {audit_status['last_error']}")
    except Exception as exc:  # noqa: BLE001
        _append_log(status, f"Post-sync movie audit failed: {exc}")
        return

    if not config.get("push_new_movies_to_jellyfin", True):
        _append_log(status, "Skipped Jellyfin push (push_new_movies_to_jellyfin=false)")
        return

    auto = load_auto_download_config()
    if not auto.get("jellyfin_enabled"):
        _append_log(status, "Skipped Jellyfin push (Jellyfin disabled in auto-download)")
        return

    # Ensure JF has scanned new STRMs before applying media info.
    if not config.get("refresh_jellyfin"):
        try:
            from emby_watcher import MediaServerClient

            client = MediaServerClient(
                str(auto.get("jellyfin_url", "")),
                str(auto.get("jellyfin_api_key", "")),
                "jellyfin",
            )
            client.refresh_libraries()
            _append_log(status, "Jellyfin library refresh requested before media push")
            time.sleep(3)
        except Exception as exc:  # noqa: BLE001
            _append_log(status, f"Jellyfin refresh before push failed: {exc}")

    status["phase"] = "jellyfin_push"
    status["progress_text"] = f"Pushing {len(paths)} movies media info to Jellyfin..."
    _append_log(status, status["progress_text"])
    _save_status(status)

    try:
        from strm_jellyfin_push import is_jellyfin_push_running, run_jellyfin_push

        if is_jellyfin_push_running():
            _append_log(status, "Jellyfin push already running — skipped")
            return

        jf_root = str(config.get("jellyfin_movies_root") or "/media/movies").strip()
        push_status = run_jellyfin_push(
            strm_root=movies_output or config.get("movies_output"),
            jellyfin_movies_root=jf_root or "/media/movies",
            only_paths=paths,
        )
        _append_log(
            status,
            "Post-sync Jellyfin push: "
            f"applied={push_status.get('applied', 0)}, "
            f"missing={push_status.get('missing', 0)}, "
            f"failed={push_status.get('failed', 0)}, "
            f"skipped_no_media={push_status.get('skipped_no_media', 0)}",
        )
        if push_status.get("last_error"):
            _append_log(status, f"Post-sync JF push note: {push_status['last_error']}")
    except Exception as exc:  # noqa: BLE001
        _append_log(status, f"Post-sync Jellyfin push failed: {exc}")


def _build_tmdb_client(config: dict) -> TmdbClient | None:
    if not config.get("use_tmdb"):
        return None
    api_key = str(config.get("tmdb_api_key") or "").strip()
    if not api_key:
        return None
    return TmdbClient(
        api_key,
        language=str(config.get("tmdb_language") or "it-IT"),
        rate_limit=int(config.get("tmdb_rate_limit") or 40),
    )


def run_strm_sync(host: str, user: str, password: str, config: dict | None = None) -> None:
    config = config or load_strm_sync_config()
    status = default_strm_sync_status()
    status["running"] = True
    _save_status(status)

    tmdb_client = _build_tmdb_client(config)
    sync_started = time.perf_counter()

    try:
        if tmdb_client is not None:
            purged = tmdb_client.purge_expired_negative_cache()
            if purged:
                _append_log(status, f"TMDB: purged {purged} expired no-match cache entries")
        allow_4k = bool(config.get("allow_4k", False))
        update_existing = bool(config.get("update_existing", True))
        remove_missing = bool(config.get("remove_missing", False))
        cleanup_min_ratio = max(0.05, min(1.0, float(config.get("cleanup_min_ratio", 0.5))))
        exclude_terms = list(config.get("exclude_terms", []))
        exclude_adult = bool(config.get("exclude_adult", True))
        adult_terms = list(config.get("adult_terms", []))
        series_source = str(config.get("series_source") or "api")
        movies_output = str(config.get("movies_output") or "").strip()
        series_output = str(config.get("series_output") or "").strip()
        expected_movie_paths: set[str] = set()
        expected_episode_paths: set[str] = set()
        created_movie_paths: list[str] = []

        if config.get("use_tmdb") and tmdb_client is None:
            _append_log(status, "TMDB enabled but API key missing — using raw names")

        if config.get("sync_movies") and movies_output:
            movies_started = time.perf_counter()
            status["phase"] = "movies"
            status["progress_text"] = "Loading movie catalog..."
            _append_log(status, status["progress_text"])
            _save_status(status)

            vod_category_map = _fetch_categories(host, user, password, "get_vod_categories")
            vod_ids = [str(cid) for cid in config.get("vod_category_ids", []) if cid]
            if vod_ids:
                movies: list[dict] = []
                for idx, cat_id in enumerate(vod_ids):
                    status["progress_text"] = f"Movies — category {idx + 1}/{len(vod_ids)}"
                    _save_status(status)
                    movies.extend(_fetch_vod_streams(host, user, password, cat_id))
            else:
                movies = _fetch_vod_streams(host, user, password, None)
                movies = exclude_hidden_items(movies, "vod")

            from discarded_movie_streams import load_discarded_streams

            discarded_store = load_discarded_streams()
            discarded_count = len(discarded_store.get("streams") or {})
            if discarded_count:
                _append_log(
                    status,
                    f"Discarded movie streams loaded: {discarded_count} "
                    f"(skipped unless fingerprint changed; new versions are probed)",
                )

            # Pre-filter all versions, then group — so a new alternate can replace
            # a discarded winner after probe.
            filtered_versions: list[dict] = []
            for item in movies:
                name = str(item.get("name") or "")
                cat_name = vod_category_map.get(str(item.get("category_id") or ""), "")
                reason = _exclusion_reason(
                    name,
                    cat_name,
                    exclude_terms=exclude_terms,
                    exclude_adult=exclude_adult,
                    adult_terms=adult_terms,
                )
                if reason:
                    status["movies_excluded"] += 1
                else:
                    filtered_versions.append(item)

            groups = group_catalog_versions(filtered_versions)
            group_items = list(groups.items())
            _append_log(
                status,
                f"Movies: {len(group_items)} titles "
                f"({len(filtered_versions)} versions), "
                f"{status['movies_excluded']} excluded",
            )

            for idx, (_title_key, versions) in enumerate(group_items):
                status["progress"] = (idx + 1) / max(len(group_items), 1)
                sample_name = str((versions[0] or {}).get("name") or "")[:60]
                status["progress_text"] = (
                    f"Movies — {idx + 1}/{len(group_items)}: {sample_name}"
                )
                if idx % 25 == 0:
                    status["tmdb_lookups"] = getattr(tmdb_client, "lookups", 0)
                    status["tmdb_cache_hits"] = getattr(tmdb_client, "cache_hits", 0)
                    _save_status(status)
                    if tmdb_client is not None:
                        tmdb_client.save_cache()
                try:
                    result, strm_path = _sync_movie_version_group(
                        versions,
                        host,
                        user,
                        password,
                        movies_output,
                        update_existing=update_existing,
                        tmdb_client=tmdb_client,
                        config=config,
                        allow_4k=allow_4k,
                        discarded_store=discarded_store,
                    )
                except Exception:
                    # An unexpected error on a single movie must not abort the scan.
                    status["movies_errors"] += 1
                    continue
                if strm_path and os.path.isfile(strm_path):
                    expected_movie_paths.add(os.path.realpath(strm_path))
                if result == "created":
                    status["movies_created"] += 1
                    if strm_path and os.path.isfile(strm_path):
                        created_movie_paths.append(os.path.realpath(strm_path))
                elif result == "updated":
                    status["movies_updated"] += 1
                    if strm_path and os.path.isfile(strm_path):
                        # Treat URL updates as new streams that need media audit/push.
                        created_movie_paths.append(os.path.realpath(strm_path))
                elif result == "removed":
                    status["movies_removed"] += 1
                elif result == "excluded":
                    status["movies_excluded"] += 1
                elif result == "unmatched":
                    status["movies_unmatched"] += 1
                else:
                    status["movies_skipped"] += 1

            if tmdb_client is not None:
                tmdb_client.save_cache()
            status["movies_elapsed_sec"] = time.perf_counter() - movies_started
            _append_log(
                status,
                (
                    f"Movies done: {status['movies_created']} created, "
                    f"{status['movies_updated']} updated, "
                    f"{status['movies_skipped']} skipped, "
                    f"{status['movies_excluded']} excluded "
                    f"({format_elapsed_seconds(status['movies_elapsed_sec'])})"
                ),
            )
            if created_movie_paths:
                # Dedupe while preserving order.
                created_movie_paths = list(dict.fromkeys(created_movie_paths))
                _append_log(
                    status,
                    f"New/updated movie STRMs for audit+JF: {len(created_movie_paths)}",
                )

        if config.get("sync_series") and series_output:
            series_started = time.perf_counter()
            status["phase"] = "series"
            status["progress"] = 0.0
            status["progress_text"] = "Loading series catalog..."
            _append_log(status, status["progress_text"])
            _save_status(status)

            series_category_map = _fetch_categories(
                host, user, password, "get_series_categories"
            )
            series_ids = [str(cid) for cid in config.get("series_category_ids", []) if cid]
            if series_ids:
                series_list: list[dict] = []
                for idx, cat_id in enumerate(series_ids):
                    status["progress_text"] = f"Series — category {idx + 1}/{len(series_ids)}"
                    _save_status(status)
                    series_list.extend(_fetch_series_catalog(host, user, password, cat_id))
            else:
                series_list = _fetch_series_catalog(host, user, password, None)
                series_list = exclude_hidden_items(series_list, "series")

            filtered_series: list[dict] = []
            for series in series_list:
                name = str(series.get("name") or "")
                cat_name = series_category_map.get(str(series.get("category_id") or ""), "")
                reason = _exclusion_reason(
                    name,
                    cat_name,
                    exclude_terms=exclude_terms,
                    exclude_adult=exclude_adult,
                    adult_terms=adult_terms,
                )
                if reason:
                    status["series_excluded"] += 1
                else:
                    filtered_series.append(series)

            _append_log(
                status,
                f"Series: {len(filtered_series)} to sync, {status['series_excluded']} excluded",
            )

            m3u_index: dict[str, list[dict]] | None = None
            if series_source in {"m3u", "m3u_api_fallback"}:
                status["progress_text"] = "Downloading M3U playlist..."
                _append_log(status, status["progress_text"])
                _save_status(status)
                try:
                    m3u_index, m3u_episode_count = _download_m3u_series_index(
                        host, user, password
                    )
                    _append_log(
                        status,
                        (
                            f"M3U parsed: {m3u_episode_count} series episodes "
                            f"across {len(m3u_index)} titles"
                        ),
                    )
                except Exception as exc:
                    status["last_error"] = f"M3U download failed: {exc}"
                    _append_log(status, status["last_error"])
                    if series_source == "m3u":
                        m3u_index = {}
                    else:
                        m3u_index = None

            for idx, series in enumerate(filtered_series):
                status["progress"] = (idx + 1) / max(len(filtered_series), 1)
                series_name = str(series.get("name") or "")
                status["progress_text"] = (
                    f"Series — {idx + 1}/{len(filtered_series)}: {series_name[:60]}"
                )
                if idx % 5 == 0:
                    status["tmdb_lookups"] = getattr(tmdb_client, "lookups", 0)
                    status["tmdb_cache_hits"] = getattr(tmdb_client, "cache_hits", 0)
                    _save_status(status)
                    if tmdb_client is not None:
                        tmdb_client.save_cache()
                try:
                    counts: dict[str, int]
                    episodes = (
                        _m3u_episodes_for_series(m3u_index, series_name)
                        if m3u_index is not None
                        else []
                    )
                    if episodes:
                        counts = _sync_series_m3u_item(
                            series,
                            episodes,
                            series_output,
                            update_existing=update_existing,
                            tmdb_client=tmdb_client,
                            config=config,
                            expected_paths=expected_episode_paths if remove_missing else None,
                        )
                        status["series_from_m3u"] += 1
                    elif series_source == "m3u":
                        counts = {
                            "created": 0,
                            "updated": 0,
                            "skipped": 0,
                            "removed": 0,
                            "excluded": 0,
                            "unmatched": 0,
                            "errors": 0,
                            "tmdb_filtered": 0,
                        }
                        if m3u_index is not None:
                            status["series_m3u_missing"] += 1
                    else:
                        counts = _sync_series_item(
                            series,
                            host,
                            user,
                            password,
                            series_output,
                            update_existing=update_existing,
                            tmdb_client=tmdb_client,
                            config=config,
                            expected_paths=expected_episode_paths if remove_missing else None,
                        )
                        status["series_from_api"] += 1
                        if m3u_index is not None:
                            status["series_m3u_missing"] += 1
                except Exception:
                    # Any unexpected per-series error must not abort the whole scan.
                    status["series_errors"] += 1
                    continue
                status["episodes_created"] += counts["created"]
                status["episodes_updated"] += counts["updated"]
                status["episodes_skipped"] += counts["skipped"]
                status["episodes_removed"] += counts.get("removed", 0)
                status["episodes_tmdb_filtered"] += counts.get("tmdb_filtered", 0)
                status["series_excluded"] += counts["excluded"]
                status["series_unmatched"] += counts["unmatched"]
                status["series_errors"] += counts.get("errors", 0)
                # Series-level: brand-new folder vs existing series with writes.
                if (
                    counts["created"] > 0
                    and counts["updated"] == 0
                    and counts["skipped"] == 0
                ):
                    status["series_created"] += 1
                elif counts["created"] > 0 or counts["updated"] > 0:
                    status["series_updated"] += 1

            if tmdb_client is not None:
                tmdb_client.save_cache()
            status["series_elapsed_sec"] = time.perf_counter() - series_started
            _append_log(
                status,
                (
                    f"Series done: {status['series_created']} series created, "
                    f"{status['series_updated']} series updated, "
                    f"{status['episodes_created']} episodes created, "
                    f"{status['episodes_updated']} updated, "
                    f"{status['episodes_skipped']} skipped, "
                    f"{status['series_excluded']} series excluded, "
                    f"{status['series_errors']} series errors "
                    f"({format_elapsed_seconds(status['series_elapsed_sec'])})"
                ),
            )
            if status.get("episodes_tmdb_filtered"):
                _append_log(
                    status,
                    (
                        "TMDB episode filter: "
                        f"{status['episodes_tmdb_filtered']} phantom episodes skipped/removed"
                    ),
                )
            if series_source in {"m3u", "m3u_api_fallback"}:
                _append_log(
                    status,
                    (
                        f"Series source: {status['series_from_m3u']} from M3U, "
                        f"{status['series_from_api']} via API fallback, "
                        f"{status['series_m3u_missing']} not found in M3U"
                    ),
                )

        if remove_missing:
            status["phase"] = "cleanup"
            status["progress_text"] = "Removing missing entries..."
            _save_status(status)
            if config.get("sync_movies") and movies_output:
                allowed, reason = _cleanup_guard_allows(
                    movies_output,
                    expected_movie_paths,
                    cleanup_min_ratio,
                )
                if allowed:
                    status["movies_removed"] = _remove_orphan_strm_files(
                        movies_output,
                        expected_movie_paths,
                    )
                    status["dirs_removed"] = int(status.get("dirs_removed") or 0) + _prune_dirs_without_strm(
                        movies_output
                    )
                else:
                    status["cleanup_skipped"] = True
                    _append_log(status, f"Movie cleanup skipped: {reason}")
            if config.get("sync_series") and series_output:
                allowed, reason = _cleanup_guard_allows(
                    series_output,
                    expected_episode_paths,
                    cleanup_min_ratio,
                )
                if allowed:
                    status["episodes_removed"] = _remove_orphan_strm_files(
                        series_output,
                        expected_episode_paths,
                    )
                    status["dirs_removed"] = int(status.get("dirs_removed") or 0) + _prune_dirs_without_strm(
                        series_output
                    )
                else:
                    status["cleanup_skipped"] = True
                    _append_log(status, f"Series cleanup skipped: {reason}")
            _append_log(
                status,
                (
                    f"Cleanup: {status['movies_removed']} movies, "
                    f"{status['episodes_removed']} episodes removed, "
                    f"{status.get('dirs_removed', 0)} empty dirs removed"
                ),
            )

        if config.get("refresh_emby") or config.get("refresh_jellyfin"):
            status["phase"] = "refresh"
            status["progress_text"] = "Refreshing media libraries..."
            _save_status(status)
            _refresh_media_libraries(config)
            _append_log(status, "Media library refresh requested")

        # After movie sync (+ optional library refresh): audit new movies and push to JF.
        if created_movie_paths and config.get("audit_new_movies_on_sync", True):
            _run_post_sync_movie_audit_and_push(
                created_movie_paths,
                config=config,
                status=status,
                movies_output=movies_output,
            )
        elif created_movie_paths:
            _append_log(
                status,
                f"Skipped post-sync audit for {len(created_movie_paths)} movies "
                f"(audit_new_movies_on_sync=false)",
            )

        try:
            from strm_seasons import start_season_analysis

            if start_season_analysis(
                config.get("series_output"),
                strm_config=config,
            ):
                _append_log(status, "Season completeness analysis started")
            else:
                _append_log(status, "Season analysis already running — skipped")
        except Exception as exc:
            _append_log(status, f"Season analysis not started: {exc}")

        if tmdb_client is not None:
            status["tmdb_lookups"] = tmdb_client.lookups
            status["tmdb_cache_hits"] = tmdb_client.cache_hits
        status["total_elapsed_sec"] = time.perf_counter() - sync_started
        status["phase"] = "done"
        status["progress"] = 1.0
        status["progress_text"] = "Sync completed"
        status["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _append_sync_summary(status, config)
    except Exception as exc:
        status["last_error"] = str(exc)
        status["phase"] = "error"
        status["progress_text"] = str(exc)
        status["total_elapsed_sec"] = time.perf_counter() - sync_started
        _append_log(status, f"Error: {exc}")
        if status.get("movies_elapsed_sec") or status.get("series_elapsed_sec"):
            _append_sync_summary(status, config)
    finally:
        if tmdb_client is not None:
            try:
                tmdb_client.save_cache()
            except Exception:
                pass
        status["running"] = False
        _save_status(status)


def clear_stale_sync_running(*, reason: str = "process restarted") -> bool:
    """If status says running but no live thread, clear the flag. Returns True if cleared."""
    with _sync_lock:
        alive = _sync_thread is not None and _sync_thread.is_alive()
    if alive:
        return False
    status = load_strm_sync_status()
    if not status.get("running"):
        return False
    status["running"] = False
    if not status.get("last_error"):
        status["last_error"] = f"Sync interrupted ({reason})."
    log = status.setdefault("log", [])
    if isinstance(log, list):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.append(f"[{timestamp}] Cleared stale running=True ({reason})")
        status["log"] = log[-80:]
    _save_status(status)
    return True


def is_strm_sync_running() -> bool:
    with _sync_lock:
        if _sync_thread is not None and _sync_thread.is_alive():
            return True
    # Orphaned flag after container/process restart — clear so schedule can resume.
    clear_stale_sync_running()
    return False


def start_strm_sync(host: str, user: str, password: str, config: dict | None = None) -> bool:
    global _sync_thread
    clear_stale_sync_running()
    with _sync_lock:
        if _sync_thread is not None and _sync_thread.is_alive():
            return False
        if load_strm_sync_status().get("running"):
            return False

        def _worker() -> None:
            try:
                run_strm_sync(host, user, password, config)
            finally:
                global _sync_thread
                with _sync_lock:
                    _sync_thread = None

        _sync_thread = threading.Thread(target=_worker, name="strm-sync", daemon=True)
        _sync_thread.start()
        return True


# Clear orphaned running flag left by container/process restarts.
clear_stale_sync_running()

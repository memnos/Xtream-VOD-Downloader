import json
import os
import re
import shutil
import time
from datetime import datetime

from core import (
    DATA_DIR,
    EPISODE_TAG_RE,
    LOCAL_DOWNLOAD_MARKER,
    SEASON_DIR_RE,
    SERIES_DOWNLOAD_PATHS,
    STRM_OUTPUT_SERIES_PATH,
    VIDEO_EXTENSIONS,
    _remove_path_and_sidecars,
    align_episode_nfo_to_media,
    build_episode_stream_url,
    build_episode_strm_path,
    build_episode_strm_path_tmdb,
    extract_title_year,
    find_strm_folder_match,
    find_xtream_series,
    get_series_info,
    load_auto_download_config,
    load_credentials,
    load_json_file,
    load_strm_sync_config,
    normalize_episodes_map,
    notify_media_servers_after_local_download,
    prepare_output_dir,
    resolve_series_folder_name,
    sanitize_filename,
    write_strm,
)

SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
_IMAGE_SIDECAR_EXTENSIONS = {".nfo", ".jpg", ".jpeg", ".png", ".webp"}

DELETION_PROMPTS_FILE = os.environ.get(
    "DELETION_PROMPTS_FILE", os.path.join(DATA_DIR, "deletion_prompts.json")
)
SERIES_DOWNLOAD_ROOTS = SERIES_DOWNLOAD_PATHS
_TMDB_FOLDER_RE = re.compile(r"\[tmdbid-(\d+)\]", re.IGNORECASE)


def _save_json_file(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def _default_deletion_prompts() -> dict:
    return {"pending": [], "dismissed": []}


def _normalize_prompt_path(path: str) -> str:
    return os.path.realpath(str(path or "").strip())


def _prompt_path_keys(paths: list | None) -> set[str]:
    return {_normalize_prompt_path(p) for p in (paths or []) if str(p or "").strip()}


def _dedupe_pending_prompts(pending: list) -> list:
    """Keep one prompt per download folder (Emby + Jellyfin use different series ids)."""
    unique: list = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        paths = _prompt_path_keys(item.get("paths"))
        if not paths:
            unique.append(dict(item))
            continue
        existing = next((p for p in unique if _prompt_path_keys(p.get("paths")) & paths), None)
        if existing is None:
            unique.append(dict(item))
            continue
        # Merge alternate media-server ids so Yes/No clears all of them.
        alt = list(existing.get("alternate_series_ids") or [])
        sid = str(item.get("series_id") or "")
        existing_sid = str(existing.get("series_id") or "")
        if sid and sid != existing_sid and sid not in alt:
            alt.append(sid)
        for extra in item.get("alternate_series_ids") or []:
            extra_s = str(extra)
            if extra_s and extra_s != existing_sid and extra_s not in alt:
                alt.append(extra_s)
        existing["alternate_series_ids"] = alt
    return unique


def load_deletion_prompts() -> dict:
    data = load_json_file(DELETION_PROMPTS_FILE, _default_deletion_prompts())
    if not isinstance(data, dict):
        return _default_deletion_prompts()
    pending = data.get("pending", [])
    dismissed = data.get("dismissed", [])
    pending = pending if isinstance(pending, list) else []
    deduped = _dedupe_pending_prompts(pending)
    result = {
        "pending": deduped,
        "dismissed": dismissed if isinstance(dismissed, list) else [],
    }
    if deduped != pending:
        save_deletion_prompts(result)
    return result


def save_deletion_prompts(data: dict) -> None:
    _save_json_file(DELETION_PROMPTS_FILE, data)


def folder_has_video_files(folder: str) -> bool:
    if not os.path.isdir(folder):
        return False
    for root, _dirs, files in os.walk(folder):
        for name in files:
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS:
                return True
    return False


def find_series_download_paths(series_name: str) -> list[str]:
    candidates: list[str] = []
    safe_name = resolve_series_folder_name(series_name)
    if safe_name:
        candidates.append(safe_name)
    plain = sanitize_filename(series_name)
    if plain and plain not in candidates:
        candidates.append(plain)

    paths = []
    seen_realpaths: set[str] = set()
    for dest in SERIES_DOWNLOAD_ROOTS:
        names = list(candidates)
        match = find_strm_folder_match(dest, series_name)
        if match and match not in names:
            names.append(match)
        for name in names:
            folder = os.path.join(dest, name)
            real_folder = os.path.realpath(folder)
            if real_folder in seen_realpaths:
                continue
            if folder_has_video_files(folder):
                paths.append(folder)
                seen_realpaths.add(real_folder)
    return paths


def is_series_fully_watched(episodes: list) -> bool:
    if not episodes:
        return False
    for episode in episodes:
        user_data = episode.get("UserData") or {}
        if not user_data.get("Played"):
            return False
    return True


def _ordered_episodes(episodes: list) -> list[tuple[int, int, dict]]:
    ordered = []
    for episode in episodes:
        season = episode.get("ParentIndexNumber")
        ep_num = episode.get("IndexNumber")
        if season is None or ep_num is None:
            continue
        ordered.append((int(season), int(ep_num), episode))
    ordered.sort()
    return ordered


def is_season_fully_watched(episodes: list, season: int) -> bool:
    """True when every library episode of ``season`` is marked Played."""
    try:
        target = int(season)
    except (TypeError, ValueError):
        return False
    season_eps = [item for season_i, _ep, item in _ordered_episodes(episodes) if season_i == target]
    if not season_eps:
        return False
    for item in season_eps:
        user_data = item.get("UserData") or {}
        if not user_data.get("Played"):
            return False
    return True


def iter_local_season_folders(series_folder: str) -> list[tuple[int, str]]:
    """Return (season_number, season_dir) for Season XX folders that still have videos."""
    found: list[tuple[int, str]] = []
    if not os.path.isdir(series_folder):
        return found
    try:
        entries = list(os.scandir(series_folder))
    except OSError:
        return found
    for entry in entries:
        if not entry.is_dir(follow_symlinks=True):
            continue
        match = SEASON_DIR_RE.match(entry.name)
        if not match:
            continue
        if not folder_has_video_files(entry.path):
            continue
        found.append((int(match.group(1)), entry.path))
    found.sort(key=lambda item: item[0])
    return found


def is_series_complete_after_episode(episodes: list, season: int, episode: int) -> bool:
    ordered = _ordered_episodes(episodes)
    if not ordered:
        return False

    last_season, last_episode, _ = ordered[-1]
    if (season, episode) != (last_season, last_episode):
        return False

    for season_i, episode_i, item in ordered:
        if (season_i, episode_i) == (season, episode):
            continue
        user_data = item.get("UserData") or {}
        if not user_data.get("Played"):
            return False
    return True


def should_prompt_series_deletion(episodes: list, season: int, episode: int) -> bool:
    return is_series_fully_watched(episodes) or is_series_complete_after_episode(
        episodes, season, episode
    )


def deletion_prompt_identity(series_id: str, season: int | None = None) -> str:
    sid = str(series_id or "").strip()
    if season is None or season == "":
        return sid
    try:
        return f"{sid}:S{int(season):02d}"
    except (TypeError, ValueError):
        return sid


def _prompt_season_number(item: dict) -> int | None:
    raw = item.get("season")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _prompt_identity(item: dict) -> str:
    return deletion_prompt_identity(str(item.get("series_id") or ""), _prompt_season_number(item))


def _tmdb_client_for_status():
    """Build a TMDB client from strm sync config / env, or None if unavailable."""
    try:
        from tmdb import TmdbClient
    except ImportError:
        return None
    strm_config = load_strm_sync_config()
    api_key = str(
        strm_config.get("tmdb_api_key") or os.environ.get("TMDB_API_KEY") or ""
    ).strip()
    if not api_key:
        return None
    return TmdbClient(
        api_key,
        language=str(strm_config.get("tmdb_language") or "it-IT"),
        rate_limit=int(strm_config.get("tmdb_rate_limit") or 40),
    )


def series_production_finished(paths: list[str] | None = None, *, tmdb_id: int | None = None) -> bool:
    """True only when TMDB confirms the show has Ended/Canceled.

    If TMDB id/status is unknown, returns False (do not offer deletion yet).
    """
    tid = tmdb_id
    if tid is None:
        for path in paths or []:
            tid = _folder_tmdb_id(os.path.basename(os.path.realpath(path)))
            if tid:
                break
    if not tid:
        return False
    client = _tmdb_client_for_status()
    if client is None:
        return False
    try:
        ended = client.is_tv_series_ended(int(tid))
        client.save_cache()
    except Exception:
        return False
    return bool(ended)


def prune_incomplete_deletion_prompts() -> int:
    """Drop full-series prompts for shows still airing; keep season-level prompts."""
    data = load_deletion_prompts()
    pending = data.get("pending", [])
    kept: list = []
    removed = 0
    for item in pending:
        if not isinstance(item, dict):
            continue
        # Season prompts are valid while the show is still returning.
        if _prompt_season_number(item) is not None:
            kept.append(item)
            continue
        paths = item.get("paths") or []
        if series_production_finished(paths):
            kept.append(item)
        else:
            removed += 1
    if removed:
        data["pending"] = kept
        save_deletion_prompts(data)
    return removed


def _prompt_matches_identity(
    item: dict, series_id: str, *, season: int | None = None
) -> bool:
    target = deletion_prompt_identity(series_id, season)
    if _prompt_identity(item) == target:
        return True
    # Legacy callers may pass a bare series_id for a season prompt identity.
    if season is None and str(item.get("series_id") or "") == str(series_id):
        return True
    if season is None and series_id in {str(x) for x in (item.get("alternate_series_ids") or [])}:
        return True
    # Identity may already be "id:S03".
    if ":" in str(series_id) and _prompt_identity(item) == str(series_id):
        return True
    return False


def add_deletion_prompt(
    series_id: str,
    series_name: str,
    paths: list[str],
    *,
    season: int | None = None,
) -> bool:
    if not paths:
        return False
    try:
        season_i = int(season) if season is not None and season != "" else None
    except (TypeError, ValueError):
        season_i = None
    identity = deletion_prompt_identity(series_id, season_i)
    data = load_deletion_prompts()
    dismissed = set(data.get("dismissed", []))
    if identity in dismissed:
        return False
    if season_i is None and series_id in dismissed:
        return False
    new_paths = _prompt_path_keys(paths)
    for item in data.get("pending", []):
        same_season = _prompt_season_number(item) == season_i
        same_series = str(item.get("series_id") or "") == str(series_id) or series_id in {
            str(x) for x in (item.get("alternate_series_ids") or [])
        }
        if same_series and same_season:
            return False
        if new_paths and _prompt_path_keys(item.get("paths")) & new_paths:
            # Same folder already queued from the other media server — remember this id.
            alt = list(item.get("alternate_series_ids") or [])
            if series_id and series_id not in alt and series_id != item.get("series_id"):
                alt.append(series_id)
                item["alternate_series_ids"] = alt
                save_deletion_prompts(data)
            return False
    entry = {
        "series_id": series_id,
        "series_name": series_name,
        "paths": paths,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if season_i is not None:
        entry["season"] = season_i
    data["pending"].append(entry)
    save_deletion_prompts(data)
    return True


def dismiss_deletion_prompt(series_id: str, *, season: int | None = None) -> None:
    data = load_deletion_prompts()
    pending = data.get("pending", [])
    match = next(
        (p for p in pending if _prompt_matches_identity(p, series_id, season=season)),
        None,
    )
    dismissed = set(data.get("dismissed", []))
    ids_to_dismiss = {deletion_prompt_identity(series_id, season)}
    if match:
        ids_to_dismiss.add(_prompt_identity(match))
        ids_to_dismiss.add(str(match.get("series_id") or ""))
        ids_to_dismiss.update(
            deletion_prompt_identity(str(x), _prompt_season_number(match))
            for x in (match.get("alternate_series_ids") or [])
        )
        match_paths = _prompt_path_keys(match.get("paths"))
        match_season = _prompt_season_number(match)
        for item in pending:
            if item is match:
                continue
            if match_paths and _prompt_path_keys(item.get("paths")) & match_paths:
                if _prompt_season_number(item) == match_season:
                    ids_to_dismiss.add(_prompt_identity(item))
    dismissed.update(x for x in ids_to_dismiss if x)
    data["dismissed"] = sorted(dismissed)
    drop_identities = set(ids_to_dismiss)
    data["pending"] = [
        p
        for p in pending
        if _prompt_identity(p) not in drop_identities
        and not (
            match
            and _prompt_path_keys(match.get("paths"))
            and _prompt_path_keys(p.get("paths")) & _prompt_path_keys(match.get("paths"))
            and _prompt_season_number(p) == _prompt_season_number(match)
        )
    ]
    save_deletion_prompts(data)


def remove_deletion_prompt(series_id: str, *, season: int | None = None) -> dict | None:
    data = load_deletion_prompts()
    pending = data.get("pending", [])
    match = next(
        (p for p in pending if _prompt_matches_identity(p, series_id, season=season)),
        None,
    )
    if not match:
        save_deletion_prompts(data)
        return None
    match_paths = _prompt_path_keys(match.get("paths"))
    match_season = _prompt_season_number(match)
    match_identity = _prompt_identity(match)
    data["pending"] = [
        p
        for p in pending
        if p is not match
        and _prompt_identity(p) != match_identity
        and not (
            match_paths
            and _prompt_path_keys(p.get("paths")) & match_paths
            and _prompt_season_number(p) == match_season
        )
    ]
    save_deletion_prompts(data)
    return match


def inventory_local_episodes(paths: list[str]) -> list[dict]:
    """Collect season/episode entries from local video files under download folders."""
    found: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for root in paths:
        if not os.path.isdir(root):
            continue
        series_folder = os.path.basename(os.path.realpath(root))
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                ext = os.path.splitext(name)[1].lower()
                if ext not in VIDEO_EXTENSIONS:
                    continue
                match = EPISODE_TAG_RE.search(name)
                if not match:
                    continue
                season, episode = int(match.group(1)), int(match.group(2))
                key = (season, episode)
                if key in seen:
                    continue
                seen.add(key)
                found.append(
                    {
                        "season": season,
                        "episode": episode,
                        "path": os.path.join(dirpath, name),
                        "series_folder": series_folder,
                    }
                )
    found.sort(key=lambda item: (item["season"], item["episode"]))
    return found


def _folder_tmdb_id(folder_name: str) -> int | None:
    match = _TMDB_FOLDER_RE.search(folder_name or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _resolve_restore_strm_path(
    *,
    series_name: str,
    series_folder_hint: str,
    season: int,
    episode: int,
    series_output: str,
    tmdb_match: dict | None,
) -> str:
    """Pick STRM path using TMDB naming when possible, else folder hint / plain name."""
    if tmdb_match and tmdb_match.get("tmdb_id") is not None:
        _folder, strm_path = build_episode_strm_path_tmdb(
            tmdb_match.get("title") or series_name,
            tmdb_match.get("year"),
            tmdb_match.get("tmdb_id"),
            season,
            episode,
            series_output,
        )
        return strm_path

    hint = series_folder_hint or ""
    tmdb_id = _folder_tmdb_id(hint)
    year = extract_title_year(hint)
    if tmdb_id and hint:
        title = re.sub(r"\s*\[tmdbid-\d+\]\s*$", "", hint, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip() or series_name
        _folder, strm_path = build_episode_strm_path_tmdb(
            title, year, tmdb_id, season, episode, series_output
        )
        return strm_path

    existing = find_strm_folder_match(series_output, hint or series_name)
    name = existing or hint or series_name
    _folder, strm_path = build_episode_strm_path(name, season, episode, series_output)
    return strm_path


def restore_strm_for_episodes(
    series_name: str,
    episodes: list[dict],
    *,
    series_folder_hint: str = "",
) -> dict:
    """Recreate .strm (+ align .nfo) for episodes previously kept as local downloads."""
    result: dict = {
        "created": [],
        "updated": [],
        "targets": [],
        "skipped": 0,
        "missing": [],
        "nfo_aligned": 0,
        "errors": [],
    }
    if not episodes:
        return result

    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or creds.get("username") or "").strip()
    password = str(creds.get("password") or "").strip()
    if not host or not user or not password:
        result["errors"].append("xtream_credentials_missing")
        return result

    auto = load_auto_download_config()
    allow_4k = bool(auto.get("allow_4k"))
    strm_config = load_strm_sync_config()
    series_output = str(
        strm_config.get("series_output") or STRM_OUTPUT_SERIES_PATH
    ).strip() or STRM_OUTPUT_SERIES_PATH

    tmdb_match = None
    if strm_config.get("use_tmdb"):
        try:
            from strm_sync import _resolve_series_naming
            from tmdb import TmdbClient

            tmdb_key = str(strm_config.get("tmdb_api_key") or "").strip()
            if tmdb_key:
                client = TmdbClient(
                    tmdb_key,
                    language=str(strm_config.get("tmdb_language") or "it-IT"),
                    rate_limit=int(strm_config.get("tmdb_rate_limit") or 40),
                )
                tmdb_match, _hint = _resolve_series_naming(
                    series_name, client, strm_config
                )
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"tmdb:{exc}")

    # Prefer folder-hint TMDB id over a mismatched search result.
    hint_tmdb = _folder_tmdb_id(series_folder_hint)
    if hint_tmdb and (
        not tmdb_match or int(tmdb_match.get("tmdb_id") or 0) != hint_tmdb
    ):
        year = extract_title_year(series_folder_hint)
        title = re.sub(
            r"\s*\[tmdbid-\d+\]\s*$",
            "",
            series_folder_hint or "",
            flags=re.IGNORECASE,
        ).strip()
        title = re.sub(r"\s*\(\d{4}\)\s*$", "", title).strip() or series_name
        tmdb_match = {"title": title, "year": year, "tmdb_id": hint_tmdb}

    series = find_xtream_series(host, user, password, series_name, allow_4k=allow_4k)
    if not series:
        series = find_xtream_series(
            host, user, password, series_folder_hint or series_name, allow_4k=allow_4k
        )
    if not series:
        result["errors"].append("xtream_series_not_found")
        return result

    try:
        info = get_series_info(host, user, password, series["series_id"])
    except RuntimeError as exc:
        result["errors"].append(f"xtream_info:{exc}")
        return result
    if not info or "episodes" not in info:
        result["errors"].append("xtream_episodes_missing")
        return result

    episodes_map = normalize_episodes_map(info.get("episodes"))
    wanted = {(int(ep["season"]), int(ep["episode"])) for ep in episodes}

    for season, episode in sorted(wanted):
        season_eps = episodes_map.get(str(season), [])
        xtream_ep = None
        for ep in season_eps:
            if int(ep.get("episode_num", -1)) == episode:
                xtream_ep = ep
                break
        if not xtream_ep or xtream_ep.get("id") is None:
            result["missing"].append(f"S{season:02d}E{episode:02d}")
            continue

        ext = str(xtream_ep.get("container_extension") or "mp4")
        remote_url = build_episode_stream_url(host, user, password, xtream_ep["id"], ext)
        strm_path = _resolve_restore_strm_path(
            series_name=series_name,
            series_folder_hint=series_folder_hint,
            season=season,
            episode=episode,
            series_output=series_output,
            tmdb_match=tmdb_match,
        )
        try:
            from stream_proxy import resolve_episode_play_url

            series_folder = (
                series_folder_hint
                or resolve_series_folder_name(series_name, strm_path)
                or series_name
            )
            url = resolve_episode_play_url(
                series_folder=series_folder,
                season=season,
                episode=episode,
                remote_url=remote_url,
                strm_path=strm_path,
                ext=ext,
                config=load_auto_download_config(),
            )
        except ImportError:
            url = remote_url
        try:
            prepare_output_dir(os.path.dirname(strm_path))
            existed = os.path.isfile(strm_path)
            changed = write_strm(strm_path, url)
            result["targets"].append(strm_path)
            if changed and not existed:
                result["created"].append(strm_path)
            elif changed:
                result["updated"].append(strm_path)
            else:
                result["skipped"] += 1
            nfo = align_episode_nfo_to_media(strm_path)
            if not nfo.get("skipped"):
                result["nfo_aligned"] += 1
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"S{season:02d}E{episode:02d}:{exc}")

    return result


def _is_subtitle_filename(name: str) -> bool:
    lower = (name or "").lower()
    return any(lower.endswith(ext) for ext in SUBTITLE_EXTENSIONS)


def _subtitle_suffix_after_episode(name: str) -> str:
    """Return language/ext suffix after SxxExx, e.g. '.ita.srt' or ' [LOCAL].ita.srt'."""
    match = EPISODE_TAG_RE.search(name or "")
    if not match:
        ext = os.path.splitext(name or "")[1]
        return ext if ext else ".srt"
    return (name or "")[match.end() :]


def _strip_local_marker(name: str) -> str:
    return (name or "").replace(LOCAL_DOWNLOAD_MARKER, "")


def collect_subtitle_files(paths: list[str]) -> list[dict]:
    """Inventory subtitle sidecars under download folders (kept across local delete)."""
    found: list[dict] = []
    seen: set[str] = set()
    for root in paths:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if not _is_subtitle_filename(name):
                    continue
                match = EPISODE_TAG_RE.search(name)
                if not match:
                    continue
                full = os.path.realpath(os.path.join(dirpath, name))
                if full in seen:
                    continue
                seen.add(full)
                found.append(
                    {
                        "season": int(match.group(1)),
                        "episode": int(match.group(2)),
                        "path": full,
                        "suffix": _strip_local_marker(_subtitle_suffix_after_episode(name)),
                    }
                )
    return found


def align_subtitles_to_strm(
    subtitles: list[dict],
    strm_paths: list[str],
) -> list[str]:
    """Move/rename kept subtitles beside restored .strm using matching SxxExx names."""
    by_ep: dict[tuple[int, int], str] = {}
    for strm_path in strm_paths:
        if not strm_path:
            continue
        match = EPISODE_TAG_RE.search(os.path.basename(strm_path))
        if not match:
            continue
        by_ep[(int(match.group(1)), int(match.group(2)))] = strm_path

    moved: list[str] = []
    for sub in subtitles:
        key = (int(sub.get("season") or 0), int(sub.get("episode") or 0))
        strm_path = by_ep.get(key)
        src = str(sub.get("path") or "")
        if not strm_path or not src or not os.path.isfile(src):
            continue
        suffix = str(sub.get("suffix") or ".srt")
        if not suffix.startswith("."):
            suffix = "." + suffix
        dest = os.path.splitext(strm_path)[0] + suffix
        dest_dir = os.path.dirname(dest)
        try:
            prepare_output_dir(dest_dir)
            if os.path.realpath(src) == os.path.realpath(dest):
                continue
            if os.path.exists(dest):
                # Prefer keeping an existing non-LOCAL sidecar; drop the duplicate.
                if os.path.realpath(src) != os.path.realpath(dest):
                    os.remove(src)
                continue
            shutil.move(src, dest)
            moved.append(dest)
        except OSError:
            continue
    return moved


def _rmdir_if_empty(path: str) -> bool:
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
            return True
    except OSError:
        pass
    return False


def delete_series_downloads(paths: list[str]) -> list[str]:
    """Delete local video downloads (and nfo/image sidecars); keep subtitles and .strm."""
    allowed_roots = [os.path.realpath(root) for root in SERIES_DOWNLOAD_ROOTS]
    deleted: list[str] = []
    for path in paths:
        real_path = os.path.realpath(path)
        if not any(
            real_path == root or real_path.startswith(root + os.sep) for root in allowed_roots
        ):
            continue
        if not os.path.isdir(real_path):
            continue
        for dirpath, _dirs, files in os.walk(real_path, topdown=False):
            for name in files:
                full = os.path.join(dirpath, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if _is_subtitle_filename(name) or ext == ".strm":
                    continue
                if ext in VIDEO_EXTENSIONS:
                    deleted.extend(_remove_path_and_sidecars(full))
                    continue
                # Leftover LOCAL-tagged nfo/thumbs not paired with a video basename.
                if LOCAL_DOWNLOAD_MARKER in name and ext in _IMAGE_SIDECAR_EXTENSIONS:
                    try:
                        os.remove(full)
                        deleted.append(os.path.realpath(full))
                    except OSError:
                        pass
            _rmdir_if_empty(dirpath)
        if _rmdir_if_empty(real_path):
            deleted.append(real_path)
            parent = os.path.dirname(real_path)
            if any(
                parent == root or parent.startswith(root + os.sep) for root in allowed_roots
            ):
                if _rmdir_if_empty(parent):
                    deleted.append(parent)
    return deleted


def _series_folder_hint_from_paths(paths: list[str]) -> str:
    """Series folder name for STRM restore (parent of Season XX when deleting one season)."""
    for path in paths:
        try:
            real = os.path.realpath(path)
        except OSError:
            real = str(path or "").rstrip("/")
        base = os.path.basename(real)
        if SEASON_DIR_RE.match(base):
            return os.path.basename(os.path.dirname(real))
        if base:
            return base
    return ""


def delete_series_downloads_and_restore_strm(
    paths: list[str],
    *,
    series_name: str,
    notify: bool = True,
) -> dict:
    """Delete local downloads, recreate STRMs for those episodes, align NFOs, notify JF/Emby.

    Subtitle sidecars (.srt/.ass/…) are preserved and renamed next to the restored .strm.
    """
    episodes = inventory_local_episodes(paths)
    folder_hint = _series_folder_hint_from_paths(paths)
    subtitles = collect_subtitle_files(paths)

    deleted = delete_series_downloads(paths)
    restore = restore_strm_for_episodes(
        series_name,
        episodes,
        series_folder_hint=folder_hint,
    )
    strm_paths = list(restore.get("targets") or []) or (
        list(restore.get("created") or []) + list(restore.get("updated") or [])
    )
    restore["subtitles_moved"] = align_subtitles_to_strm(subtitles, strm_paths)

    notify_notes: list[str] = []
    if notify and (deleted or restore.get("created") or restore.get("updated")):
        notify_path = ""
        created = list(restore.get("created") or []) + list(restore.get("updated") or [])
        if created:
            notify_path = created[0]
        elif deleted:
            notify_path = os.path.join(
                SERIES_DOWNLOAD_PATHS[0], folder_hint or sanitize_filename(series_name)
            )
        try:
            notify_notes = notify_media_servers_after_local_download(
                notify_path,
                deleted_paths=list(deleted),
            )
        except Exception as exc:  # noqa: BLE001
            notify_notes = [f"notify_error:{exc}"]

    return {
        "deleted": deleted,
        "episodes": episodes,
        "restore": restore,
        "notify": notify_notes,
    }


def _best_series_match(folder_name: str, candidates: list) -> dict | None:
    folder_lower = folder_name.lower().strip()
    for item in candidates:
        name = str(item.get("Name") or "").strip()
        if name.lower() == folder_lower:
            return item
    for item in candidates:
        name = str(item.get("Name") or "").strip()
        if not name:
            continue
        name_lower = name.lower()
        if name_lower in folder_lower or folder_lower.startswith(name_lower):
            return item
    return candidates[0] if len(candidates) == 1 else None


def _folder_display_name(folder_name: str) -> str:
    name = re.sub(r"\s*\[tmdbid-\d+\]\s*$", "", folder_name or "", flags=re.IGNORECASE).strip()
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip()
    return name or (folder_name or "").strip()


def _resolve_scan_series_match(media, user_id: str, folder_name: str) -> dict | None:
    """Map a local download folder to a media-server series id/name."""
    tmdb_id = _folder_tmdb_id(folder_name)
    server_type = str(getattr(media, "server_type", "") or "").strip().lower()
    display = _folder_display_name(folder_name)
    if tmdb_id and server_type == "silo":
        # Prefer deterministic Silo ids from the folder TMDB tag — no extra API call.
        return {"Id": f"series-tmdb-{tmdb_id}", "Name": display}

    search_term = display.split(" (")[0].strip() or display
    candidates = media.search_series(user_id, search_term)
    match = _best_series_match(folder_name, candidates)
    if match:
        return match
    return None


def clear_deletion_dismissals() -> int:
    """Forget 'No' answers so completed series can be offered again."""
    data = load_deletion_prompts()
    dismissed = data.get("dismissed") or []
    count = len(dismissed) if isinstance(dismissed, list) else 0
    data["dismissed"] = []
    save_deletion_prompts(data)
    return count


def scan_completed_series_prompts(media, user_id: str) -> int:
    """Queue deletion prompts for ended series or fully watched local seasons."""
    data = load_deletion_prompts()
    dismissed = set(data.get("dismissed", []))
    pending_identities = {_prompt_identity(p) for p in data.get("pending", []) if isinstance(p, dict)}
    for p in data.get("pending", []):
        if not isinstance(p, dict):
            continue
        pending_identities.add(str(p.get("series_id") or ""))
        for alt in p.get("alternate_series_ids") or []:
            pending_identities.add(deletion_prompt_identity(str(alt), _prompt_season_number(p)))
    pending_paths = set()
    for p in data.get("pending", []):
        pending_paths |= _prompt_path_keys(p.get("paths"))
    seen_folders: set[str] = set()
    added = 0

    for dest in SERIES_DOWNLOAD_ROOTS:
        if not os.path.isdir(dest):
            continue
        for folder_name in os.listdir(dest):
            folder = os.path.join(dest, folder_name)
            real_folder = os.path.realpath(folder)
            if real_folder in seen_folders or not folder_has_video_files(folder):
                continue
            seen_folders.add(real_folder)

            tmdb_id = _folder_tmdb_id(folder_name)
            ended = series_production_finished([folder], tmdb_id=tmdb_id)
            local_seasons = iter_local_season_folders(folder)
            # Skip airing shows with no Season XX downloads — nothing to free yet.
            if not ended and not local_seasons:
                continue

            match = _resolve_scan_series_match(media, user_id, folder_name)
            if not match:
                continue

            series_id = str(match.get("Id") or "")
            if not series_id:
                continue
            series_name = str(match.get("Name") or folder_name).strip()

            try:
                episodes = media.get_series_episodes(
                    user_id, series_id, include_user_data=True, resolve_paths=False
                )
            except TypeError:
                # Emby/Jellyfin clients do not take resolve_paths.
                try:
                    episodes = media.get_series_episodes(
                        user_id, series_id, include_user_data=True
                    )
                except Exception:
                    episodes = []
            except Exception:
                episodes = []
            if not episodes:
                # Transient empty catalog responses — brief pause + retry.
                time.sleep(0.5)
                try:
                    episodes = media.get_series_episodes(
                        user_id, series_id, include_user_data=True, resolve_paths=False
                    )
                except TypeError:
                    try:
                        episodes = media.get_series_episodes(
                            user_id, series_id, include_user_data=True
                        )
                    except Exception:
                        continue
                except Exception:
                    continue
            if not episodes:
                continue

            # Whole series: only when TMDB says ended and every library episode is watched.
            if ended and is_series_fully_watched(episodes):
                if (
                    series_id not in dismissed
                    and deletion_prompt_identity(series_id) not in dismissed
                    and series_id not in pending_identities
                    and real_folder not in pending_paths
                ):
                    paths = find_series_download_paths(series_name) or [folder]
                    if add_deletion_prompt(series_id, series_name, paths):
                        added += 1
                        pending_identities.add(series_id)
                        pending_paths |= _prompt_path_keys(paths)
                continue

            # Still airing (or not fully watched): offer each fully watched local season.
            for season_num, season_path in local_seasons:
                season_real = os.path.realpath(season_path)
                identity = deletion_prompt_identity(series_id, season_num)
                if identity in dismissed or identity in pending_identities:
                    continue
                if season_real in pending_paths:
                    continue
                if not is_season_fully_watched(episodes, season_num):
                    continue
                if add_deletion_prompt(
                    series_id, series_name, [season_path], season=season_num
                ):
                    added += 1
                    pending_identities.add(identity)
                    pending_paths.add(season_real)

    return added

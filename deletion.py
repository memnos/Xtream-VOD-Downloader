import json
import os
import shutil
from datetime import datetime

from core import DATA_DIR, SERIES_DOWNLOAD_PATHS, load_json_file, sanitize_filename

DELETION_PROMPTS_FILE = os.environ.get(
    "DELETION_PROMPTS_FILE", os.path.join(DATA_DIR, "deletion_prompts.json")
)
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}
SERIES_DOWNLOAD_ROOTS = SERIES_DOWNLOAD_PATHS


def _save_json_file(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.chmod(path, 0o600)


def _default_deletion_prompts() -> dict:
    return {"pending": [], "dismissed": []}


def load_deletion_prompts() -> dict:
    data = load_json_file(DELETION_PROMPTS_FILE, _default_deletion_prompts())
    if not isinstance(data, dict):
        return _default_deletion_prompts()
    pending = data.get("pending", [])
    dismissed = data.get("dismissed", [])
    return {
        "pending": pending if isinstance(pending, list) else [],
        "dismissed": dismissed if isinstance(dismissed, list) else [],
    }


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
    safe_name = sanitize_filename(series_name)
    if not safe_name:
        return []
    paths = []
    for dest in SERIES_DOWNLOAD_ROOTS:
        folder = os.path.join(dest, safe_name)
        if folder_has_video_files(folder):
            paths.append(folder)
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


def add_deletion_prompt(series_id: str, series_name: str, paths: list[str]) -> bool:
    if not paths:
        return False
    data = load_deletion_prompts()
    dismissed = set(data.get("dismissed", []))
    if series_id in dismissed:
        return False
    for item in data.get("pending", []):
        if item.get("series_id") == series_id:
            return False
    data["pending"].append(
        {
            "series_id": series_id,
            "series_name": series_name,
            "paths": paths,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    save_deletion_prompts(data)
    return True


def dismiss_deletion_prompt(series_id: str) -> None:
    data = load_deletion_prompts()
    dismissed = set(data.get("dismissed", []))
    dismissed.add(series_id)
    data["dismissed"] = sorted(dismissed)
    data["pending"] = [p for p in data.get("pending", []) if p.get("series_id") != series_id]
    save_deletion_prompts(data)


def remove_deletion_prompt(series_id: str) -> dict | None:
    data = load_deletion_prompts()
    pending = data.get("pending", [])
    match = next((p for p in pending if p.get("series_id") == series_id), None)
    data["pending"] = [p for p in pending if p.get("series_id") != series_id]
    save_deletion_prompts(data)
    return match


def delete_series_downloads(paths: list[str]) -> list[str]:
    allowed_roots = [os.path.realpath(root) for root in SERIES_DOWNLOAD_ROOTS]
    deleted = []
    for path in paths:
        real_path = os.path.realpath(path)
        if not any(
            real_path == root or real_path.startswith(root + os.sep) for root in allowed_roots
        ):
            continue
        if os.path.isdir(real_path):
            shutil.rmtree(real_path)
            deleted.append(real_path)
    return deleted

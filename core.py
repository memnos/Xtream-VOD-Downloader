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
MAX_DOWNLOAD_HISTORY = 20
DIR_MODE = 0o777
FILE_MODE = 0o664
DOWNLOAD_ROOTS = (
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
    DOWNLOAD_TV2_PATH,
)
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".ts", ".webm"}


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
        "emby_url": os.environ.get("EMBY_URL", ""),
        "emby_api_key": os.environ.get("EMBY_API_KEY", ""),
        "emby_username": os.environ.get("EMBY_USERNAME", ""),
        "series_dest": DEFAULT_SERIES_DEST,
        "cooldown_seconds": int(os.environ.get("AUTO_COOLDOWN_SECONDS", "90")),
        "poll_interval_seconds": int(os.environ.get("AUTO_POLL_INTERVAL_SECONDS", "20")),
        "prompt_delete_completed": True,
    }


def load_auto_download_config() -> dict:
    defaults = default_auto_download_config()
    data = load_json_file(AUTO_DOWNLOAD_FILE, defaults)
    if not isinstance(data, dict):
        return defaults
    merged = {**defaults, **data}
    if merged["series_dest"] not in DOWNLOAD_CONFIG.values():
        merged["series_dest"] = DEFAULT_SERIES_DEST
    return merged


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


def find_xtream_series(host: str, user: str, password: str, series_name: str) -> dict | None:
    target = normalize_title(series_name)
    if not target:
        return None

    catalog = fetch_series_catalog(host, user, password)
    exact = [s for s in catalog if normalize_title(s.get("name", "")) == target]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return min(exact, key=lambda s: len(s.get("name", "")))

    partial = [
        s
        for s in catalog
        if target in normalize_title(s.get("name", ""))
        or normalize_title(s.get("name", "")) in target
    ]
    if not partial:
        return None
    return min(partial, key=lambda s: abs(len(normalize_title(s.get("name", ""))) - len(target)))


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
) -> list[dict]:
    series = find_xtream_series(host, user, password, series_name)
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
) -> dict | None:
    series = find_xtream_series(host, user, password, series_name)
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
) -> tuple[str, str]:
    safe_series = sanitize_filename(series_name)
    path = os.path.join(dest_root, safe_series, f"Season {int(season):02d}")
    filename = f"{safe_series} - S{int(season):02d}E{int(episode):02d}.{ext}"
    return path, os.path.join(path, filename)


def build_movie_output(movie_name: str, ext: str, dest_root: str) -> tuple[str, str]:
    safe_name = sanitize_filename(movie_name)
    path = os.path.join(dest_root, safe_name)
    return path, os.path.join(path, f"{safe_name}.{ext}")


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

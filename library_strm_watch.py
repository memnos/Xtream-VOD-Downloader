"""Remove .strm entries when a finished video appears in the local library.

Xtream already does this after its own download. Mediarium (and anything else
that drops an mkv/mp4 into the movie or series library) does not go through
that path, and episode releases without the ``[LOCAL]`` marker were ignored.
This watcher sees those files once they stop growing and runs the same
cleanup, then asks Silo to rescan the folders it can see.
"""
from __future__ import annotations

import json
import os
import re
import time

import core
from core import (
    VIDEO_EXTENSIONS,
    _is_trailer_filename,
    delete_strm_after_local_download,
    load_auto_download_config,
    notify_media_servers_after_local_download,
    parse_episode_numbers_from_path,
)

MIN_VIDEO_BYTES = 5 * 1024 * 1024
SETTLE_SECONDS = 30
_SAMPLE_RE = re.compile(
    r"(?:^|[\s._\-\[(])sample(?:[\s._\-\])]|$)", re.IGNORECASE
)
_LIBRARY_ROOTS = (
    ("movies", "DOWNLOAD_MOVIES_PATH"),
    ("movies", "STRM_OUTPUT_MOVIES_PATH"),
    ("series", "DOWNLOAD_TV_PATH"),
    ("series", "STRM_OUTPUT_SERIES_PATH"),
)


def _state_path() -> str:
    return os.path.join(core.DATA_DIR, "library_strm_watch.json")


def _load_state() -> dict:
    try:
        with open(_state_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"watermark": 0.0, "pending": {}, "seen": {}}
    if not isinstance(data, dict):
        return {"watermark": 0.0, "pending": {}, "seen": {}}
    data.setdefault("watermark", 0.0)
    if not isinstance(data.get("pending"), dict):
        data["pending"] = {}
    if not isinstance(data.get("seen"), dict):
        data["seen"] = {}
    return data


def _save_state(state: dict) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    os.replace(temporary, path)


def _dir_mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def _file_size(path: str) -> int:
    try:
        if os.path.isfile(path):
            return int(os.path.getsize(path))
    except OSError:
        return 0
    return 0


def _interesting_video(path: str) -> bool:
    name = os.path.basename(path)
    if not name or name.startswith("."):
        return False
    ext = os.path.splitext(name)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        return False
    if _is_trailer_filename(name):
        return False
    if _SAMPLE_RE.search(os.path.splitext(name)[0]):
        return False
    if _file_size(path) < MIN_VIDEO_BYTES:
        return False
    if os.path.isfile(path + ".part"):
        return False
    return True


def _videos_in(directory: str) -> list[str]:
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found: list[str] = []
    for name in names:
        full = os.path.join(directory, name)
        if _interesting_video(full):
            found.append(full)
    return found


def _root_path(attr: str) -> str:
    return os.path.realpath(getattr(core, attr))


def library_kind(path: str) -> str | None:
    real = os.path.realpath(path)
    for kind, attr in _LIBRARY_ROOTS:
        base = _root_path(attr)
        if real == base or real.startswith(base + os.sep):
            return kind
    return None


def relative_library_dir(path: str) -> tuple[str, str] | None:
    """Return (movies|series, path under that library) for a file or folder.

    The library root itself is not a scan target.
    """
    real = os.path.realpath(path)
    # A removed .strm is no longer a file. Scan the folder that held it.
    if not os.path.isdir(real):
        real = os.path.dirname(real)
    for kind, attr in _LIBRARY_ROOTS:
        base = _root_path(attr)
        if real != base and not real.startswith(base + os.sep):
            continue
        relative = os.path.relpath(real, base)
        if relative in ("", "."):
            return None
        return kind, relative.replace(os.sep, "/")
    return None


def silo_scan_path(kind: str, relative: str, libraries: list[dict]) -> str | None:
    """Map a library-relative directory onto the path Silo has configured."""
    want = "movie" if kind == "movies" else "series"
    relative = (relative or "").strip("/")
    if not relative:
        return None
    for lib in libraries:
        if not isinstance(lib, dict) or lib.get("enabled") is False:
            continue
        library_type = str(lib.get("type") or lib.get("Type") or "").lower()
        if not library_type.startswith(want):
            continue
        paths = lib.get("paths") or lib.get("Paths") or []
        if not paths:
            continue
        root = str(paths[0]).replace("\\", "/").rstrip("/")
        if not root:
            continue
        return f"{root}/{relative}"
    return None


def discover_library_videos(watermark: float) -> list[str]:
    """Videos under movie folders or season folders touched since watermark.

    A watermark of 0 walks the whole library once. Later ticks only reopen a
    folder whose own mtime moved (a new or replaced file).
    """
    found: list[str] = []
    movies = _root_path("DOWNLOAD_MOVIES_PATH")
    if os.path.isdir(movies):
        try:
            folders = os.listdir(movies)
        except OSError:
            folders = []
        for name in folders:
            folder = os.path.join(movies, name)
            if not os.path.isdir(folder):
                continue
            if watermark and _dir_mtime(folder) < watermark:
                continue
            found.extend(_videos_in(folder))

    tv = _root_path("DOWNLOAD_TV_PATH")
    if os.path.isdir(tv):
        try:
            shows = os.listdir(tv)
        except OSError:
            shows = []
        for show in shows:
            show_dir = os.path.join(tv, show)
            if not os.path.isdir(show_dir):
                continue
            try:
                children = os.listdir(show_dir)
            except OSError:
                continue
            for child in children:
                child_path = os.path.join(show_dir, child)
                if os.path.isdir(child_path):
                    if watermark and _dir_mtime(child_path) < watermark:
                        continue
                    for video in _videos_in(child_path):
                        if parse_episode_numbers_from_path(video) is None:
                            continue
                        found.append(video)
                    continue
                if watermark and _dir_mtime(show_dir) < watermark:
                    continue
                if (
                    _interesting_video(child_path)
                    and parse_episode_numbers_from_path(child_path) is not None
                ):
                    found.append(child_path)
    return found


def _silo_client():
    config = load_auto_download_config()
    if not config.get("silo_enabled"):
        return None
    url = str(config.get("silo_url") or "").strip()
    api_key = str(config.get("silo_api_key") or "").strip()
    if not url or not api_key:
        return None
    from silo_client import SiloClient

    return SiloClient(url, api_key)


def request_silo_rescan(paths: list[str]) -> None:
    """Ask Silo to rescan each local or .strm folder that changed."""
    client = _silo_client()
    if client is None:
        return
    try:
        libraries = client.list_libraries()
    except Exception as exc:
        print(f"[library_strm] Silo: elenco librerie non letto ({exc})", flush=True)
        return
    targets: list[str] = []
    for path in paths:
        parsed = relative_library_dir(path)
        if parsed is None:
            continue
        target = silo_scan_path(parsed[0], parsed[1], libraries)
        if target and target not in targets:
            targets.append(target)
    for target in targets:
        try:
            client.request_scan(target)
            print(f"[library_strm] Silo: scansione {target}", flush=True)
        except Exception as exc:
            print(f"[library_strm] Silo: scansione non avviata ({target}): {exc}", flush=True)


def cleanup_superseded_strm(local_path: str) -> list[str]:
    """Delete .strm replaced by this local video and tell the media servers."""
    real = os.path.realpath(local_path)
    kind = library_kind(real)
    if kind is None:
        return []
    if kind == "series" and parse_episode_numbers_from_path(real) is None:
        return []
    deleted = delete_strm_after_local_download(real, allow_unmarked=True)
    strm_deleted = [path for path in deleted if path.lower().endswith(".strm")]
    if not strm_deleted:
        return deleted
    print(
        "[library_strm] Rimosso .strm, file locale presente: "
        f"{os.path.basename(real)} ({len(strm_deleted)})",
        flush=True,
    )
    try:
        notify_media_servers_after_local_download(real, deleted_paths=deleted)
    except Exception as exc:
        print(f"[library_strm] Notifica media server: {exc}", flush=True)
    try:
        request_silo_rescan([real, *strm_deleted])
    except Exception as exc:
        print(f"[library_strm] Silo: {exc}", flush=True)
    return deleted


def tick_library_strm_watch() -> dict:
    """Notice settled library videos and drop the .strm they replace."""
    state = _load_state()
    now = time.time()
    try:
        watermark = float(state.get("watermark") or 0)
    except (TypeError, ValueError):
        watermark = 0.0
    pending = state["pending"]
    seen = state["seen"]
    removed = 0

    for path in discover_library_videos(watermark):
        try:
            real = os.path.realpath(path)
        except OSError:
            real = path
        size = _file_size(real)
        if size < MIN_VIDEO_BYTES:
            continue
        previous = seen.get(real)
        if isinstance(previous, dict) and int(previous.get("size") or 0) == size:
            pending.pop(real, None)
            continue
        slot = pending.get(real)
        if not isinstance(slot, dict) or int(slot.get("size") or -1) != size:
            pending[real] = {"size": size, "stable_since": now}

    for path in list(pending):
        if not os.path.isfile(path):
            pending.pop(path, None)
            continue
        size = _file_size(path)
        slot = pending.get(path) or {}
        if int(slot.get("size") or -1) != size:
            pending[path] = {"size": size, "stable_since": now}
            continue
        try:
            stable_since = float(slot.get("stable_since") or now)
        except (TypeError, ValueError):
            stable_since = now
        if now - stable_since < SETTLE_SECONDS:
            continue
        pending.pop(path, None)
        try:
            deleted = cleanup_superseded_strm(path)
        except Exception as exc:
            print(f"[library_strm] {path}: {exc}", flush=True)
            pending[path] = {"size": size, "stable_since": now}
            continue
        removed += sum(1 for item in deleted if item.lower().endswith(".strm"))
        seen[path] = {"size": size}

    state["watermark"] = now
    state["pending"] = pending
    state["seen"] = seen
    try:
        _save_state(state)
    except OSError as exc:
        print(f"[library_strm] Stato non salvato: {exc}", flush=True)
    return {"removed": removed, "pending": len(pending)}

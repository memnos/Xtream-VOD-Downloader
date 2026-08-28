"""Progressive HTTP proxy for movie and episode .strm playback.

GuamaFlix cannot switch mid-play via remote control. Instead, .strm files point
here; on Play the proxy relays Xtream (passthrough) or optionally downloads to
[LOCAL] and serves Range requests for bytes already on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import requests

from core import (
    DATA_DIR,
    DEFAULT_SERIES_DEST,
    DOWNLOAD_MOVIES_PATH,
    DOWNLOAD_TV_PATH,
    LOCAL_DOWNLOAD_MARKER,
    MOVIE_STRM_URL_SIDECAR,
    build_episode_output,
    build_movie_output,
    growing_download_bytes,
    load_auto_download_config,
    load_json_file,
    prepare_output_dir,
    write_movie_strm_url_sidecar,
)

PROXY_REGISTRY_FILE = os.environ.get(
    "PROXY_REGISTRY_FILE", os.path.join(DATA_DIR, "proxy_registry.json")
)
DEFAULT_PROXY_PORT = 8510
DEFAULT_BITRATE_BPS = 2_000_000
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_JOBS_LOCK = threading.RLock()
_JOBS: dict[str, "ProxyJob"] = {}
_REGISTRY_LOCK = threading.RLock()
_REGISTRY_CACHE: dict | None = None
_REGISTRY_DIRTY = 0
_REGISTRY_FLUSH_EVERY = 250


def movie_proxy_key(folder_name: str) -> str:
    return hashlib.sha256(str(folder_name or "").encode("utf-8")).hexdigest()[:20]


def episode_proxy_key(series_folder: str, season: int, episode: int) -> str:
    token = f"{series_folder}|S{int(season):02d}E{int(episode):02d}"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]


def _series_dest(config: dict | None = None) -> str:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    dest = str(cfg.get("series_dest") or "").strip()
    return dest or DEFAULT_SERIES_DEST or DOWNLOAD_TV_PATH


def stream_proxy_enabled(config: dict | None = None) -> bool:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    return bool(cfg.get("stream_proxy_enabled"))


def stream_proxy_host(config: dict | None = None) -> str:
    """Host/IP reachable by the TV client (LAN or Tailscale). Empty = proxy URLs disabled."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    return str(cfg.get("stream_proxy_host") or os.environ.get("STREAM_PROXY_HOST") or "").strip()


def stream_proxy_port(config: dict | None = None) -> int:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    try:
        return max(1, int(cfg.get("stream_proxy_port") or DEFAULT_PROXY_PORT))
    except (TypeError, ValueError):
        return DEFAULT_PROXY_PORT


def stream_proxy_download_enabled(config: dict | None = None) -> bool:
    """When False, proxy only passthroughs Xtream (no [LOCAL] download)."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    return bool(cfg.get("stream_proxy_download", False))


def is_stream_proxy_url(url: str) -> bool:
    text = (url or "").strip().lower()
    if not text:
        return False
    return "/p/movie/" in text or "/p/episode/" in text


def build_movie_proxy_url(
    folder_name: str,
    *,
    config: dict | None = None,
) -> str:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    host = stream_proxy_host(cfg)
    if not host:
        raise ValueError("stream_proxy_host is not configured")
    port = stream_proxy_port(cfg)
    key = movie_proxy_key(folder_name)
    return f"http://{host}:{port}/p/movie/{key}"


def build_episode_proxy_url(
    series_folder: str,
    season: int,
    episode: int,
    *,
    config: dict | None = None,
) -> str:
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    host = stream_proxy_host(cfg)
    if not host:
        raise ValueError("stream_proxy_host is not configured")
    port = stream_proxy_port(cfg)
    key = episode_proxy_key(series_folder, season, episode)
    return f"http://{host}:{port}/p/episode/{key}"


def build_episode_sub_proxy_url(
    series_folder: str,
    season: int,
    episode: int,
    *,
    config: dict | None = None,
) -> str:
    """HTTP URL for the Italian sidecar next to the episode .strm (for JF clients)."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    host = stream_proxy_host(cfg)
    if not host:
        raise ValueError("stream_proxy_host is not configured")
    port = stream_proxy_port(cfg)
    key = episode_proxy_key(series_folder, season, episode)
    return f"http://{host}:{port}/p/sub/episode/{key}.srt"


def resolve_episode_subtitle_path(entry: dict) -> str | None:
    """Find .ita.srt / .it.srt beside strm (or download branch) for a proxy entry."""
    candidates: list[str] = []
    strm = str(entry.get("strm_path") or "").strip()
    if strm:
        base, _ext = os.path.splitext(strm)
        for suf in (".ita.srt", ".it.srt", ".ita.forced.srt", ".it.forced.srt"):
            candidates.append(base + suf)
        folder = os.path.dirname(strm)
        name = os.path.basename(base)
        if os.path.isdir(folder):
            try:
                for fn in os.listdir(folder):
                    lower = fn.lower()
                    if name.lower() in lower and lower.endswith(".srt") and (
                        ".ita" in lower or ".it." in lower or lower.endswith(".it.srt")
                    ):
                        candidates.append(os.path.join(folder, fn))
            except OSError:
                pass
    series_folder = str(entry.get("series_folder") or "").strip()
    season = entry.get("season")
    episode = entry.get("episode")
    if series_folder and season is not None and episode is not None:
        season_dir = os.path.join(
            DOWNLOAD_TV_PATH, series_folder, f"Season {int(season):02d}"
        )
        needle = f"S{int(season):02d}E{int(episode):02d}".lower()
        if os.path.isdir(season_dir):
            try:
                for fn in os.listdir(season_dir):
                    lower = fn.lower()
                    if needle in lower and lower.endswith(".srt") and (
                        ".ita" in lower or ".it." in lower
                    ):
                        candidates.append(os.path.join(season_dir, fn))
            except OSError:
                pass
    seen: set[str] = set()
    for path in candidates:
        real = os.path.realpath(path) if path else ""
        if not real or real in seen:
            continue
        seen.add(real)
        if os.path.isfile(real) and os.path.getsize(real) > 20:
            return real
    return None


def _load_registry() -> dict:
    """Return registry dict (cached in-process). Caller must hold _REGISTRY_LOCK."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is not None:
        return _REGISTRY_CACHE
    data = load_json_file(PROXY_REGISTRY_FILE, {"movies": {}, "episodes": {}})
    if not isinstance(data, dict):
        _REGISTRY_CACHE = {"movies": {}, "episodes": {}}
        return _REGISTRY_CACHE
    movies = data.get("movies")
    if not isinstance(movies, dict):
        movies = {}
    episodes = data.get("episodes")
    if not isinstance(episodes, dict):
        episodes = {}
    _REGISTRY_CACHE = {"movies": movies, "episodes": episodes}
    return _REGISTRY_CACHE


def _save_registry(data: dict) -> None:
    """Persist registry to disk. Caller must hold _REGISTRY_LOCK."""
    global _REGISTRY_CACHE, _REGISTRY_DIRTY
    _REGISTRY_CACHE = data
    os.makedirs(os.path.dirname(PROXY_REGISTRY_FILE) or ".", exist_ok=True)
    tmp = PROXY_REGISTRY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        # Compact JSON: registry is large; indent made sync rewrite every episode unbearable.
        json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, PROXY_REGISTRY_FILE)
    try:
        os.chmod(PROXY_REGISTRY_FILE, 0o600)
    except OSError:
        pass
    _REGISTRY_DIRTY = 0


def flush_proxy_registry() -> None:
    """Force-write any dirty in-memory registry entries to disk."""
    global _REGISTRY_DIRTY
    with _REGISTRY_LOCK:
        if _REGISTRY_CACHE is None or _REGISTRY_DIRTY <= 0:
            return
        _save_registry(_REGISTRY_CACHE)


def _registry_entry_unchanged(prev: dict | None, entry: dict, keys: tuple[str, ...]) -> bool:
    if not isinstance(prev, dict):
        return False
    return all(prev.get(k) == entry.get(k) for k in keys)

def register_movie_proxy(
    *,
    folder_name: str,
    remote_url: str,
    local_path: str,
    strm_path: str = "",
    bitrate_bps: int = 0,
    ext: str = "mkv",
    write_sidecar: bool = True,
    save: bool = True,
) -> str:
    """Register a movie for proxy playback; returns the proxy key."""
    folder_name = str(folder_name or "").strip()
    remote_url = str(remote_url or "").strip()
    if not folder_name or not remote_url:
        raise ValueError("folder_name and remote_url are required")
    key = movie_proxy_key(folder_name)
    if not local_path:
        _folder, local_path = build_movie_output(
            folder_name, ext or "mkv", DOWNLOAD_MOVIES_PATH
        )
    entry = {
        "key": key,
        "kind": "movie",
        "folder": folder_name,
        "remote_url": remote_url,
        "local_path": os.path.realpath(local_path),
        "strm_path": os.path.realpath(strm_path) if strm_path else "",
        "bitrate_bps": int(bitrate_bps or 0),
        "ext": str(ext or "mkv").lstrip("."),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _REGISTRY_LOCK:
        global _REGISTRY_DIRTY
        data = _load_registry()
        prev = data["movies"].get(key)
        if _registry_entry_unchanged(
            prev,
            entry,
            ("remote_url", "local_path", "strm_path", "ext"),
        ):
            return key
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["movies"][key] = entry
        _REGISTRY_DIRTY += 1
        if save and _REGISTRY_DIRTY >= _REGISTRY_FLUSH_EVERY:
            _save_registry(data)
    if write_sidecar:
        write_movie_strm_url_sidecar(local_path, remote_url, strm_path=strm_path or None)
    return key


def register_movies_batch(entries: list[dict]) -> int:
    """Bulk-register movies in one registry write. Each entry needs folder/remote_url/local_path."""
    if not entries:
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _REGISTRY_LOCK:
        data = _load_registry()
        for raw in entries:
            folder_name = str(raw.get("folder") or "").strip()
            remote_url = str(raw.get("remote_url") or "").strip()
            local_path = str(raw.get("local_path") or "").strip()
            if not folder_name or not remote_url or not local_path:
                continue
            key = movie_proxy_key(folder_name)
            strm_path = str(raw.get("strm_path") or "")
            data["movies"][key] = {
                "key": key,
                "kind": "movie",
                "folder": folder_name,
                "remote_url": remote_url,
                "local_path": os.path.realpath(local_path),
                "strm_path": os.path.realpath(strm_path) if strm_path else "",
                "bitrate_bps": int(raw.get("bitrate_bps") or 0),
                "ext": str(raw.get("ext") or "mkv").lstrip("."),
                "updated_at": now,
            }
        _save_registry(data)
    return len(entries)


def get_movie_proxy_entry(key: str) -> dict | None:
    with _REGISTRY_LOCK:
        data = _load_registry()
        entry = data["movies"].get(key)
        return dict(entry) if isinstance(entry, dict) else None


def update_episode_proxy_local_path(
    *,
    series_folder: str,
    season: int,
    episode: int,
    local_path: str,
) -> bool:
    """Update only local_path for an existing episode registry entry."""
    key = episode_proxy_key(series_folder, int(season), int(episode))
    with _REGISTRY_LOCK:
        global _REGISTRY_DIRTY
        data = _load_registry()
        entry = data["episodes"].get(key)
        if not isinstance(entry, dict):
            return False
        entry = dict(entry)
        entry["local_path"] = os.path.realpath(local_path) if local_path else ""
        entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        data["episodes"][key] = entry
        _REGISTRY_DIRTY += 1
        _save_registry(data)
    return True


def clear_episode_proxy_local_path(
    *,
    series_folder: str,
    season: int,
    episode: int,
) -> bool:
    return update_episode_proxy_local_path(
        series_folder=series_folder,
        season=season,
        episode=episode,
        local_path="",
    )


def get_episode_proxy_entry_by_ids(
    series_folder: str, season: int, episode: int
) -> dict | None:
    return get_episode_proxy_entry(episode_proxy_key(series_folder, season, episode))


def register_episode_proxy(
    *,
    series_folder: str,
    season: int,
    episode: int,
    remote_url: str,
    local_path: str,
    strm_path: str = "",
    bitrate_bps: int = 0,
    ext: str = "mkv",
    save: bool = True,
) -> str:
    """Register an episode for proxy playback; returns the proxy key."""
    series_folder = str(series_folder or "").strip()
    remote_url = str(remote_url or "").strip()
    season_i = int(season)
    episode_i = int(episode)
    if not series_folder or not remote_url or season_i < 0 or episode_i < 0:
        raise ValueError("series_folder, remote_url, season, and episode are required")
    key = episode_proxy_key(series_folder, season_i, episode_i)
    if not local_path:
        _folder, local_path = build_episode_output(
            series_folder, season_i, episode_i, ext or "mkv", DOWNLOAD_TV_PATH
        )
    entry = {
        "key": key,
        "kind": "episode",
        "series_folder": series_folder,
        "season": season_i,
        "episode": episode_i,
        "remote_url": remote_url,
        "local_path": os.path.realpath(local_path),
        "strm_path": os.path.realpath(strm_path) if strm_path else "",
        "bitrate_bps": int(bitrate_bps or 0),
        "ext": str(ext or "mkv").lstrip("."),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with _REGISTRY_LOCK:
        global _REGISTRY_DIRTY
        data = _load_registry()
        prev = data["episodes"].get(key)
        if _registry_entry_unchanged(
            prev,
            entry,
            ("remote_url", "local_path", "strm_path", "ext", "season", "episode", "series_folder"),
        ):
            return key
        data["episodes"][key] = entry
        _REGISTRY_DIRTY += 1
        if save and _REGISTRY_DIRTY >= _REGISTRY_FLUSH_EVERY:
            _save_registry(data)
    return key


def register_episodes_batch(entries: list[dict]) -> int:
    """Bulk-register episodes in one registry write."""
    if not entries:
        return 0
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _REGISTRY_LOCK:
        data = _load_registry()
        for raw in entries:
            series_folder = str(raw.get("series_folder") or "").strip()
            remote_url = str(raw.get("remote_url") or "").strip()
            local_path = str(raw.get("local_path") or "").strip()
            try:
                season_i = int(raw.get("season"))
                episode_i = int(raw.get("episode"))
            except (TypeError, ValueError):
                continue
            if not series_folder or not remote_url or not local_path:
                continue
            if season_i < 0 or episode_i < 0:
                continue
            key = episode_proxy_key(series_folder, season_i, episode_i)
            strm_path = str(raw.get("strm_path") or "")
            data["episodes"][key] = {
                "key": key,
                "kind": "episode",
                "series_folder": series_folder,
                "season": season_i,
                "episode": episode_i,
                "remote_url": remote_url,
                "local_path": os.path.realpath(local_path),
                "strm_path": os.path.realpath(strm_path) if strm_path else "",
                "bitrate_bps": int(raw.get("bitrate_bps") or 0),
                "ext": str(raw.get("ext") or "mkv").lstrip("."),
                "updated_at": now,
            }
        _save_registry(data)
    return len(entries)


def get_episode_proxy_entry(key: str) -> dict | None:
    with _REGISTRY_LOCK:
        data = _load_registry()
        entry = data["episodes"].get(key)
        return dict(entry) if isinstance(entry, dict) else None


def get_proxy_entry(key: str) -> dict | None:
    return get_movie_proxy_entry(key) or get_episode_proxy_entry(key)


def resolve_movie_play_url(
    *,
    folder_name: str,
    remote_url: str,
    strm_path: str,
    ext: str = "mkv",
    config: dict | None = None,
    write_sidecar: bool = True,
) -> str:
    """Return proxy URL when enabled, else the remote Xtream URL."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    folder_name = str(folder_name or "").strip()
    remote_url = str(remote_url or "").strip()
    _folder, local_path = build_movie_output(
        folder_name, ext or "mkv", DOWNLOAD_MOVIES_PATH, strm_path=strm_path
    )
    # Sidecar/download dir only when we actually download locals.
    do_sidecar = write_sidecar and stream_proxy_download_enabled(cfg)
    if do_sidecar:
        write_movie_strm_url_sidecar(local_path, remote_url, strm_path=strm_path)
    if not stream_proxy_enabled(cfg) or not stream_proxy_host(cfg):
        return remote_url
    register_movie_proxy(
        folder_name=folder_name,
        remote_url=remote_url,
        local_path=local_path,
        strm_path=strm_path,
        ext=ext,
        write_sidecar=False,
    )
    return build_movie_proxy_url(folder_name, config=cfg)


def resolve_episode_play_url(
    *,
    series_folder: str,
    season: int,
    episode: int,
    remote_url: str,
    strm_path: str,
    ext: str = "mkv",
    config: dict | None = None,
) -> str:
    """Return proxy URL when enabled, else the remote Xtream URL."""
    cfg = config if isinstance(config, dict) else load_auto_download_config()
    series_folder = str(series_folder or "").strip()
    remote_url = str(remote_url or "").strip()
    season_i = int(season)
    episode_i = int(episode)
    dest = _series_dest(cfg)
    _folder, local_path = build_episode_output(
        series_folder,
        season_i,
        episode_i,
        ext or "mkv",
        dest,
        strm_path=strm_path,
    )
    if not stream_proxy_enabled(cfg) or not stream_proxy_host(cfg):
        return remote_url
    register_episode_proxy(
        series_folder=series_folder,
        season=season_i,
        episode=episode_i,
        remote_url=remote_url,
        local_path=local_path,
        strm_path=strm_path,
        ext=ext,
    )
    return build_episode_proxy_url(
        series_folder, season_i, episode_i, config=cfg
    )


class ProxyJob:
    def __init__(self, entry: dict):
        self.key = str(entry.get("key") or "")
        self.remote_url = str(entry.get("remote_url") or "")
        self.local_path = str(entry.get("local_path") or "")
        self.bitrate_bps = int(entry.get("bitrate_bps") or 0) or DEFAULT_BITRATE_BPS
        self.total_size = 0  # known remote Content-Length when available
        self.error = ""
        self.done = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cv = threading.Condition()

    def ensure_started(self) -> None:
        with self._cv:
            if self._thread and self._thread.is_alive():
                return
            if self.done and growing_download_bytes(self.local_path) > 0:
                # Completed file — nothing to download.
                if self.total_size <= 0:
                    self.total_size = growing_download_bytes(self.local_path)
                return
            self.error = ""
            self.done = False
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run_download, name=f"proxy-dl-{self.key[:8]}", daemon=True
            )
            self._thread.start()

    def _run_download(self) -> None:
        path = self.local_path
        try:
            prepare_output_dir(os.path.dirname(path))
            existing = growing_download_bytes(path)
            # Prefer final path (no .part).
            part = path + ".part"
            if os.path.isfile(part) and not os.path.isfile(path):
                try:
                    os.replace(part, path)
                    existing = os.path.getsize(path)
                except OSError:
                    pass

            headers = {
                "User-Agent": "Xtream-VOD-Downloader/proxy",
                "Accept-Encoding": "identity",
            }
            if existing > 0:
                headers["Range"] = f"bytes={existing}-"

            with requests.get(
                self.remote_url,
                headers=headers,
                stream=True,
                timeout=(15, 120),
                allow_redirects=True,
            ) as resp:
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()
                # Capture total size.
                if resp.status_code == 206:
                    cr = resp.headers.get("Content-Range") or ""
                    if "/" in cr:
                        try:
                            self.total_size = int(cr.rsplit("/", 1)[-1])
                        except ValueError:
                            pass
                elif resp.headers.get("Content-Length"):
                    try:
                        cl = int(resp.headers["Content-Length"])
                        self.total_size = existing + cl if resp.status_code == 206 else cl
                    except ValueError:
                        pass

                mode = "ab" if existing > 0 and resp.status_code == 206 else "wb"
                if mode == "wb" and existing > 0:
                    # Server ignored Range — restart from 0.
                    existing = 0
                with open(path, mode) as handle:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if self._stop.is_set():
                            break
                        if not chunk:
                            continue
                        handle.write(chunk)
                        handle.flush()
                        with self._cv:
                            self._cv.notify_all()

            size = growing_download_bytes(path)
            if self.total_size > 0 and size >= self.total_size:
                self.done = True
            elif size > 0 and self.total_size <= 0:
                # No length from server — treat as done when connection ended cleanly.
                self.done = True
                self.total_size = size
            with self._cv:
                self._cv.notify_all()
            # Finalize: notify libraries / delete remote strm if configured.
            if self.done and size > 0:
                try:
                    from core import finalize_after_local_download, finalize_download_path

                    finalize_download_path(path)
                    entry = get_proxy_entry(self.key) or {}
                    finalize_after_local_download(
                        path,
                        strm_path=str(entry.get("strm_path") or "") or None,
                        strm_url=self.remote_url,
                        notify=True,
                    )
                except Exception as exc:  # noqa: BLE001
                    self.error = f"finalize:{exc}"
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            with self._cv:
                self._cv.notify_all()

    def size(self) -> int:
        return growing_download_bytes(self.local_path)

    def wait_for_bytes(self, need: int, timeout: float) -> bool:
        deadline = time.time() + max(0.1, float(timeout))
        while time.time() < deadline:
            if self.size() >= need:
                return True
            if self.error and self.size() < need:
                return False
            if self.done and self.size() < need:
                return False
            remaining = deadline - time.time()
            with self._cv:
                self._cv.wait(timeout=min(0.5, max(0.05, remaining)))
        return self.size() >= need


def get_or_create_job(entry: dict, *, start_download: bool = True) -> ProxyJob:
    key = str(entry.get("key") or "")
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None:
            job = ProxyJob(entry)
            _JOBS[key] = job
        else:
            # Refresh remote URL / path if registry changed.
            job.remote_url = str(entry.get("remote_url") or job.remote_url)
            job.local_path = str(entry.get("local_path") or job.local_path)
            if int(entry.get("bitrate_bps") or 0) > 0:
                job.bitrate_bps = int(entry["bitrate_bps"])
        if start_download:
            job.ensure_started()
            # Ensure sidecar exists once download dir is used (movies only).
            if str(entry.get("kind") or "") != "episode":
                try:
                    write_movie_strm_url_sidecar(
                        job.local_path,
                        job.remote_url,
                        strm_path=str(entry.get("strm_path") or "") or None,
                    )
                except Exception:
                    pass
        return job


def _buffer_targets(config: dict) -> tuple[int, int]:
    """Return (min_bytes, bitrate_bps default)."""
    try:
        min_mb = max(10, int(config.get("prefetch_buffer_mb") or 20))
    except (TypeError, ValueError):
        min_mb = 20
    try:
        buffer_s = max(30, int(config.get("prefetch_buffer_seconds") or 120))
    except (TypeError, ValueError):
        buffer_s = 120
    bitrate = DEFAULT_BITRATE_BPS
    min_from_time = int(bitrate * buffer_s / 8)
    return max(min_mb * 1024 * 1024, min_from_time), bitrate


def _parse_range(header: str | None) -> tuple[int | None, int | None]:
    if not header:
        return None, None
    match = _RANGE_RE.search(header.strip())
    if not match:
        return None, None
    start_s, end_s = match.group(1), match.group(2)
    start = int(start_s) if start_s else 0
    end = int(end_s) if end_s else None
    return start, end


def _content_type_for_ext(ext: str) -> str:
    ext = (ext or "mkv").lower().lstrip(".")
    if ext in ("mp4", "m4v", "mov"):
        return "video/mp4"
    return "video/x-matroska"


def _needs_remote_passthrough(ext: str, job: "ProxyJob") -> bool:
    """Incomplete MP4/MOV often lacks moov until the end — unplayable from a growing file."""
    if (ext or "").lower().lstrip(".") not in ("mp4", "m4v", "mov"):
        return False
    if job.done and job.total_size > 0 and job.size() >= job.total_size:
        return False
    # Treat as incomplete unless we already have a finished-sized file.
    size = job.size()
    if job.total_size > 0 and size >= job.total_size:
        return False
    return True


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[stream_proxy] {self.address_string()} - {fmt % args}", flush=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle(head_only=True)

    def do_GET(self) -> None:  # noqa: N802
        self._handle(head_only=False)

    def _passthrough_remote(
        self,
        job: ProxyJob,
        entry: dict,
        *,
        start: int | None,
        end: int | None,
        head_only: bool,
    ) -> None:
        """Serve Range from Xtream while the local download catches up (MP4-safe)."""
        headers = {
            "User-Agent": "Xtream-VOD-Downloader/proxy-passthrough",
            "Accept-Encoding": "identity",
        }
        if start is not None:
            if end is not None:
                headers["Range"] = f"bytes={start}-{end}"
            else:
                headers["Range"] = f"bytes={start}-"
        try:
            resp = requests.get(
                job.remote_url,
                headers=headers,
                stream=True,
                timeout=(15, 120),
                allow_redirects=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.send_error(502, f"remote fetch failed: {exc}"[:200])
            return
        try:
            if resp.status_code not in (200, 206):
                self.send_error(502, f"remote status {resp.status_code}")
                return
            content_type = _content_type_for_ext(str(entry.get("ext") or "mp4"))
            # Capture total size for future local serving.
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range") or ""
                if "/" in cr:
                    try:
                        job.total_size = int(cr.rsplit("/", 1)[-1])
                    except ValueError:
                        pass
            elif resp.headers.get("Content-Length"):
                try:
                    job.total_size = int(resp.headers["Content-Length"])
                except ValueError:
                    pass

            self.send_response(resp.status_code)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            for hop in ("Content-Length", "Content-Range"):
                if resp.headers.get(hop):
                    self.send_header(hop, resp.headers[hop])
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Proxy-Mode", "passthrough")
            self.end_headers()
            if head_only:
                return
            try:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if not chunk:
                        continue
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        break
            except (
                requests.exceptions.ChunkedEncodingError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
            ) as exc:
                # Provider closed the socket (often a second Xtream connection).
                print(f"[stream_proxy] upstream closed during passthrough: {exc}", flush=True)
        finally:
            resp.close()

    def _serve_complete_local(
        self,
        job: ProxyJob,
        entry: dict,
        *,
        start: int | None,
        end: int | None,
        head_only: bool,
    ) -> None:
        """Serve a finished on-disk file with HTTP Range (no Xtream / no download)."""
        size = job.size()
        if size <= 0 or not os.path.isfile(job.local_path):
            self.send_error(404, "local file missing")
            return
        job.done = True
        job.total_size = size
        total = size
        if start is None:
            start = 0
            end = size - 1
            status = 200
        else:
            if start >= size:
                self.send_error(416, "invalid range")
                return
            if end is None or end >= size:
                end = size - 1
            if end < start:
                self.send_error(416, "invalid range")
                return
            status = 206
        length = end - start + 1
        content_type = _content_type_for_ext(str(entry.get("ext") or "mkv"))
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Proxy-Mode", "local-complete")
        self.end_headers()
        if head_only:
            return
        try:
            with open(job.local_path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _handle(self, *, head_only: bool) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "")
        if path in ("/health", "/"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head_only:
                self.wfile.write(body)
            return

        # Clients often append ".mp4", "/stream", "/stream.mp4", or a trailing slash.
        match_sub = re.match(
            r"^/p/sub/(movie|episode)/([a-f0-9]{8,64})(?:\.srt)?/?$",
            path,
            flags=re.IGNORECASE,
        )
        if match_sub:
            kind = match_sub.group(1)
            key = match_sub.group(2)
            entry = (
                get_movie_proxy_entry(key)
                if kind == "movie"
                else get_episode_proxy_entry(key)
            )
            if not entry:
                self.send_error(404, "unknown subtitle key")
                return
            sub_path = resolve_episode_subtitle_path(entry) if kind == "episode" else None
            if not sub_path:
                self.send_error(404, "subtitle file missing")
                return
            try:
                data = open(sub_path, "rb").read()
            except OSError as exc:
                self.send_error(500, f"subtitle read failed: {exc}"[:180])
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Proxy-Mode", "subtitle")
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            return

        match = re.match(
            r"^/p/(movie|episode)/([a-f0-9]{8,64})"
            r"(?:/stream)?(?:\.(?:mp4|m4v|mkv|mov|ts|m3u8))?/?$",
            path,
            flags=re.IGNORECASE,
        )
        if not match:
            self.send_error(404, "not found")
            return

        kind = match.group(1)
        key = match.group(2)
        if kind == "movie":
            entry = get_movie_proxy_entry(key)
            unknown_msg = "unknown movie key"
        else:
            entry = get_episode_proxy_entry(key)
            unknown_msg = "unknown episode key"
        if not entry:
            self.send_error(404, unknown_msg)
            return

        cfg = load_auto_download_config()
        do_download = stream_proxy_download_enabled(cfg)
        job = get_or_create_job(entry, start_download=do_download)
        ext = str(entry.get("ext") or "mkv").lower()
        start, end = _parse_range(self.headers.get("Range"))

        # Finished local file: serve it even when download mode is off (strm → proxy → disk).
        local_bytes = job.size()
        local_complete = (
            local_bytes > 0
            and os.path.isfile(job.local_path)
            and not os.path.isfile(job.local_path + ".part")
        )
        if local_complete:
            self._serve_complete_local(
                job, entry, start=start, end=end, head_only=head_only
            )
            return

        # Passthrough-only mode (default): never grow local files.
        if not do_download:
            self._passthrough_remote(job, entry, start=start, end=end, head_only=head_only)
            return

        # Prefer remote passthrough when local bytes can't satisfy the client yet:
        # - incomplete MP4 (moov often at EOF)
        # - deep seek past downloaded range
        # - little/no local data (avoid JF 100s timeout / spinner on 503)
        size_now = job.size()
        deep_seek = start is not None and start > max(0, size_now - 1)
        local_cold = size_now < 2 * 1024 * 1024
        if _needs_remote_passthrough(ext, job) or deep_seek or local_cold:
            self._passthrough_remote(job, entry, start=start, end=end, head_only=head_only)
            return

        min_bytes, default_br = _buffer_targets(cfg)
        bitrate = job.bitrate_bps or default_br

        ua = (self.headers.get("User-Agent") or "").lower()
        is_probe = ("ffprobe" in ua) or ("ffmpeg" in ua)
        range_span = (end - start + 1) if (start is not None and end is not None) else None
        # ffprobe / small Range: serve ASAP (only requested bytes). Full play: wait buffer.
        wait_s = max(60, int(cfg.get("prefetch_max_wait_seconds") or 180))
        if is_probe or (range_span is not None and range_span <= 4 * 1024 * 1024):
            if start is None:
                need = min(2 * 1024 * 1024, min_bytes)
            elif end is not None:
                need = int(end) + 1
            else:
                need = int(start) + 2 * 1024 * 1024
            wait_s = min(120, max(30, wait_s))
        elif start is None or start == 0:
            need = min_bytes
        else:
            need = int(start) + min_bytes
            if end is not None:
                need = max(need, int(end) + 1)
            if start and start > min_bytes:
                wait_s = max(wait_s, int(start * 8 / max(bitrate, 500_000)) + 120)

        if not job.wait_for_bytes(need, timeout=float(wait_s)):
            if job.error:
                self.send_error(502, f"download failed: {job.error[:200]}")
            else:
                self.send_error(503, "buffer not ready yet; retry shortly")
            return

        size_now = job.size()
        total = max(job.total_size or 0, size_now)

        if start is None:
            start = 0
            end = size_now - 1
            status = 206 if (job.total_size > size_now) else 200
        else:
            if start >= size_now:
                if not job.wait_for_bytes(start + 1, timeout=min(60.0, float(wait_s))):
                    self.send_error(503, "requested range not downloaded yet")
                    return
                size_now = job.size()
                total = max(total, size_now, job.total_size or 0)
            if end is None:
                end = min(size_now - 1, start + 1024 * 1024 - 1)
            end = min(end, size_now - 1)
            if end < start:
                self.send_error(416, "invalid range")
                return
            status = 206

        length = end - start + 1
        content_type = _content_type_for_ext(ext)

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Proxy-Mode", "local")
        self.end_headers()
        if head_only:
            return

        try:
            with open(job.local_path, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        next_need = start + (length - remaining) + 1
                        if not job.wait_for_bytes(next_need, timeout=30.0):
                            break
                        continue
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return


def rewrite_existing_movie_strms_to_proxy(
    *,
    movies_root: str | None = None,
    config: dict | None = None,
    limit: int = 0,
) -> dict:
    """Point existing movie .strm files at the progressive proxy (keeps remote in registry).

    Batched: one registry write at the end (plus periodic saves) so large libraries finish quickly.
    Does not create download folders / sidecars until first Play.
    """
    from core import (
        STRM_OUTPUT_MOVIES_PATH,
        movie_folder_from_strm_path,
        read_strm_url,
        write_strm,
    )

    cfg = config if isinstance(config, dict) else load_auto_download_config()
    root = (movies_root or STRM_OUTPUT_MOVIES_PATH).rstrip("/")
    result = {"scanned": 0, "updated": 0, "skipped": 0, "errors": []}
    if not stream_proxy_enabled(cfg) or not stream_proxy_host(cfg):
        result["errors"].append("proxy_disabled_or_host_missing")
        return result
    if not os.path.isdir(root):
        result["errors"].append("movies_root_missing")
        return result

    pending: list[dict] = []
    host = stream_proxy_host(cfg)
    port = stream_proxy_port(cfg)

    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            result["scanned"] += 1
            if limit and result["updated"] >= limit:
                if pending:
                    register_movies_batch(pending)
                    pending = []
                return result
            strm_path = os.path.join(dirpath, name)
            try:
                current = read_strm_url(strm_path) or ""
                if is_stream_proxy_url(current):
                    result["skipped"] += 1
                    continue
                if not current.startswith("http"):
                    result["skipped"] += 1
                    continue
                folder = movie_folder_from_strm_path(strm_path) or os.path.basename(dirpath)
                ext = current.rsplit(".", 1)[-1].split("?")[0].strip().lower() or "mkv"
                if not re.fullmatch(r"[a-z0-9]{2,5}", ext):
                    ext = "mkv"
                _folder, local_path = build_movie_output(
                    folder, ext, DOWNLOAD_MOVIES_PATH, strm_path=strm_path
                )
                key = movie_proxy_key(folder)
                play_url = f"http://{host}:{port}/p/movie/{key}"
                if not write_strm(strm_path, play_url):
                    result["skipped"] += 1
                    continue
                pending.append(
                    {
                        "folder": folder,
                        "remote_url": current,
                        "local_path": local_path,
                        "strm_path": strm_path,
                        "ext": ext,
                    }
                )
                result["updated"] += 1
                if len(pending) >= 500:
                    register_movies_batch(pending)
                    pending = []
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"{strm_path}:{exc}")
    if pending:
        register_movies_batch(pending)
    return result


def rewrite_existing_episode_strms_to_proxy(
    *,
    series_root: str | None = None,
    config: dict | None = None,
    limit: int = 0,
) -> dict:
    """Point existing episode .strm files at the progressive proxy."""
    from core import (
        STRM_OUTPUT_SERIES_PATH,
        parse_episode_numbers_from_path,
        read_strm_url,
        series_folder_from_strm_path,
        write_strm,
    )

    cfg = config if isinstance(config, dict) else load_auto_download_config()
    root = (series_root or STRM_OUTPUT_SERIES_PATH).rstrip("/")
    result = {"scanned": 0, "updated": 0, "skipped": 0, "errors": []}
    if not stream_proxy_enabled(cfg) or not stream_proxy_host(cfg):
        result["errors"].append("proxy_disabled_or_host_missing")
        return result
    if not os.path.isdir(root):
        result["errors"].append("series_root_missing")
        return result

    pending: list[dict] = []
    host = stream_proxy_host(cfg)
    port = stream_proxy_port(cfg)
    dest = _series_dest(cfg)

    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            result["scanned"] += 1
            if limit and result["updated"] >= limit:
                if pending:
                    register_episodes_batch(pending)
                    pending = []
                return result
            strm_path = os.path.join(dirpath, name)
            try:
                current = read_strm_url(strm_path) or ""
                if is_stream_proxy_url(current):
                    result["skipped"] += 1
                    continue
                if not current.startswith("http"):
                    result["skipped"] += 1
                    continue
                nums = parse_episode_numbers_from_path(strm_path)
                if not nums:
                    result["skipped"] += 1
                    continue
                season_i, episode_i = nums
                series_folder = (
                    series_folder_from_strm_path(strm_path)
                    or os.path.basename(os.path.dirname(os.path.dirname(strm_path)))
                )
                if not series_folder:
                    result["skipped"] += 1
                    continue
                ext = current.rsplit(".", 1)[-1].split("?")[0].strip().lower() or "mkv"
                if not re.fullmatch(r"[a-z0-9]{2,5}", ext):
                    ext = "mkv"
                _folder, local_path = build_episode_output(
                    series_folder,
                    season_i,
                    episode_i,
                    ext,
                    dest,
                    strm_path=strm_path,
                )
                key = episode_proxy_key(series_folder, season_i, episode_i)
                play_url = f"http://{host}:{port}/p/episode/{key}"
                if not write_strm(strm_path, play_url):
                    result["skipped"] += 1
                    continue
                pending.append(
                    {
                        "series_folder": series_folder,
                        "season": season_i,
                        "episode": episode_i,
                        "remote_url": current,
                        "local_path": local_path,
                        "strm_path": strm_path,
                        "ext": ext,
                    }
                )
                result["updated"] += 1
                if len(pending) >= 500:
                    register_episodes_batch(pending)
                    pending = []
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"{strm_path}:{exc}")
    if pending:
        register_episodes_batch(pending)
    return result


def run_proxy_server(host: str = "0.0.0.0", port: int | None = None) -> None:
    cfg = load_auto_download_config()
    listen_port = int(port or stream_proxy_port(cfg))
    server = ThreadingHTTPServer((host, listen_port), ProxyHandler)
    print(f"[stream_proxy] listening on {host}:{listen_port}", flush=True)
    server.serve_forever()


def main() -> None:
    run_proxy_server()


if __name__ == "__main__":
    main()

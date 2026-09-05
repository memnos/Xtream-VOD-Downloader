import os
import re
import threading
import time
from dataclasses import dataclass, field

import requests

from core import (
    DOWNLOAD_MOVIES_PATH,
    LOCAL_DOWNLOAD_MARKER,
    DownloadCancelled,
    append_playback_history,
    build_episode_output,
    build_movie_output,
    delete_movie_local_and_restore_strm,
    ensure_movie_output_is_file,
    evaluate_prefetch_switch,
    finalize_after_local_download,
    find_local_files_for_strm,
    find_subsequent_xtream_episodes,
    find_xtream_episode,
    growing_download_bytes,
    is_media_considered_watched,
    load_auto_download_config,
    load_credentials,
    map_local_path_to_media_server,
    map_media_server_path_to_local,
    playback_blocks_xtream_download,
    prepare_output_dir,
    read_strm_url,
    resolve_episode_from_strm_path,
    run_ytdlp,
    save_watcher_status,
    watcher_should_run,
    write_movie_strm_url_sidecar,
)
from deletion import (
    add_deletion_prompt,
    find_series_download_paths,
    prune_incomplete_deletion_prompts,
    scan_completed_series_prompts,
    series_production_finished,
    should_prompt_series_deletion,
)
from continue_download import (
    continue_download_enabled,
    scan_and_enqueue_continue_downloads,
    take_pending_auto_downloads,
)


@dataclass
class MediaSession:
    server_id: str
    client: "MediaServerClient"
    user_id: str
    username: str


@dataclass
class PlayingItem:
    item_type: str
    item_id: str
    title: str
    item_path: str
    blocks_download: bool
    series_id: str = ""
    series_name: str = ""
    season: int | None = None
    episode: int | None = None
    server_id: str = ""
    session_id: str = ""
    bitrate_bps: int = 0
    position_ticks: int = 0
    run_time_ticks: int = 0
    strm_path: str = ""

    @property
    def key(self) -> str:
        prefix = f"{self.server_id}:" if self.server_id else ""
        if self.item_type == "Episode":
            return f"ep:{prefix}{self.series_id}:{self.season}:{self.episode}"
        return f"movie:{prefix}{self.item_id}"

    def display_label(self) -> str:
        label = (
            f"{self.series_name} S{int(self.season):02d}E{int(self.episode):02d}"
            if self.item_type == "Episode"
            else self.title
        )
        if self.server_id:
            return f"{label} ({self.server_id})"
        return label

    def history_type(self) -> str:
        return "Series" if self.item_type == "Episode" else "Movie"


PlayingEpisode = PlayingItem

DEFER_XTREAM_ASSIST_IDLE_SEC = 25


@dataclass
class QueueItem:
    series_name: str
    season: int
    episode: int
    label: str
    strm_path: str = ""
    kind: str = "episode"  # episode | movie


@dataclass
class PausedDownload:
    item: QueueItem
    dest_root: str
    xtream_host: str
    xtream_user: str
    xtream_pw: str
    output_file: str
    url: str
    priority: bool = False
    kind: str = "episode"
    bitrate_bps: int = 0
    source_item_id: str = ""
    source_session_id: str = ""
    server_id: str = ""


@dataclass
class WatcherStatus:
    running: bool = False
    enabled: bool = False
    playback_active: bool = False
    download_paused: bool = False
    downloading: bool = False
    current_playing: str = ""
    current_download: str = ""
    download_progress: float = 0.0
    download_progress_text: str = ""
    queue_size: int = 0
    cooldown_remaining: int = 0
    last_action: str = ""
    last_error: str = ""
    log: list[str] = field(default_factory=list)


def _as_item_list(data: object) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("Items")
        return items if isinstance(items, list) else []
    return []


class MediaServerClient:
    """Emby and Jellyfin client (Jellyfin exposes a compatible /emby/* API)."""

    def __init__(self, base_url: str, api_key: str, server_type: str = "emby"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.server_type = (server_type or "emby").lower()
        self._user_id_cache: str | None = None

    @property
    def display_name(self) -> str:
        return "Jellyfin" if self.server_type == "jellyfin" else "Emby"

    def _request_paths(self, path: str) -> list[str]:
        if self.server_type == "jellyfin" and path.startswith("/emby/"):
            return [path[5:], path]
        return [path]

    def _get(self, path: str, params: dict | None = None) -> object:
        headers = {"X-Emby-Token": self.api_key}
        last_error: Exception | None = None
        for api_path in self._request_paths(path):
            query = {"api_key": self.api_key}
            if params:
                query.update(params)
            url = f"{self.base_url}{api_path}"
            for attempt in range(3):
                try:
                    response = requests.get(url, params=query, headers=headers, timeout=30)
                    response.raise_for_status()
                    return response.json()
                except requests.HTTPError as exc:
                    last_error = exc
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in {502, 503, 504} and attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    if status == 404:
                        break
                    raise
                except (requests.ConnectionError, requests.Timeout) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"API request failed: {path}")

    def _post(self, path: str, body: dict | None = None, params: dict | None = None) -> object:
        headers = {"X-Emby-Token": self.api_key}
        last_error: Exception | None = None
        for api_path in self._request_paths(path):
            url = f"{self.base_url}{api_path}"
            query = {"api_key": self.api_key}
            if params:
                query.update(params)
            for attempt in range(3):
                try:
                    response = requests.post(
                        url,
                        params=query,
                        json=body if body is not None else {},
                        headers=headers,
                        timeout=60,
                    )
                    response.raise_for_status()
                    if response.content:
                        return response.json()
                    return {}
                except requests.HTTPError as exc:
                    last_error = exc
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in {502, 503, 504} and attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    if status == 404:
                        break
                    raise
                except (requests.ConnectionError, requests.Timeout) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"API request failed: {path}")

    def _delete(self, path: str, params: dict | None = None) -> object:
        headers = {"X-Emby-Token": self.api_key}
        last_error: Exception | None = None
        for api_path in self._request_paths(path):
            url = f"{self.base_url}{api_path}"
            query = {"api_key": self.api_key}
            if params:
                query.update(params)
            for attempt in range(3):
                try:
                    response = requests.delete(
                        url, params=query, headers=headers, timeout=30
                    )
                    response.raise_for_status()
                    if response.content:
                        try:
                            return response.json()
                        except ValueError:
                            return {}
                    return {}
                except requests.HTTPError as exc:
                    last_error = exc
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in {502, 503, 504} and attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    if status == 404:
                        break
                    raise
                except (requests.ConnectionError, requests.Timeout) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(1 + attempt)
                        continue
                    raise
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"API request failed: {path}")

    def refresh_libraries(self) -> None:
        self._post("/emby/Library/Refresh", {})

    def notify_library_paths(self, updates: list[dict]) -> None:
        """Tell Emby/Jellyfin that media paths were created/deleted/modified."""
        if not updates:
            return
        self._post("/emby/Library/Media/Updated", {"Updates": updates})

    def find_movies_by_tmdb_id(self, tmdb_id: int | str) -> list[dict]:
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return []
        if tid <= 0:
            return []
        data = self._get(
            "/emby/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": "Movie",
                "AnyProviderIdEquals": f"Tmdb.{tid}",
                "Fields": "Path,ProviderIds,Name",
                "Limit": 20,
            },
        )
        return _as_item_list(data)

    def find_series_by_tmdb_id(self, tmdb_id: int | str) -> list[dict]:
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return []
        if tid <= 0:
            return []
        data = self._get(
            "/emby/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": "Series",
                "AnyProviderIdEquals": f"Tmdb.{tid}",
                "Fields": "Path,ProviderIds,Name",
                "Limit": 20,
            },
        )
        items = _as_item_list(data)
        # Jellyfin often ignores AnyProviderIdEquals; keep only exact TMDB matches.
        matched = [
            item
            for item in items
            if str((item.get("ProviderIds") or {}).get("Tmdb") or "") == str(tid)
        ]
        return matched

    def find_series_near_path(
        self,
        series_path: str,
        *,
        tmdb_id: int | str | None = None,
    ) -> list[dict]:
        """Resolve a Series item by library path (and optional TMDB id)."""
        target = (series_path or "").replace("\\", "/").rstrip("/")
        if not target:
            return []
        folder_name = target.rsplit("/", 1)[-1]
        search = re.sub(r"\s*\[tmdbid-\d+\]\s*$", "", folder_name, flags=re.IGNORECASE).strip()
        # Jellyfin SearchTerm fails with trailing "(Year)" — strip it for lookup.
        search = re.sub(r"\s*\(\d{4}\)\s*$", "", search).strip() or search
        data = self._get(
            "/emby/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": "Series",
                "SearchTerm": search or folder_name,
                "Fields": "Path,ProviderIds,Name",
                "Limit": 25,
            },
        )
        items = _as_item_list(data)
        exact = [
            item
            for item in items
            if str(item.get("Path") or "").replace("\\", "/").rstrip("/") == target
        ]
        if exact:
            return exact
        try:
            tid = int(tmdb_id) if tmdb_id is not None else 0
        except (TypeError, ValueError):
            tid = 0
        if tid > 0:
            by_tmdb = [
                item
                for item in items
                if str((item.get("ProviderIds") or {}).get("Tmdb") or "") == str(tid)
            ]
            if by_tmdb:
                return by_tmdb
            return self.find_series_by_tmdb_id(tid)
        return []

    def refresh_item_metadata(self, item_id: str, *, replace_all: bool = True) -> None:
        flag = "true" if replace_all else "false"
        self._post(
            f"/emby/Items/{item_id}/Refresh",
            None,
            params={
                "MetadataRefreshMode": "FullRefresh",
                "ImageRefreshMode": "FullRefresh",
                "ReplaceAllMetadata": flag,
                "ReplaceAllImages": flag,
            },
        )
    def test_connection(self, username: str) -> tuple[bool, str]:
        try:
            users = self._get("/emby/Users")
            if not isinstance(users, list):
                return False, "Unexpected API response for /Users"
            user_id = self.resolve_user_id(username)
            if not user_id:
                names = ", ".join(str(user.get("Name", "")) for user in users if user.get("Name"))
                hint = f" Available users: {names}" if names else ""
                return False, f"User '{username}' not found.{hint}"
            sessions = self.get_sessions()
            return True, f"Connected — user '{username}' OK, {len(sessions)} active session(s)"
        except requests.ConnectionError:
            return False, "Connection refused — check URL and that the server is reachable from the container"
        except requests.Timeout:
            return False, "Connection timed out"
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 401:
                return False, "Invalid API key (HTTP 401)"
            if exc.response is not None and exc.response.status_code in {502, 503, 504}:
                return (
                    False,
                    f"Server temporarily unavailable (HTTP {exc.response.status_code}) — "
                    "retry in a few seconds; Jellyfin may still be starting",
                )
            return False, str(exc)
        except Exception as exc:
            return False, str(exc)

    def resolve_user_id(self, username: str) -> str | None:
        if self._user_id_cache:
            return self._user_id_cache
        users = self._get("/emby/Users")
        if not isinstance(users, list):
            return None
        target = username.strip().lower()
        for user in users:
            name = str(user.get("Name", "")).lower()
            if name == target:
                self._user_id_cache = user["Id"]
                return self._user_id_cache
        return None

    def get_sessions(self) -> list:
        data = self._get("/emby/Sessions")
        return _as_item_list(data)

    def display_message(
        self,
        session_id: str,
        *,
        header: str,
        text: str,
        timeout_ms: int | None = 25000,
    ) -> None:
        """Show an on-screen message on a client session (Emby/Jellyfin DisplayMessage)."""
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        params: dict[str, str] = {
            "Header": header,
            "Text": text,
        }
        body: dict = {
            "Header": header,
            "Text": text,
        }
        if timeout_ms is not None and int(timeout_ms) > 0:
            params["TimeoutMs"] = str(int(timeout_ms))
            body["TimeoutMs"] = int(timeout_ms)
        self._post(f"/emby/Sessions/{sid}/Message", body=body, params=params)

    def stop_playback(self, session_id: str) -> None:
        sid = (session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        self._post(f"/emby/Sessions/{sid}/Playing/Stop", None)

    def play_item(
        self,
        session_id: str,
        item_id: str,
        *,
        start_position_ticks: int = 0,
    ) -> None:
        sid = (session_id or "").strip()
        iid = (item_id or "").strip()
        if not sid or not iid:
            raise ValueError("session_id and item_id are required")
        params = {
            "ItemIds": iid,
            "PlayCommand": "PlayNow",
            "StartPositionTicks": str(max(0, int(start_position_ticks or 0))),
        }
        self._post(f"/emby/Sessions/{sid}/Playing", None, params=params)

    def find_item_by_path(self, path: str) -> dict | None:
        target = (path or "").replace("\\", "/").rstrip("/")
        if not target:
            return None
        name = os.path.basename(target)
        search = re.sub(r"\s*\[LOCAL\]\s*", " ", name, flags=re.IGNORECASE)
        search = os.path.splitext(search)[0].strip() or name
        data = self._get(
            "/emby/Items",
            {
                "Recursive": "true",
                "SearchTerm": search[:80],
                "Fields": "Path,MediaSources,MediaStreams",
                "Limit": 25,
            },
        )
        items = _as_item_list(data)
        for item in items:
            item_path = str(item.get("Path") or "").replace("\\", "/").rstrip("/")
            if item_path == target:
                return item
            for source in item.get("MediaSources") or []:
                src_path = str(source.get("Path") or "").replace("\\", "/").rstrip("/")
                if src_path == target:
                    return item
        return None

    def get_series_episodes(self, user_id: str, series_id: str, include_user_data: bool = False) -> list:
        fields = "Path,ParentIndexNumber,IndexNumber,SeriesName,Id"
        if include_user_data:
            fields += ",UserData"
        data = self._get(
            f"/emby/Shows/{series_id}/Episodes",
            {"UserId": user_id, "Fields": fields},
        )
        return _as_item_list(data)

    def get_played_episodes(self, user_id: str, page_size: int = 500) -> list:
        """All played episodes for a user (paginated)."""
        items: list = []
        start = 0
        page_size = max(50, min(int(page_size or 500), 1000))
        while True:
            data = self._get(
                f"/emby/Users/{user_id}/Items",
                {
                    "Recursive": "true",
                    "IncludeItemTypes": "Episode",
                    "Filters": "IsPlayed",
                    "Fields": "Path,ParentIndexNumber,IndexNumber,SeriesName,SeriesId",
                    "SortBy": "DatePlayed",
                    "SortOrder": "Descending",
                    "StartIndex": start,
                    "Limit": page_size,
                },
            )
            batch = _as_item_list(data)
            if not batch:
                break
            items.extend(batch)
            total = 0
            if isinstance(data, dict):
                try:
                    total = int(data.get("TotalRecordCount") or 0)
                except (TypeError, ValueError):
                    total = 0
            start += len(batch)
            if total and start >= total:
                break
            if len(batch) < page_size:
                break
        return items

    def search_series(self, user_id: str, search_term: str, limit: int = 10) -> list:
        data = self._get(
            f"/emby/Users/{user_id}/Items",
            {
                "Recursive": "true",
                "IncludeItemTypes": "Series",
                "SearchTerm": search_term,
                "Fields": "Name,OriginalTitle",
                "Limit": limit,
            },
        )
        return _as_item_list(data)

    def get_item(self, user_id: str, item_id: str, fields: str = "Path") -> dict | None:
        data = self._get(
            f"/emby/Users/{user_id}/Items/{item_id}",
            {"Fields": fields},
        )
        return data if isinstance(data, dict) else None

    def get_series_name(self, user_id: str, series_id: str) -> str:
        data = self.get_item(user_id, series_id, "Name,OriginalTitle")
        if not data:
            return ""
        return str(data.get("Name") or data.get("OriginalTitle") or "").strip()


EmbyClient = MediaServerClient


class AutoDownloadWatcher:
    MAX_LOG_LINES = 80
    DELETION_SCAN_INTERVAL = 3600
    CONTINUE_DOWNLOAD_SCAN_INTERVAL = 900

    def __init__(self):
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._download_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._status = WatcherStatus()
        self._queue: list[QueueItem] = []
        self._watching_item: PlayingItem | None = None
        self._watching_session: MediaSession | None = None
        self._xtream_stream_active = False
        self._cooldown_until = 0.0
        self._skip_cooldown_once = False
        self._active_proc_cancel = threading.Event()
        self._queued_keys: set[str] = set()
        self._paused_download: PausedDownload | None = None
        self._download_context: dict | None = None
        self._last_progress_persist = 0.0
        self._session_cache: dict[str, tuple[tuple[str, str, str, str], MediaSession]] = {}
        self._last_deletion_scan = 0.0
        self._last_continue_scan = 0.0
        self._pruned_incomplete_prompts = False
        self._prefetch_key: str | None = None
        self._prefetch_buffer_logged_key: str | None = None
        self._prefetch_decision_key: str | None = None
        self._prefetch_stay_notified_key: str | None = None
        self._assist_scheduled: set[str] = set()
        self._deferred_xtream_assist: dict | None = None
        self._xtream_assist_idle_after: float = 0.0
        self._abort_output_files: set[str] = set()
        self._pending_watched_movie_cleanup: str | None = None

    def _status_snapshot(self) -> dict:
        cooldown = max(0, int(self._cooldown_until - time.time()))
        return {
            "running": self._status.running,
            "enabled": self._status.enabled,
            "playback_active": self._watching_item is not None,
            "download_paused": self._paused_download is not None,
            "downloading": self._status.downloading,
            "current_playing": self._format_playing(self._watching_item),
            "current_download": self._status.current_download,
            "download_progress": self._status.download_progress,
            "download_progress_text": self._status.download_progress_text,
            "queue_size": len(self._queue),
            "cooldown_remaining": cooldown,
            "cooldown_until": self._cooldown_until if cooldown > 0 else 0.0,
            "last_action": self._status.last_action,
            "last_error": self._status.last_error,
            "log": list(self._status.log),
        }

    def _persist_status(self) -> None:
        with self._lock:
            snapshot = self._status_snapshot()
        try:
            save_watcher_status(snapshot)
        except OSError:
            pass

    @property
    def status(self) -> WatcherStatus:
        with self._lock:
            data = self._status_snapshot()
        return WatcherStatus(**data)

    def start_if_needed(self) -> None:
        config = load_auto_download_config()
        should_run = watcher_should_run(config)
        with self._lock:
            self._status.enabled = should_run
            if not should_run:
                if self._thread and self._thread.is_alive():
                    self._stop_event.set()
                self._status.running = False
                self._persist_status()
                return
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._status.running = True
            features = []
            if config.get("enabled"):
                features.append("download")
            if config.get("auto_intro_skip_enabled"):
                features.append("intro-skip")
            if config.get("auto_subs_enabled"):
                features.append("subs")
            self._log(f"Watcher avviato ({', '.join(features) or 'idle'})")
            try:
                from intro_skip import load_intro_season_backfills

                if load_intro_season_backfills():
                    self._xtream_assist_idle_after = time.time() + 15
                    self._log("Intro skip: backfill stagione in coda (idle 15s)")
            except Exception:
                pass

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            config = load_auto_download_config()
            if not watcher_should_run(config):
                with self._lock:
                    self._status.running = False
                self._persist_status()
                break
            try:
                self._tick(config)
                with self._lock:
                    self._status.last_error = ""
            except Exception as exc:
                with self._lock:
                    self._status.last_error = str(exc)
                self._log(f"Errore watcher: {exc}")
            self._persist_status()
            interval = max(5, int(config.get("poll_interval_seconds", 20)))
            self._stop_event.wait(interval)

        with self._lock:
            self._status.running = False
        self._log("Watcher fermato")

    def _build_media_sessions(self, config: dict) -> list[MediaSession]:
        sessions: list[MediaSession] = []
        active_servers: set[str] = set()
        servers = (
            ("emby", "emby_enabled", "emby_url", "emby_api_key", "emby_username"),
            ("jellyfin", "jellyfin_enabled", "jellyfin_url", "jellyfin_api_key", "jellyfin_username"),
        )
        for server_id, enabled_key, url_key, key_key, user_key in servers:
            if not config.get(enabled_key):
                self._session_cache.pop(server_id, None)
                continue
            url = str(config.get(url_key, "")).strip()
            api_key = str(config.get(key_key, "")).strip()
            username = str(config.get(user_key, "")).strip()
            if not url or not api_key or not username:
                self._session_cache.pop(server_id, None)
                continue
            active_servers.add(server_id)
            cache_key = (server_id, url, api_key, username)
            cached = self._session_cache.get(server_id)
            if cached and cached[0] == cache_key:
                sessions.append(cached[1])
                continue
            try:
                client = MediaServerClient(url, api_key, server_id)
                user_id = client.resolve_user_id(username)
                if not user_id:
                    self._session_cache.pop(server_id, None)
                    self._log(f"{client.display_name}: user not found ({username})")
                    continue
                session = MediaSession(server_id, client, user_id, username)
                self._session_cache[server_id] = (cache_key, session)
                sessions.append(session)
            except Exception as exc:
                self._session_cache.pop(server_id, None)
                label = "Jellyfin" if server_id == "jellyfin" else "Emby"
                self._log(f"{label}: {exc}")
        for server_id in list(self._session_cache):
            if server_id not in active_servers:
                self._session_cache.pop(server_id, None)
        return sessions

    def _find_active_playback(
        self, sessions: list[MediaSession], xtream_host: str
    ) -> tuple[PlayingItem | None, MediaSession | None]:
        playing: PlayingItem | None = None
        active_session: MediaSession | None = None
        for session in sessions:
            try:
                item = self._find_user_playing(session.client, session.username, xtream_host)
            except Exception as exc:
                self._log(f"{session.client.display_name}: {exc}")
                continue
            if not item:
                continue
            item.server_id = session.server_id
            if playing is None:
                playing = item
                active_session = session
            elif playing.key != item.key:
                self._log(
                    f"Riproduzione su più server; mantengo {playing.server_id}: "
                    f"{playing.display_label()}"
                )
        return playing, active_session

    def _tick(self, config: dict) -> None:
        auto_dl = bool(config.get("enabled"))
        assist_on = bool(
            config.get("auto_intro_skip_enabled") or config.get("auto_subs_enabled")
        )
        creds = load_credentials()
        host = creds.get("host", "")
        xtream_user = creds.get("user", "")
        xtream_pw = creds.get("password", "")
        if auto_dl and (not host or not xtream_user or not xtream_pw):
            return

        sessions = self._build_media_sessions(config)
        if not sessions:
            return

        if auto_dl and config.get("prompt_delete_completed", True):
            if not self._pruned_incomplete_prompts:
                try:
                    removed = prune_incomplete_deletion_prompts()
                    self._pruned_incomplete_prompts = True
                    if removed:
                        self._log(
                            f"Rimossi {removed} prompt eliminazione per serie "
                            "non ancora concluse su TMDB"
                        )
                except Exception as exc:
                    self._log(f"Prune prompt eliminazione: {exc}")
            now = time.time()
            if now - self._last_deletion_scan >= self.DELETION_SCAN_INTERVAL:
                self._last_deletion_scan = now
                for session in sessions:
                    try:
                        added = scan_completed_series_prompts(session.client, session.user_id)
                        if added:
                            self._log(
                                f"Scan serie completate ({session.client.display_name}): "
                                f"{added} in attesa eliminazione"
                            )
                    except Exception as exc:
                        self._log(f"Scan eliminazione serie ({session.client.display_name}): {exc}")

        # Drain cross-process continue-download requests, then periodically rescan.
        if auto_dl:
            self._drain_pending_continue_downloads()
            if continue_download_enabled(config):
                now = time.time()
                if now - self._last_continue_scan >= self.CONTINUE_DOWNLOAD_SCAN_INTERVAL:
                    self._last_continue_scan = now
                    try:
                        result = scan_and_enqueue_continue_downloads(config=config)
                        queued = int(result.get("queued") or 0)
                        found = int(result.get("episodes") or 0)
                        if found:
                            self._log(
                                f"Scan serie incomplete: {found} episodi .strm nuovi "
                                f"({queued} in coda file)"
                            )
                        self._drain_pending_continue_downloads()
                    except Exception as exc:
                        self._log(f"Scan continue-download: {exc}")

        playing, active_session = self._find_active_playback(sessions, host or "")
        if playing and active_session:
            if self._watching_item and self._watching_item.key != playing.key:
                self._record_playback(self._watching_item)
                self._prefetch_key = None
                self._prefetch_buffer_logged_key = None
                self._prefetch_decision_key = None
                self._prefetch_stay_notified_key = None
            if assist_on and playing.item_type == "Episode":
                self._maybe_schedule_playback_assist(playing, active_session, config)
            if self._deferred_xtream_assist:
                # Keep delaying while a strm (or any item) is still playing.
                self._xtream_assist_idle_after = 0.0
            if auto_dl and playing.blocks_download:
                prefetch_on = bool(config.get("prefetch_playing_strm"))
                if prefetch_on:
                    self._ensure_playing_prefetch(
                        playing,
                        config,
                        host,
                        xtream_user,
                        xtream_pw,
                    )
                priority_active = self._priority_download_active_for(playing)
                if not self._xtream_stream_active:
                    if prefetch_on and priority_active:
                        self._log(
                            f"Riproduzione strm ({playing.display_label()}): "
                            "prefetch prioritario attivo"
                        )
                    else:
                        self._log(
                            f"Riproduzione strm ({playing.display_label()}): download in pausa"
                        )
                self._xtream_stream_active = True
                # Cancel non-priority downloads only.
                if not priority_active:
                    self._active_proc_cancel.set()
            elif auto_dl:
                if self._xtream_stream_active:
                    self._log("Riproduzione da file locale: download consentito")
                self._xtream_stream_active = False
                self._prefetch_key = None
                self._prefetch_buffer_logged_key = None
                self._prefetch_decision_key = None
                self._prefetch_stay_notified_key = None
            else:
                self._xtream_stream_active = False
            self._watching_item = playing
            self._watching_session = active_session
            return

        if self._watching_item:
            ended = self._watching_item
            ended_session = self._watching_session
            was_strm = self._xtream_stream_active
            self._watching_item = None
            self._watching_session = None
            self._xtream_stream_active = False
            self._prefetch_key = None
            self._prefetch_buffer_logged_key = None
            self._prefetch_decision_key = None
            self._prefetch_stay_notified_key = None
            self._record_playback(ended)

            if was_strm and self._deferred_xtream_assist:
                self._xtream_assist_idle_after = time.time() + DEFER_XTREAM_ASSIST_IDLE_SEC
                self._log(
                    f"Assist Xtream in attesa {DEFER_XTREAM_ASSIST_IDLE_SEC}s "
                    f"dopo strm (1 connessione provider)"
                )

            if auto_dl:
                if was_strm and self._paused_download is not None:
                    self._skip_cooldown_once = True
                    self._cooldown_until = 0.0
                    self._log(f"Fine riproduzione strm: ripresa download ({ended.display_label()})")
                else:
                    cooldown = max(30, int(config.get("cooldown_seconds", 90)))
                    self._cooldown_until = time.time() + cooldown
                    self._log(f"Fine riproduzione: {ended.display_label()} (pausa {cooldown}s)")

                if ended.item_type == "Episode" and ended_session:
                    self._enqueue_subsequent(
                        media=ended_session.client,
                        user_id=ended_session.user_id,
                        ended=ended,
                        dest_root=str(config.get("series_dest", "")),
                        xtream_host=host,
                        xtream_user=xtream_user,
                        xtream_pw=xtream_pw,
                        allow_4k=bool(config.get("allow_4k")),
                    )
                    self._maybe_prompt_series_deletion(
                        ended_session.client, ended_session.user_id, ended, config,
                    )
                elif ended.item_type == "Movie" and ended_session:
                    self._maybe_cleanup_watched_movie(ended, ended_session, config)

        self._maybe_run_deferred_xtream_assist()

        if not auto_dl:
            return

        # While an Xtream strm is playing, only a priority prefetch may run.
        if self._xtream_stream_active:
            return

        if self._skip_cooldown_once:
            self._skip_cooldown_once = False
        elif time.time() < self._cooldown_until:
            return

        with self._lock:
            if self._download_thread and self._download_thread.is_alive():
                return
            paused = self._paused_download
            if paused:
                self._paused_download = None
                self._start_download_thread(paused)
                return
            if not self._queue:
                return
            item = self._queue.pop(0)

        dest_root = str(config.get("series_dest", ""))
        prepared = self._prepare_download(
            item,
            dest_root,
            host,
            xtream_user,
            xtream_pw,
            allow_4k=bool(config.get("allow_4k")),
        )
        if prepared:
            self._start_download_thread(prepared)

    def _maybe_schedule_playback_assist(
        self,
        playing: PlayingItem,
        session: MediaSession,
        config: dict,
    ) -> None:
        """When an episode starts, queue intro-skip / IT subs for the season from that ep."""
        if playing.item_type != "Episode":
            return
        if playing.season is None or playing.episode is None or not playing.series_id:
            return
        season = int(playing.season)
        episode = int(playing.episode)
        # Per-episode so later episodes still get intro after the first of the season.
        assist_key = (
            f"{playing.server_id}:{playing.item_id}:"
            f"intro={int(bool(config.get('auto_intro_skip_enabled')))}:"
            f"subs={int(bool(config.get('auto_subs_enabled')))}"
        )
        if assist_key in self._assist_scheduled:
            return
        self._assist_scheduled.add(assist_key)

        series_folder = ""
        strm_path = self._resolve_playing_strm_path(playing, config) or ""
        if strm_path:
            series_folder = os.path.basename(os.path.dirname(os.path.dirname(strm_path)))
        if not series_folder and playing.item_path:
            mapped = map_media_server_path_to_local(
                playing.item_path,
                server=playing.server_id or session.server_id,
                config=config,
            )
            if mapped:
                series_folder = os.path.basename(os.path.dirname(os.path.dirname(mapped)))
                if mapped.lower().endswith(".strm"):
                    strm_path = mapped

        defer_xtream = bool(playing.blocks_download)
        payload = {
            "playing": playing,
            "session": session,
            "config": config,
            "series_folder": series_folder,
            "strm_path": strm_path,
            "assist_key": assist_key,
        }
        if defer_xtream:
            self._deferred_xtream_assist = payload
            self._xtream_assist_idle_after = 0.0
            if series_folder and playing.series_id and config.get("auto_intro_skip_enabled"):
                try:
                    from intro_skip import save_intro_season_backfill

                    save_intro_season_backfill(
                        series_id=playing.series_id,
                        series_folder=series_folder,
                        from_season=season,
                        from_episode=episode,
                        user_id=session.user_id,
                        server=playing.server_id or session.server_id or "jellyfin",
                    )
                except Exception:
                    pass

        def _job() -> None:
            self._run_playback_assist_job(payload, xtream_ok=not defer_xtream)

        try:
            from intro_skip import schedule_intro_job

            started = schedule_intro_job(f"assist:{assist_key}", _job)
            if not started:
                self._assist_scheduled.discard(assist_key)
        except Exception:
            threading.Thread(target=_job, daemon=True, name="playback-assist").start()

    def _run_playback_assist_job(self, payload: dict, *, xtream_ok: bool) -> None:
        playing: PlayingItem = payload["playing"]
        session: MediaSession = payload["session"]
        config: dict = payload["config"]
        series_folder = str(payload.get("series_folder") or "")
        strm_path = str(payload.get("strm_path") or "")
        if playing.season is None or playing.episode is None:
            return
        season = int(playing.season)
        episode = int(playing.episode)
        client = session.client
        user_id = session.user_id
        series_id = playing.series_id
        label = playing.display_label()
        server = playing.server_id or session.server_id or "jellyfin"

        self._log(
            f"Assist riproduzione: {label} "
            f"(intro={bool(config.get('auto_intro_skip_enabled'))}, "
            f"subs={bool(config.get('auto_subs_enabled'))}"
            f"{'' if xtream_ok else ', xtream=defer'})"
        )

        if config.get("auto_intro_skip_enabled") and series_folder and not xtream_ok:
            try:
                from intro_skip import ensure_intro_for_episode

                info = ensure_intro_for_episode(
                    client,
                    item_id=playing.item_id,
                    series_folder=series_folder,
                    season=season,
                    episode=episode,
                    strm_path=strm_path,
                    series_id=series_id,
                    config=config,
                    log=self._log,
                    allow_xtream=False,
                    prefer_sample=True,
                    keep_sample=True,
                )
                self._log(
                    f"Intro skip S{season:02d}E{episode:02d}: "
                    f"ok={info.get('ok')} skip={info.get('skipped')} "
                    f"err={info.get('error') or '-'}"
                )
            except Exception as exc:
                self._log(f"Intro skip episodio corrente: {exc}")
        elif config.get("auto_intro_skip_enabled") and not series_folder:
            self._log(f"Intro skip: cartella serie sconosciuta per {label}")

        if config.get("auto_subs_enabled"):
            try:
                from auto_subtitles import ensure_season_subtitles

                summary = ensure_season_subtitles(
                    client,
                    user_id=user_id,
                    series_id=series_id,
                    season=season,
                    config=config,
                    from_episode=episode,
                    prefer_forced=bool(config.get("auto_subs_prefer_forced", True)),
                    language=str(config.get("auto_subs_language") or "it"),
                    log=self._log,
                    verify_playback_info=xtream_ok,
                )
                self._log(
                    f"Sottotitoli S{season:02d} da E{episode:02d}: "
                    f"ok={summary.get('ok')} skip={summary.get('skipped')} "
                    f"fail={summary.get('failed')}"
                )
            except Exception as exc:
                self._log(f"Sottotitoli errore: {exc}")

        if not xtream_ok:
            self._log(
                "Intro resto serie differito: riproduzione strm "
                "(il provider permette 1 sola connessione)"
            )
            return

        if config.get("auto_intro_skip_enabled") and series_folder:
            try:
                from intro_skip import (
                    ensure_remaining_series_intros,
                )

                summary = ensure_remaining_series_intros(
                    client,
                    user_id=user_id,
                    series_id=series_id,
                    series_folder=series_folder,
                    from_season=season,
                    from_episode=episode,
                    config=config,
                    log=self._log,
                    server=server,
                    allow_xtream=True,
                    include_current=True,
                )
                self._log(
                    f"Intro skip serie da S{season:02d}E{episode:02d}: "
                    f"ok={summary.get('ok')} skip={summary.get('skipped')} "
                    f"fail={summary.get('failed')} dl={summary.get('downloaded')} "
                    f"clone={summary.get('cloned')} "
                    f"targets={summary.get('targets')}"
                    f"{' deferred=' + str(summary.get('next') or '') if summary.get('deferred') else ''}"
                )
            except Exception as exc:
                self._log(f"Intro skip resto serie: {exc}")

    def _maybe_run_deferred_xtream_assist(self) -> None:
        after = self._xtream_assist_idle_after
        payload = self._deferred_xtream_assist
        if self._watching_item is not None:
            return
        from core import xtream_playback_blocks_extra_streams

        if xtream_playback_blocks_extra_streams():
            return
        from intro_skip import load_intro_season_backfills, schedule_intro_job

        pending_backfill = load_intro_season_backfills()
        if payload and after > 0 and time.time() >= after:
            self._deferred_xtream_assist = None
            self._xtream_assist_idle_after = 0.0
            assist_key = str(payload.get("assist_key") or "deferred")
            self._log("Assist Xtream ripreso dopo idle (intro + probe sottotitoli)")

            def _job() -> None:
                self._run_playback_assist_job(payload, xtream_ok=True)

            try:
                schedule_intro_job(f"assist-deferred:{assist_key}", _job)
            except Exception:
                threading.Thread(
                    target=_job, daemon=True, name="playback-assist-deferred"
                ).start()
            return
        if pending_backfill and after > 0 and time.time() >= after:
            self._xtream_assist_idle_after = 0.0
            self._log("Intro skip: backfill stagione da coda persistente")

            def _backfill() -> None:
                self._run_intro_season_backfills()

            try:
                schedule_intro_job("intro-season-backfill", _backfill)
            except Exception:
                threading.Thread(
                    target=_backfill, daemon=True, name="intro-backfill"
                ).start()

    def _run_intro_season_backfills(self) -> None:
        from intro_skip import (
            ensure_remaining_series_intros,
            load_intro_season_backfills,
        )
        from strm_seasons import _build_jellyfin_client

        config = load_auto_download_config()
        client, uid = _build_jellyfin_client(config)
        if not client:
            return
        for row in load_intro_season_backfills():
            series_id = str(row.get("series_id") or "")
            folder = str(row.get("series_folder") or "")
            try:
                from_season = int(row.get("from_season") or row.get("season") or 0)
                from_episode = int(row.get("from_episode") or 1)
            except (TypeError, ValueError):
                continue
            if not series_id or not folder or from_season < 1:
                continue
            user_id = str(row.get("user_id") or uid or "")
            server = str(row.get("server") or "jellyfin")
            try:
                summary = ensure_remaining_series_intros(
                    client,
                    user_id=user_id,
                    series_id=series_id,
                    series_folder=folder,
                    from_season=from_season,
                    from_episode=from_episode,
                    config=config,
                    log=self._log,
                    server=server,
                    allow_xtream=True,
                    include_current=True,
                )
                self._log(
                    f"Intro skip backfill da S{from_season:02d}E{from_episode:02d}: "
                    f"ok={summary.get('ok')} skip={summary.get('skipped')} "
                    f"fail={summary.get('failed')} dl={summary.get('downloaded')} "
                    f"clone={summary.get('cloned')}"
                    f"{' deferred=' + str(summary.get('next') or '') if summary.get('deferred') else ''}"
                )
            except Exception as exc:
                self._log(f"Intro skip backfill S{from_season:02d}: {exc}")
                continue

    def _start_download_thread(self, paused: PausedDownload) -> None:
        self._download_thread = threading.Thread(
            target=self._run_download,
            args=(paused,),
            daemon=True,
        )
        self._download_thread.start()

    def _priority_download_active_for(self, playing: PlayingItem) -> bool:
        return self._prefetch_key == playing.key

    def _resolve_playing_strm_path(self, playing: PlayingItem, config: dict) -> str | None:
        server = playing.server_id or "emby"
        mapped = map_media_server_path_to_local(
            playing.item_path, server=server, config=config
        )
        if mapped and os.path.isfile(mapped):
            return mapped
        if playing.item_path and os.path.isfile(playing.item_path):
            return playing.item_path
        return None

    def _ensure_playing_prefetch(
        self,
        playing: PlayingItem,
        config: dict,
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
    ) -> None:
        if self._prefetch_key == playing.key:
            with self._lock:
                if self._download_thread and self._download_thread.is_alive():
                    return
                paused = self._paused_download
                if paused and paused.priority:
                    self._paused_download = None
                    self._start_download_thread(paused)
                    return
            # Fall through to (re)prepare if nothing is running.

        strm_path = self._resolve_playing_strm_path(playing, config)
        if not strm_path:
            self._log(
                f"Prefetch: percorso .strm non leggibile per {playing.display_label()} "
                f"({playing.item_path})"
            )
            return

        with self._lock:
            if self._download_thread and self._download_thread.is_alive():
                ctx = self._download_context or {}
                job = ctx.get("job")
                if job and getattr(job, "priority", False) and self._prefetch_key == playing.key:
                    return
                # Cancel non-priority work so prefetch can start.
                if not (job and getattr(job, "priority", False)):
                    self._active_proc_cancel.set()
                return

            paused = self._paused_download
            if paused and paused.priority and self._prefetch_key == playing.key:
                self._paused_download = None
                self._prefetch_key = playing.key
                self._start_download_thread(paused)
                return
            # Drop a non-priority paused job so prefetch can take the slot;
            # it will be rebuilt from the queue later if still needed.
            if paused and not paused.priority:
                self._log(
                    f"Prefetch: scarto download in pausa non prioritario "
                    f"({paused.item.label})"
                )
                self._paused_download = None
                # Put it back at the front of the queue when possible.
                try:
                    self._queue.insert(0, paused.item)
                except Exception:
                    pass

        prepared = self._prepare_playing_prefetch(
            playing,
            strm_path,
            config,
            xtream_host,
            xtream_user,
            xtream_pw,
        )
        if not prepared:
            return
        self._prefetch_key = playing.key
        self._prefetch_buffer_logged_key = None
        self._prefetch_decision_key = None
        self._prefetch_stay_notified_key = None
        self._start_download_thread(prepared)

    def _prepare_playing_prefetch(
        self,
        playing: PlayingItem,
        strm_path: str,
        config: dict,
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
    ) -> PausedDownload | None:
        if playing.item_type == "Movie":
            # Progressive proxy owns strm playback; skip watcher prefetch/switch.
            try:
                from stream_proxy import stream_proxy_enabled

                if stream_proxy_enabled(config):
                    self._log(
                        f"Prefetch saltato (proxy attivo): {playing.display_label()}"
                    )
                    return None
            except ImportError:
                pass
            url = read_strm_url(strm_path)
            if not url:
                self._log(f"Prefetch film: URL assente in {strm_path}")
                return None
            ext = url.rsplit(".", 1)[-1].split("?")[0].strip().lower() or "mkv"
            if not re.fullmatch(r"[a-z0-9]{2,5}", ext):
                ext = "mkv"
            folder_name = os.path.basename(os.path.dirname(strm_path)) or playing.title
            _folder, output_file = build_movie_output(
                folder_name, ext, DOWNLOAD_MOVIES_PATH, strm_path=strm_path
            )
            ensure_movie_output_is_file(output_file)
            write_movie_strm_url_sidecar(output_file, url, strm_path=strm_path)
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                # Incomplete resume is handled by run_ytdlp; only skip if finalize-ready
                # and no .strm left — still allow resume of partial files.
                pass
            item = QueueItem(
                series_name=folder_name,
                season=0,
                episode=0,
                label=playing.display_label(),
                strm_path=strm_path,
                kind="movie",
            )
            return PausedDownload(
                item=item,
                dest_root=DOWNLOAD_MOVIES_PATH,
                xtream_host=xtream_host,
                xtream_user=xtream_user,
                xtream_pw=xtream_pw,
                output_file=output_file,
                url=url,
                priority=True,
                kind="movie",
                bitrate_bps=int(playing.bitrate_bps or 0),
                source_item_id=str(playing.item_id or ""),
                source_session_id=str(playing.session_id or ""),
                server_id=str(playing.server_id or ""),
            )

        if playing.item_type == "Episode":
            try:
                from stream_proxy import stream_proxy_enabled

                if stream_proxy_enabled(config):
                    self._log(
                        f"Prefetch saltato (proxy attivo): {playing.display_label()}"
                    )
                    return None
            except ImportError:
                pass
            dest_root = str(config.get("series_dest") or "")
            item = QueueItem(
                series_name=playing.series_name,
                season=int(playing.season or 0),
                episode=int(playing.episode or 0),
                label=playing.display_label(),
                strm_path=strm_path,
                kind="episode",
            )
            prepared = self._prepare_download(
                item,
                dest_root,
                xtream_host,
                xtream_user,
                xtream_pw,
                allow_4k=bool(config.get("allow_4k")),
            )
            if prepared:
                prepared.priority = True
                prepared.kind = "episode"
                prepared.bitrate_bps = int(playing.bitrate_bps or 0)
                prepared.source_item_id = str(playing.item_id or "")
                prepared.source_session_id = str(playing.session_id or "")
                prepared.server_id = str(playing.server_id or "")
            return prepared

        return None

    def _find_user_playing(
        self, media: MediaServerClient, username: str, xtream_host: str
    ) -> PlayingItem | None:
        target = username.strip().lower()
        for session in media.get_sessions():
            if str(session.get("UserName", "")).lower() != target:
                continue
            item = session.get("NowPlayingItem") or {}
            item_type = item.get("Type")
            item_path = str(item.get("Path", ""))
            blocks = playback_blocks_xtream_download(item_path, xtream_host)
            session_id = str(session.get("Id") or "")
            play_state = session.get("PlayState") or {}
            try:
                position_ticks = int(play_state.get("PositionTicks") or 0)
            except (TypeError, ValueError):
                position_ticks = 0
            try:
                run_time_ticks = int(item.get("RunTimeTicks") or 0)
            except (TypeError, ValueError):
                run_time_ticks = 0
            bitrate_bps = 0
            try:
                bitrate_bps = int(item.get("Bitrate") or 0)
            except (TypeError, ValueError):
                bitrate_bps = 0
            if bitrate_bps <= 0:
                for stream in item.get("MediaStreams") or []:
                    if str(stream.get("Type") or "") == "Video":
                        try:
                            bitrate_bps = int(stream.get("BitRate") or 0)
                        except (TypeError, ValueError):
                            bitrate_bps = 0
                        if bitrate_bps > 0:
                            break

            if item_type == "Episode":
                season = item.get("ParentIndexNumber")
                episode = item.get("IndexNumber")
                series_id = item.get("SeriesId")
                series_name = item.get("SeriesName") or item.get("Name") or ""
                if season is None or episode is None or not series_id:
                    continue
                return PlayingItem(
                    item_type="Episode",
                    item_id=str(item.get("Id", "")),
                    title=str(series_name),
                    item_path=item_path,
                    blocks_download=blocks,
                    series_id=str(series_id),
                    series_name=str(series_name),
                    season=int(season),
                    episode=int(episode),
                    session_id=session_id,
                    bitrate_bps=bitrate_bps,
                    position_ticks=position_ticks,
                    run_time_ticks=run_time_ticks,
                )

            if item_type == "Movie":
                name = str(item.get("Name", "")).strip()
                if not name:
                    continue
                return PlayingItem(
                    item_type="Movie",
                    item_id=str(item.get("Id", "")),
                    title=name,
                    item_path=item_path,
                    blocks_download=blocks,
                    session_id=session_id,
                    bitrate_bps=bitrate_bps,
                    position_ticks=position_ticks,
                    run_time_ticks=run_time_ticks,
                )
        return None

    @staticmethod
    def _record_playback(item: PlayingItem) -> None:
        try:
            append_playback_history(
                {
                    "key": item.key,
                    "type": item.history_type(),
                    "title": item.display_label(),
                    "source": "strm" if item.blocks_download else "locale",
                    "finished_at": time.strftime("%d/%m/%Y %H:%M"),
                }
            )
        except OSError:
            pass

    def _prepare_download(
        self,
        item: QueueItem,
        dest_root: str,
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
        *,
        allow_4k: bool = False,
    ) -> PausedDownload | None:
        match = None
        if item.strm_path:
            match = resolve_episode_from_strm_path(item.strm_path, xtream_host)
        if not match:
            match = find_xtream_episode(
                xtream_host, xtream_user, xtream_pw,
                item.series_name, item.season, item.episode,
                allow_4k=allow_4k,
            )
        if not match:
            self._log(f"Episodio non trovato su Xtream: {item.label}")
            with self._lock:
                self._queued_keys.discard(f"{item.series_name}:{item.season}:{item.episode}")
            return None

        _folder, output_file = build_episode_output(
            item.series_name, item.season, item.episode, match["ext"], dest_root,
            strm_path=item.strm_path or None,
        )
        if os.path.exists(output_file):
            result = finalize_after_local_download(
                output_file, strm_path=item.strm_path or None
            )
            notify = ", ".join(result.get("notify") or [])
            self._log(
                f"Già presente, salto: {item.label}"
                + (f" ({notify})" if notify else "")
            )
            with self._lock:
                self._queued_keys.discard(f"{item.series_name}:{item.season}:{item.episode}")
            return None

        return PausedDownload(
            item=item,
            dest_root=dest_root,
            xtream_host=xtream_host,
            xtream_user=xtream_user,
            xtream_pw=xtream_pw,
            output_file=output_file,
            url=match["url"],
        )

    def _run_download(self, job: PausedDownload) -> None:
        if job.kind == "movie" or job.item.kind == "movie":
            key = f"movie:{job.item.strm_path or job.item.label}"
            history_type = "Movie"
        else:
            key = f"{job.item.series_name}:{job.item.season}:{job.item.episode}"
            history_type = "Series"
        resume = growing_download_bytes(job.output_file) > 0
        cfg = load_auto_download_config()
        target_buffer_s = max(30, int(cfg.get("prefetch_buffer_seconds") or 120))
        min_ratio = float(cfg.get("prefetch_min_speed_ratio") or 1.3)
        max_wait_s = max(60, int(cfg.get("prefetch_max_wait_seconds") or 180))
        min_bytes = max(10, int(cfg.get("prefetch_buffer_mb") or 20)) * 1024 * 1024
        auto_switch = bool(cfg.get("prefetch_auto_switch", True))
        download_started_at = time.time()
        # Account for already-downloaded bytes when resuming.
        baseline_bytes = growing_download_bytes(job.output_file) if resume else 0

        with self._lock:
            self._status.downloading = True
            self._status.current_download = job.item.label
            self._status.download_progress = 0.0
            self._status.download_progress_text = "0%"
            self._active_proc_cancel.clear()
            self._download_context = {"job": job, "key": key}
        self._persist_status()

        try:
            if self._xtream_stream_active and not job.priority:
                self._pause_download(job)
                return

            folder = os.path.dirname(job.output_file)
            prepare_output_dir(folder)
            if resume:
                self._log(f"Ripresa download: {job.item.label}")
            else:
                prefix = "Prefetch" if job.priority else "Download"
                self._log(f"{prefix} avviato: {job.item.label}")

            def should_cancel() -> bool:
                try:
                    out = os.path.realpath(job.output_file)
                except OSError:
                    out = job.output_file
                with self._lock:
                    if out in self._abort_output_files:
                        return True
                if job.priority:
                    return False
                return self._active_proc_cancel.is_set() or self._xtream_stream_active

            def on_progress(value: float, text: str) -> None:
                with self._lock:
                    self._status.download_progress = min(max(value, 0.0), 1.0)
                    self._status.download_progress_text = text
                if job.priority and self._prefetch_decision_key != self._prefetch_key:
                    self._maybe_evaluate_prefetch_switch(
                        job,
                        started_at=download_started_at,
                        baseline_bytes=baseline_bytes,
                        target_buffer_seconds=target_buffer_s,
                        min_speed_ratio=min_ratio,
                        min_bytes=min_bytes,
                        max_wait_seconds=max_wait_s,
                        auto_switch=auto_switch,
                    )
                now = time.time()
                if value >= 1.0 or now - self._last_progress_persist >= 1.0:
                    self._last_progress_persist = now
                    self._persist_status()

            run_ytdlp(
                job.url, job.output_file,
                label=job.item.label,
                should_cancel=should_cancel,
                resume=resume,
                progress_callback=on_progress,
                strm_path=job.item.strm_path or None,
                history_entry={
                    "key": key,
                    "type": history_type,
                    "title": job.item.label,
                    "mode": "prefetch" if job.priority else "automatic",
                },
            )
            done_msg = (
                f"Prefetch completato: {job.item.label}"
                if job.priority
                else f"Download completato: {job.item.label}"
            )
            self._log(done_msg)
            if job.priority and self._prefetch_decision_key != self._prefetch_key:
                # Completed before mid-switch decision: treat as local-ready.
                self._notify_prefetch_ready_on_tv(
                    job,
                    header="File locale pronto",
                    text=(
                        f"«{job.item.label}» è scaricato. "
                        "Ferma e riavvia (o accetta lo switch) per il file locale."
                    ),
                )
            with self._lock:
                self._status.last_action = f"Completato {job.item.label}"
                self._status.last_error = ""
                self._queued_keys.discard(key)
        except DownloadCancelled:
            try:
                out = os.path.realpath(job.output_file)
            except OSError:
                out = job.output_file
            with self._lock:
                aborted = out in self._abort_output_files
                self._abort_output_files.discard(out)
                if aborted and not self._pending_watched_movie_cleanup:
                    self._pending_watched_movie_cleanup = out
            if aborted:
                self._log(f"Download interrotto (film visto): {job.item.label}")
                with self._lock:
                    if self._paused_download is job:
                        self._paused_download = None
                    self._queued_keys.discard(key)
            else:
                self._pause_download(job)
        except Exception as exc:
            self._log(f"Download fallito ({job.item.label}): {exc}")
            with self._lock:
                self._status.last_error = str(exc)
                self._status.last_action = f"Fallito {job.item.label}"
                self._queued_keys.discard(key)
        finally:
            with self._lock:
                if self._paused_download is None:
                    self._status.downloading = False
                    self._status.current_download = ""
                    self._status.download_progress = 0.0
                    self._status.download_progress_text = ""
                else:
                    self._status.downloading = False
                self._download_context = None
                pending = self._pending_watched_movie_cleanup
            self._persist_status()
            if pending:
                try:
                    pending_real = os.path.realpath(pending)
                    out_real = os.path.realpath(job.output_file)
                except OSError:
                    pending_real = pending
                    out_real = job.output_file
                if pending_real == out_real:
                    self._run_watched_movie_cleanup(pending)

    def _current_playback_position_seconds(self, job: PausedDownload) -> float:
        """Live playhead in seconds (falls back to last known PlayingItem position)."""
        with self._lock:
            playing = self._watching_item
            media_session = self._watching_session
        position_ticks = int(playing.position_ticks if playing else 0)
        session_id = (
            (playing.session_id if playing else "")
            or job.source_session_id
            or ""
        )
        if media_session and session_id:
            try:
                for session in media_session.client.get_sessions():
                    if str(session.get("Id") or "") != session_id:
                        continue
                    play_state = session.get("PlayState") or {}
                    position_ticks = int(
                        play_state.get("PositionTicks") or position_ticks or 0
                    )
                    break
            except Exception:
                pass
        return max(0.0, position_ticks / 10_000_000.0)

    def _maybe_evaluate_prefetch_switch(
        self,
        job: PausedDownload,
        *,
        started_at: float,
        baseline_bytes: int,
        target_buffer_seconds: float,
        min_speed_ratio: float,
        min_bytes: int,
        max_wait_seconds: float,
        auto_switch: bool,
    ) -> None:
        # After switch_local we stop. After stay_strm we keep evaluating so a
        # later catch-up can still offer switch_local.
        if self._prefetch_decision_key == self._prefetch_key:
            return
        size = growing_download_bytes(job.output_file)
        gained = max(0, size - int(baseline_bytes or 0))
        elapsed = max(0.5, time.time() - started_at)
        position_seconds = self._current_playback_position_seconds(job)

        decision = evaluate_prefetch_switch(
            downloaded_bytes=size,
            bytes_gained=gained,
            elapsed_seconds=elapsed,
            bitrate_bps=job.bitrate_bps,
            position_seconds=position_seconds,
            target_buffer_seconds=target_buffer_seconds,
            min_speed_ratio=min_speed_ratio,
            min_bytes=min_bytes,
            max_wait_seconds=max_wait_seconds,
        )

        if decision.action == "wait":
            return

        if decision.action == "stay_strm":
            if self._prefetch_stay_notified_key != self._prefetch_key:
                self._prefetch_stay_notified_key = self._prefetch_key
                self._log(
                    f"Prefetch decisione=stay_strm per {job.item.label}: {decision.reason}"
                )
                self._notify_prefetch_ready_on_tv(
                    job,
                    header="Resto sullo stream",
                    text=(
                        f"«{job.item.label}»: {decision.reason}"
                    ),
                )
            return

        # switch_local — lock so we notify/switch once
        self._prefetch_decision_key = self._prefetch_key
        self._log(
            f"Prefetch decisione={decision.action} per {job.item.label}: {decision.reason}"
        )

        switched = False
        if auto_switch:
            switched = self._try_switch_playback_to_local(job)
        ahead = max(0.0, decision.ahead_seconds)
        if switched:
            self._notify_prefetch_ready_on_tv(
                job,
                header="Passato al file locale",
                text=(
                    f"«{job.item.label}»: locale oltre la posizione "
                    f"(+{ahead:.0f}s, {decision.speed_ratio:.1f}×). "
                    "Riproduzione locale avviata."
                ),
            )
        else:
            self._notify_prefetch_ready_on_tv(
                job,
                header="Puoi passare al locale",
                text=(
                    f"«{job.item.label}»: locale oltre la posizione "
                    f"(+{ahead:.0f}s, {decision.speed_ratio:.1f}×). "
                    "Ferma e riavvia la riproduzione per il file locale."
                ),
            )

    def _try_switch_playback_to_local(self, job: PausedDownload) -> bool:
        with self._lock:
            playing = self._watching_item
            media_session = self._watching_session
        if not media_session:
            self._log("Switch locale: nessuna sessione attiva")
            return False

        session_id = (
            (playing.session_id if playing else "")
            or job.source_session_id
            or ""
        )
        if not session_id:
            self._log("Switch locale: session id assente")
            return False

        server = job.server_id or (playing.server_id if playing else media_session.server_id)
        mapped_local = map_local_path_to_media_server(
            job.output_file, server=server or "emby"
        )
        if mapped_local:
            try:
                media_session.client.notify_library_paths(
                    [{"Path": mapped_local, "UpdateType": "Created"}]
                )
            except Exception as exc:
                self._log(f"Switch locale: Media/Updated fallito ({exc})")

        # Give the server a moment to pick up the growing file.
        time.sleep(1.5)
        local_item = None
        if mapped_local:
            try:
                local_item = media_session.client.find_item_by_path(mapped_local)
            except Exception as exc:
                self._log(f"Switch locale: lookup path fallito ({exc})")

        item_id = str((local_item or {}).get("Id") or job.source_item_id or "")
        if playing and playing.item_id and not local_item:
            item_id = playing.item_id

        if not item_id:
            self._log("Switch locale: item id non trovato")
            return False

        position_ticks = int(playing.position_ticks if playing else 0)
        # Refresh position from live session if possible.
        try:
            for session in media_session.client.get_sessions():
                if str(session.get("Id") or "") != session_id:
                    continue
                play_state = session.get("PlayState") or {}
                position_ticks = int(play_state.get("PositionTicks") or position_ticks or 0)
                break
        except Exception:
            pass

        try:
            media_session.client.play_item(
                session_id,
                item_id,
                start_position_ticks=position_ticks,
            )
            self._log(
                f"Switch locale richiesto (item={item_id[:8]}… pos={position_ticks // 10_000_000}s)"
            )
            return True
        except Exception as exc:
            self._log(f"Switch locale fallito: {exc}")
            try:
                media_session.client.stop_playback(session_id)
                time.sleep(0.5)
                media_session.client.play_item(
                    session_id,
                    item_id,
                    start_position_ticks=position_ticks,
                )
                self._log("Switch locale riuscito dopo Stop+Play")
                return True
            except Exception as exc2:
                self._log(f"Switch locale Stop+Play fallito: {exc2}")
                return False

    def _notify_prefetch_ready_on_tv(
        self,
        job: PausedDownload,
        *,
        header: str = "File locale pronto",
        text: str | None = None,
    ) -> None:
        """Push an on-screen message to the Emby/Jellyfin client that started prefetch."""
        with self._lock:
            playing = self._watching_item
            media_session = self._watching_session
        if not media_session:
            self._log("Prefetch: nessuna sessione media server per la notifica TV")
            return

        session_id = (
            (playing.session_id if playing else "")
            or job.source_session_id
            or ""
        )
        if not session_id:
            try:
                for session in media_session.client.get_sessions():
                    if str(session.get("UserName", "")).lower() != media_session.username.lower():
                        continue
                    if session.get("NowPlayingItem"):
                        session_id = str(session.get("Id") or "")
                        if session_id:
                            break
            except Exception as exc:
                self._log(f"Prefetch: lookup sessione fallito ({exc})")
                return

        if not session_id:
            self._log("Prefetch: session id TV non trovato")
            return

        message = text or (
            f"«{job.item.label}» è scaricato. "
            "Ferma e riavvia la riproduzione per usare il file locale senza freeze."
        )
        try:
            media_session.client.display_message(
                session_id,
                header=header,
                text=message,
                timeout_ms=30000,
            )
            self._log(
                f"Notifica TV inviata via {media_session.client.display_name} "
                f"(sessione {session_id[:8]}…): {header}"
            )
        except Exception as exc:
            self._log(f"Notifica TV fallita ({media_session.client.display_name}): {exc}")

    def _pause_download(self, job: PausedDownload) -> None:
        with self._lock:
            self._paused_download = job
            self._status.current_download = job.item.label
        self._log(f"Download in pausa: {job.item.label}")
        with self._lock:
            self._status.last_action = f"In pausa {job.item.label}"
        self._persist_status()

    def _movie_is_watched(
        self,
        ended: PlayingItem,
        session: MediaSession,
        config: dict,
    ) -> bool:
        threshold = float(config.get("watched_movie_threshold") or 0.90)
        played = None
        position_ticks = int(ended.position_ticks or 0)
        run_time_ticks = int(ended.run_time_ticks or 0)
        try:
            detail = session.client.get_item(
                session.user_id,
                ended.item_id,
                "UserData,RunTimeTicks,Path",
            )
            if detail:
                user_data = detail.get("UserData") or {}
                played = bool(user_data.get("Played"))
                try:
                    run_time_ticks = int(
                        detail.get("RunTimeTicks") or run_time_ticks or 0
                    )
                except (TypeError, ValueError):
                    pass
                try:
                    position_ticks = int(
                        user_data.get("PlaybackPositionTicks") or position_ticks or 0
                    )
                except (TypeError, ValueError):
                    pass
                # If marked played, Emby often resets position to 0 — keep ended ticks.
                if played and position_ticks <= 0:
                    position_ticks = int(ended.position_ticks or 0)
        except Exception as exc:
            self._log(f"Film visto: UserData non letto ({exc})")
        return is_media_considered_watched(
            played=played,
            position_ticks=position_ticks,
            run_time_ticks=run_time_ticks,
            threshold=threshold,
        )

    def _find_local_movie_for_ended(
        self,
        ended: PlayingItem,
        session: MediaSession,
        config: dict,
    ) -> str | None:
        server = session.server_id or "emby"
        mapped = map_media_server_path_to_local(
            ended.item_path, server=server, config=config
        )
        candidates: list[str] = []
        if mapped:
            candidates.append(mapped)
            if mapped.lower().endswith(".strm"):
                candidates.extend(find_local_files_for_strm(mapped))
            elif LOCAL_DOWNLOAD_MARKER not in os.path.basename(mapped):
                folder = os.path.dirname(mapped)
                if os.path.isdir(folder):
                    for name in os.listdir(folder):
                        if LOCAL_DOWNLOAD_MARKER in name:
                            candidates.append(os.path.join(folder, name))
        if mapped and mapped.lower().endswith(".strm"):
            candidates.extend(find_local_files_for_strm(mapped))

        with self._lock:
            ctx = self._download_context or {}
            paused = self._paused_download
        job = ctx.get("job") if isinstance(ctx, dict) else None
        if isinstance(job, PausedDownload) and (
            job.kind == "movie" or job.item.kind == "movie"
        ):
            candidates.append(job.output_file)
        if paused and (paused.kind == "movie" or paused.item.kind == "movie"):
            candidates.append(paused.output_file)

        seen: set[str] = set()
        for path in candidates:
            if not path:
                continue
            try:
                real = os.path.realpath(path)
            except OSError:
                real = path
            if real in seen:
                continue
            seen.add(real)
            if not os.path.isfile(real):
                continue
            ext = os.path.splitext(real)[1].lower()
            if ext == ".strm":
                continue
            if DOWNLOAD_MOVIES_PATH in real.replace("\\", "/") or (
                LOCAL_DOWNLOAD_MARKER in os.path.basename(real)
            ):
                return real
        return None

    def _abort_download_for_output(self, local_path: str) -> bool:
        """Request cancel of an active/paused download writing local_path."""
        try:
            target = os.path.realpath(local_path)
        except OSError:
            target = local_path
        active = False
        with self._lock:
            self._abort_output_files.add(target)
            self._pending_watched_movie_cleanup = target
            ctx = self._download_context or {}
            job = ctx.get("job") if isinstance(ctx, dict) else None
            if isinstance(job, PausedDownload):
                try:
                    if os.path.realpath(job.output_file) == target:
                        active = True
                        self._active_proc_cancel.set()
                except OSError:
                    pass
            paused = self._paused_download
            if paused:
                try:
                    if os.path.realpath(paused.output_file) == target:
                        self._paused_download = None
                except OSError:
                    pass
        return active

    def _run_watched_movie_cleanup(self, local_path: str) -> None:
        try:
            real = os.path.realpath(local_path)
        except OSError:
            real = local_path
        with self._lock:
            if self._pending_watched_movie_cleanup:
                try:
                    pending_real = os.path.realpath(self._pending_watched_movie_cleanup)
                except OSError:
                    pending_real = self._pending_watched_movie_cleanup
                if pending_real == real:
                    self._pending_watched_movie_cleanup = None
            self._abort_output_files.discard(real)

        result = delete_movie_local_and_restore_strm(real)
        errors = result.get("errors") or []
        if errors:
            self._log(
                f"Cleanup film visto fallito ({os.path.basename(real)}): "
                + "; ".join(str(e) for e in errors)
            )
            return
        deleted = len(result.get("local_deleted") or [])
        strm = result.get("strm_path") or ""
        notify = ", ".join(result.get("notify") or [])
        self._log(
            f"Film visto: rimosso locale ({deleted} file), ripristinato .strm"
            + (f" → {strm}" if strm else "")
            + (f" ({notify})" if notify else "")
        )
        with self._lock:
            self._status.last_action = f"Cleanup film visto: {os.path.basename(real)}"

    def _maybe_cleanup_watched_movie(
        self,
        ended: PlayingItem,
        session: MediaSession,
        config: dict,
    ) -> None:
        if not bool(config.get("cleanup_watched_movie_downloads", True)):
            return
        if ended.item_type != "Movie":
            return
        if not self._movie_is_watched(ended, session, config):
            local_path = self._find_local_movie_for_ended(ended, session, config)
            if local_path:
                self._log(
                    f"Fine film senza 'visto' — tengo il download locale "
                    f"({ended.display_label()})"
                )
            return
        local_path = self._find_local_movie_for_ended(ended, session, config)
        if not local_path:
            return
        self._log(
            f"Film considerato visto: cleanup locale → .strm "
            f"({ended.display_label()})"
        )
        if self._abort_download_for_output(local_path):
            return
        self._run_watched_movie_cleanup(local_path)

    def _maybe_prompt_series_deletion(
        self, media: MediaServerClient, user_id: str, ended: PlayingEpisode, config: dict,
    ) -> None:
        if not config.get("prompt_delete_completed", True):
            return
        episodes = media.get_series_episodes(user_id, ended.series_id, include_user_data=True)
        if not should_prompt_series_deletion(episodes, ended.season, ended.episode):
            return
        paths = find_series_download_paths(ended.series_name)
        if not paths:
            return
        if not series_production_finished(paths):
            self._log(
                f"Serie vista ma non ancora conclusa su TMDB: "
                f"nessuna richiesta eliminazione per {ended.series_name}"
            )
            return
        if add_deletion_prompt(ended.series_id, ended.series_name, paths):
            self._log(f"Serie completata: in attesa conferma eliminazione per {ended.series_name}")
            with self._lock:
                self._status.last_action = f"Serie completata: {ended.series_name}"

    def _drain_pending_continue_downloads(self) -> int:
        pending = take_pending_auto_downloads()
        if not pending:
            return 0
        items = [
            QueueItem(
                series_name=str(item.get("series_name") or ""),
                season=int(item.get("season") or 0),
                episode=int(item.get("episode") or 0),
                label=str(item.get("label") or ""),
                strm_path=str(item.get("strm_path") or ""),
            )
            for item in pending
            if str(item.get("series_name") or "")
            and int(item.get("season") or 0) > 0
            and int(item.get("episode") or 0) > 0
        ]
        added = self._queue_episode_items(items)
        if added:
            self._log(f"Accodati {added} episodi da serie incomplete (post-sync/scan)")
            with self._lock:
                self._status.last_action = f"Coda +{added} episodi (serie incomplete)"
            self._persist_status()
        return added

    def _episode_path(self, media: MediaServerClient, user_id: str, ep: dict) -> str:
        path = str(ep.get("Path", "")).strip()
        if path:
            return path
        item_id = ep.get("Id")
        if not item_id:
            return ""
        detail = media.get_item(user_id, str(item_id), "Path")
        if not detail:
            return ""
        return str(detail.get("Path", "")).strip()

    def _resolve_xtream_match(
        self,
        series_names: list[str],
        strm_path: str,
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
        season_i: int,
        episode_i: int,
        *,
        allow_4k: bool = False,
    ) -> dict | None:
        match = resolve_episode_from_strm_path(strm_path, xtream_host)
        if match:
            return match
        for series_name in series_names:
            found = find_xtream_episode(
                xtream_host, xtream_user, xtream_pw,
                series_name, season_i, episode_i,
                allow_4k=allow_4k,
            )
            if found:
                return found
        return None

    def _queue_episode_items(self, items: list[QueueItem]) -> int:
        added = 0
        with self._lock:
            for item in items:
                key = f"{item.series_name}:{item.season}:{item.episode}"
                if key in self._queued_keys:
                    continue
                self._queue.append(item)
                self._queued_keys.add(key)
                added += 1
        return added

    def _enqueue_from_xtream_catalog(
        self,
        ended: PlayingEpisode,
        dest_root: str,
        series_names: list[str],
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
        *,
        allow_4k: bool = False,
    ) -> int:
        seen: set[tuple[int, int]] = set()
        candidates: list[QueueItem] = []
        for series_name in series_names:
            for ep in find_subsequent_xtream_episodes(
                xtream_host, xtream_user, xtream_pw,
                series_name, int(ended.season), int(ended.episode),
                allow_4k=allow_4k,
            ):
                key = (ep["season"], ep["episode"])
                if key in seen:
                    continue
                seen.add(key)
                _folder, output_file = build_episode_output(
                    ended.series_name, ep["season"], ep["episode"], ep["ext"], dest_root,
                )
                if os.path.exists(output_file):
                    continue
                candidates.append(
                    QueueItem(
                        series_name=ended.series_name,
                        season=ep["season"],
                        episode=ep["episode"],
                        label=f"{ended.series_name} S{ep['season']:02d}E{ep['episode']:02d}",
                    )
                )
        return self._queue_episode_items(candidates)

    def _enqueue_subsequent(
        self,
        media: MediaServerClient,
        user_id: str,
        ended: PlayingEpisode,
        dest_root: str,
        xtream_host: str,
        xtream_user: str,
        xtream_pw: str,
        *,
        allow_4k: bool = False,
    ) -> None:
        series_names = [ended.series_name]
        library_series_name = media.get_series_name(user_id, ended.series_id)
        if library_series_name and library_series_name not in series_names:
            series_names.append(library_series_name)

        episodes = media.get_series_episodes(user_id, ended.series_id)
        candidates: list[QueueItem] = []
        stats = {
            "after_current": 0,
            "not_strm": 0,
            "no_xtream": 0,
            "exists": 0,
        }
        for ep in episodes:
            season = ep.get("ParentIndexNumber")
            episode = ep.get("IndexNumber")
            if season is None or episode is None:
                continue
            season_i = int(season)
            episode_i = int(episode)
            if (season_i, episode_i) <= (int(ended.season), int(ended.episode)):
                continue
            stats["after_current"] += 1

            path = self._episode_path(media, user_id, ep)
            if not path.lower().endswith(".strm"):
                stats["not_strm"] += 1
                continue

            xtream_match = self._resolve_xtream_match(
                series_names, path, xtream_host, xtream_user, xtream_pw, season_i, episode_i,
                allow_4k=allow_4k,
            )
            if not xtream_match:
                stats["no_xtream"] += 1
                continue

            _folder, output_file = build_episode_output(
                ended.series_name, season_i, episode_i, xtream_match["ext"], dest_root,
                strm_path=path,
            )
            if os.path.exists(output_file):
                stats["exists"] += 1
                continue

            candidates.append(
                QueueItem(
                    series_name=ended.series_name,
                    season=season_i,
                    episode=episode_i,
                    label=f"{ended.series_name} S{season_i:02d}E{episode_i:02d}",
                    strm_path=path,
                )
            )

        candidates.sort(key=lambda x: (x.season, x.episode))
        added = self._queue_episode_items(candidates)

        if added == 0:
            added = self._enqueue_from_xtream_catalog(
                ended, dest_root, series_names, xtream_host, xtream_user, xtream_pw,
                allow_4k=allow_4k,
            )
            if added:
                self._log(f"In coda {added} episodio/i da catalogo Xtream")
                with self._lock:
                    self._status.last_action = f"Aggiunti {added} episodi in coda (Xtream)"
                return

        if added:
            self._log(f"In coda {added} episodio/i successivi")
            with self._lock:
                self._status.last_action = f"Aggiunti {added} episodi in coda"
            return

        details = []
        if stats["after_current"]:
            details.append(f"{stats['after_current']} in library")
        if stats["not_strm"]:
            details.append(f"{stats['not_strm']} senza .strm")
        if stats["no_xtream"]:
            details.append(f"{stats['no_xtream']} non su Xtream")
        if stats["exists"]:
            details.append(f"{stats['exists']} già scaricati")
        message = "Nessun episodio successivo da scaricare"
        if details:
            message += f" ({', '.join(details)})"
        self._log(message)
        with self._lock:
            self._status.last_action = "Nessun episodio da scaricare"

    def _log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message}"
        with self._lock:
            self._status.log.append(line)
            if len(self._status.log) > self.MAX_LOG_LINES:
                self._status.log = self._status.log[-self.MAX_LOG_LINES :]
        self._persist_status()

    @staticmethod
    def _format_playing(playing: PlayingItem | None) -> str:
        if not playing:
            return ""
        suffix = " (strm)" if playing.blocks_download else " (locale)"
        return f"{playing.display_label()}{suffix}"


_watcher_singleton: AutoDownloadWatcher | None = None
_watcher_lock = threading.Lock()


def get_watcher() -> AutoDownloadWatcher:
    global _watcher_singleton
    with _watcher_lock:
        if _watcher_singleton is None:
            _watcher_singleton = AutoDownloadWatcher()
        return _watcher_singleton

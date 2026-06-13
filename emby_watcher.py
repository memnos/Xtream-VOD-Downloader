import os
import threading
import time
from dataclasses import dataclass, field

import requests

from core import (
    DownloadCancelled,
    append_playback_history,
    build_episode_output,
    find_subsequent_xtream_episodes,
    find_xtream_episode,
    load_auto_download_config,
    load_credentials,
    playback_blocks_xtream_download,
    prepare_output_dir,
    resolve_episode_from_strm_path,
    run_ytdlp,
    save_watcher_status,
)
from deletion import (
    add_deletion_prompt,
    find_series_download_paths,
    should_prompt_series_deletion,
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


@dataclass
class QueueItem:
    series_name: str
    season: int
    episode: int
    label: str
    strm_path: str = ""


@dataclass
class PausedDownload:
    item: QueueItem
    dest_root: str
    xtream_host: str
    xtream_user: str
    xtream_pw: str
    output_file: str
    url: str


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
        return data if isinstance(data, list) else []

    def get_series_episodes(self, user_id: str, series_id: str, include_user_data: bool = False) -> list:
        fields = "Path,ParentIndexNumber,IndexNumber,SeriesName,Id"
        if include_user_data:
            fields += ",UserData"
        data = self._get(
            f"/emby/Shows/{series_id}/Episodes",
            {"UserId": user_id, "Fields": fields},
        )
        return data if isinstance(data, list) else []

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
        with self._lock:
            self._status.enabled = bool(config.get("enabled"))
            if not self._status.enabled:
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
            self._log("Watcher avviato")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            config = load_auto_download_config()
            if not config.get("enabled"):
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
        creds = load_credentials()
        host = creds.get("host", "")
        xtream_user = creds.get("user", "")
        xtream_pw = creds.get("password", "")
        if not host or not xtream_user or not xtream_pw:
            return

        sessions = self._build_media_sessions(config)
        if not sessions:
            return

        playing, active_session = self._find_active_playback(sessions, host)
        if playing and active_session:
            if self._watching_item and self._watching_item.key != playing.key:
                self._record_playback(self._watching_item)
            if playing.blocks_download:
                if not self._xtream_stream_active:
                    self._log(f"Riproduzione strm ({playing.display_label()}): download in pausa")
                self._xtream_stream_active = True
                self._active_proc_cancel.set()
            else:
                if self._xtream_stream_active:
                    self._log("Riproduzione da file locale: download consentito")
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
            self._record_playback(ended)

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
                )
                self._maybe_prompt_series_deletion(
                    ended_session.client, ended_session.user_id, ended, config,
                )

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
        prepared = self._prepare_download(item, dest_root, host, xtream_user, xtream_pw)
        if prepared:
            self._start_download_thread(prepared)

    def _start_download_thread(self, paused: PausedDownload) -> None:
        self._download_thread = threading.Thread(
            target=self._run_download,
            args=(paused,),
            daemon=True,
        )
        self._download_thread.start()

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
    ) -> PausedDownload | None:
        match = None
        if item.strm_path:
            match = resolve_episode_from_strm_path(item.strm_path, xtream_host)
        if not match:
            match = find_xtream_episode(
                xtream_host, xtream_user, xtream_pw,
                item.series_name, item.season, item.episode,
            )
        if not match:
            self._log(f"Episodio non trovato su Xtream: {item.label}")
            with self._lock:
                self._queued_keys.discard(f"{item.series_name}:{item.season}:{item.episode}")
            return None

        _folder, output_file = build_episode_output(
            item.series_name, item.season, item.episode, match["ext"], dest_root,
        )
        if os.path.exists(output_file):
            self._log(f"Già presente, salto: {item.label}")
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
        key = f"{job.item.series_name}:{job.item.season}:{job.item.episode}"
        resume = os.path.exists(job.output_file) and os.path.getsize(job.output_file) > 0

        with self._lock:
            self._status.downloading = True
            self._status.current_download = job.item.label
            self._status.download_progress = 0.0
            self._status.download_progress_text = "0%"
            self._active_proc_cancel.clear()
            self._download_context = {"job": job, "key": key}
        self._persist_status()

        try:
            if self._xtream_stream_active:
                self._pause_download(job)
                return

            folder = os.path.dirname(job.output_file)
            prepare_output_dir(folder)
            if resume:
                self._log(f"Ripresa download: {job.item.label}")
            else:
                self._log(f"Download avviato: {job.item.label}")

            def should_cancel() -> bool:
                return self._active_proc_cancel.is_set() or self._xtream_stream_active

            def on_progress(value: float, text: str) -> None:
                with self._lock:
                    self._status.download_progress = min(max(value, 0.0), 1.0)
                    self._status.download_progress_text = text
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
                history_entry={
                    "key": key,
                    "type": "Series",
                    "title": job.item.label,
                    "mode": "automatic",
                },
            )
            self._log(f"Download completato: {job.item.label}")
            with self._lock:
                self._status.last_action = f"Completato {job.item.label}"
                self._status.last_error = ""
                self._queued_keys.discard(key)
        except DownloadCancelled:
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
            self._persist_status()

    def _pause_download(self, job: PausedDownload) -> None:
        with self._lock:
            self._paused_download = job
            self._status.current_download = job.item.label
        self._log(f"Download in pausa: {job.item.label}")
        with self._lock:
            self._status.last_action = f"In pausa {job.item.label}"
        self._persist_status()

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
        if add_deletion_prompt(ended.series_id, ended.series_name, paths):
            self._log(f"Serie completata: in attesa conferma eliminazione per {ended.series_name}")
            with self._lock:
                self._status.last_action = f"Serie completata: {ended.series_name}"

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
    ) -> dict | None:
        match = resolve_episode_from_strm_path(strm_path, xtream_host)
        if match:
            return match
        for series_name in series_names:
            found = find_xtream_episode(
                xtream_host, xtream_user, xtream_pw,
                series_name, season_i, episode_i,
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
    ) -> int:
        seen: set[tuple[int, int]] = set()
        candidates: list[QueueItem] = []
        for series_name in series_names:
            for ep in find_subsequent_xtream_episodes(
                xtream_host, xtream_user, xtream_pw,
                series_name, int(ended.season), int(ended.episode),
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
            )
            if not xtream_match:
                stats["no_xtream"] += 1
                continue

            _folder, output_file = build_episode_output(
                ended.series_name, season_i, episode_i, xtream_match["ext"], dest_root,
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

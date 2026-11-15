"""Silo media-server client for automatic-download monitoring.

Silo's native API is not the Emby/Jellyfin one. Playback is read from
GET /api/v2/admin/sessions (admin API key, Bearer sa_…) and shaped like an
Emby session so the existing watcher can treat Silo as a third server.
"""
from __future__ import annotations

import threading
import uuid
from urllib.parse import quote

import requests

_TICKS_PER_SECOND = 10_000_000
_API = "/api/v2"


def _seconds_to_ticks(seconds: object) -> int:
    try:
        return max(0, int(float(seconds or 0) * _TICKS_PER_SECOND))
    except (TypeError, ValueError):
        return 0


def _as_items(data: object) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items")
        return items if isinstance(items, list) else []
    return []


class SiloClient:
    """Read Silo playback and catalog the way the Emby watcher expects."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.server_type = "silo"
        self._user_id_cache: str | None = None
        self._profile_id_cache: str | None = None
        self._watch_cache: dict[str, dict] = {}
        self._path_cache: dict[str, str] = {}
        self._seq = 0
        self._lock = threading.Lock()

    @property
    def display_name(self) -> str:
        return "Silo"

    def _headers(self) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Silo-Client": "XtreamDownloader",
            "X-Silo-Client-Version": "1.0.0",
        }
        profile_id = self._primary_profile_id()
        if profile_id:
            headers["X-Profile-Id"] = profile_id
        return headers

    def _primary_profile_id(self) -> str:
        """Catalog endpoints require X-Profile-Id; admin sessions accept it when primary."""
        if self._profile_id_cache:
            return self._profile_id_cache
        try:
            data = self._request(
                "GET",
                f"{_API}/profiles",
                params=None,
                json_body=None,
                include_profile=False,
            )
        except requests.RequestException:
            return ""
        primary = ""
        fallback = ""
        for row in _as_items(data):
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip()
            if not pid:
                continue
            if not fallback:
                fallback = pid
            if row.get("is_primary"):
                primary = pid
                break
        self._profile_id_cache = primary or fallback
        return self._profile_id_cache

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return self.base_url + path

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        include_profile: bool = True,
    ) -> object:
        headers = self._headers() if include_profile else {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
            "X-Silo-Client": "XtreamDownloader",
            "X-Silo-Client-Version": "1.0.0",
        }
        response = requests.request(
            method,
            self._url(path),
            headers=headers,
            params=params,
            json=json_body,
            timeout=20,
        )
        if response.status_code == 409 and method.upper() == "POST":
            latest = response.headers.get("X-Silo-Latest-Sequence") or ""
            raise requests.HTTPError(
                f"Silo command conflict (latest sequence {latest or '?'})",
                response=response,
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            detail = ""
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                detail = str(body.get("detail") or body.get("title") or "").strip()
            if detail:
                raise requests.HTTPError(f"{exc} — {detail}", response=response) from exc
            raise
        if not response.content:
            return {}
        return response.json()

    def _get(self, path: str, params: dict | None = None) -> object:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body: dict) -> object:
        return self._request("POST", path, json_body=json_body)

    def _collect(
        self,
        path: str,
        params: dict | None = None,
        *,
        max_pages: int = 5,
        default_limit: int | None = 200,
    ) -> list:
        query = dict(params or {})
        # Some catalog routes (series seasons/episodes) reject a limit query param.
        if default_limit is not None:
            query.setdefault("limit", default_limit)
        items: list = []
        for _ in range(max_pages):
            data = self._get(path, query)
            batch = _as_items(data)
            items.extend(batch)
            page = data.get("page") if isinstance(data, dict) else None
            cursor = str((page or {}).get("next_cursor") or "")
            if not isinstance(page, dict) or not page.get("has_more") or not cursor:
                break
            query["cursor"] = cursor
        return items

    @staticmethod
    def _quote_id(value: str) -> str:
        return quote(str(value or ""), safe="")

    def _watch(self, content_id: str) -> dict:
        key = str(content_id or "").strip()
        if not key:
            return {}
        cached = self._watch_cache.get(key)
        if cached is not None:
            return cached
        try:
            data = self._get(f"{_API}/watch/{self._quote_id(key)}")
        except requests.RequestException:
            data = {}
        detail = data if isinstance(data, dict) else {}
        self._watch_cache[key] = detail
        return detail

    def _file_path(self, content_id: str, *, file_id: str = "") -> str:
        key = str(content_id or "").strip()
        if not key:
            return ""
        cache_key = f"{key}:{file_id}"
        if cache_key in self._path_cache:
            return self._path_cache[cache_key]
        watch = self._watch(key)
        path = self._path_from_versions(watch.get("versions") or [], file_id)
        if not path:
            try:
                data = self._get(f"{_API}/catalog/items/{self._quote_id(key)}/versions")
            except requests.RequestException:
                data = {}
            path = self._path_from_versions(_as_items(data), file_id)
        self._path_cache[cache_key] = path
        return path

    @staticmethod
    def _path_from_versions(versions: list, file_id: str) -> str:
        wanted = str(file_id or "")
        first = ""
        strm = ""
        for version in versions:
            if not isinstance(version, dict):
                continue
            path = str(version.get("file_path") or "").strip()
            if not path:
                continue
            if wanted and str(version.get("file_id") or "") == wanted:
                return path
            if not first:
                first = path
            if path.lower().endswith(".strm") and not strm:
                strm = path
        return strm or first

    def list_users(self) -> list[dict]:
        return [
            item
            for item in self._collect(f"{_API}/admin/users")
            if isinstance(item, dict)
        ]

    def resolve_user_id(self, username: str) -> str | None:
        if self._user_id_cache:
            return self._user_id_cache
        target = (username or "").strip().lower()
        if not target:
            return None
        for user in self.list_users():
            if str(user.get("username") or "").strip().lower() == target:
                user_id = str(user.get("id") or "").strip()
                if user_id:
                    self._user_id_cache = user_id
                    return user_id
        return None

    def test_connection(self, username: str) -> tuple[bool, str]:
        try:
            me = self._get(f"{_API}/account/me")
            if not isinstance(me, dict) or not me.get("username"):
                return False, "Unexpected API response for /api/v2/account/me"
            users = self.list_users()
            user_id = self.resolve_user_id(username)
            if not user_id:
                names = ", ".join(
                    str(user.get("username") or "") for user in users if user.get("username")
                )
                hint = f" Available users: {names}" if names else ""
                return False, f"User '{username}' not found.{hint}"
            sessions = self.get_sessions()
            return True, f"Connected — user '{username}' OK, {len(sessions)} active session(s)"
        except requests.ConnectionError:
            return False, "Connection refused — check URL and that Silo is reachable"
        except requests.Timeout:
            return False, "Connection timed out"
        except requests.HTTPError as exc:
            response = exc.response
            status = response.status_code if response is not None else 0
            if status == 401:
                return False, "Invalid API key (HTTP 401)"
            if status == 403:
                return False, "API key is not an administrator (HTTP 403) — Silo monitoring needs an admin key"
            if status in {502, 503, 504}:
                return False, f"Server temporarily unavailable (HTTP {status})"
            return False, str(exc)
        except Exception as exc:
            return False, str(exc)

    def get_sessions(self) -> list:
        rows = self._collect(f"{_API}/admin/sessions")
        rows.sort(key=lambda row: str((row or {}).get("updated_at") or ""), reverse=True)
        sessions: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            shaped = self._shape_session(row)
            if shaped:
                sessions.append(shaped)
        return sessions

    @staticmethod
    def _has_episode_numbers(season: object, episode: object) -> bool:
        return season not in (None, "") and episode not in (None, "")

    @classmethod
    def _resolve_playback_kind(
        cls,
        media_type: str,
        content_id: str,
        *,
        season: object = None,
        episode: object = None,
        watch: dict | None = None,
    ) -> str | None:
        """Map Silo session media_type to movie/episode.

        Live admin sessions join ``media_items`` on the series ``content_id``, so
        episode playback often arrives as ``media_type="series"``. Treat those as
        episodes when the row (or watch payload) carries episode identity.
        """
        mt = (media_type or "").strip().lower()
        cid = (content_id or "").strip().lower()
        watch = watch or {}
        if mt == "movie" or cid.startswith("movie-") or cid.startswith("movie:"):
            return "movie"
        if mt == "episode" or cid.startswith("episode-") or cid.startswith("episode:"):
            return "episode"
        if mt == "series":
            if cls._has_episode_numbers(season, episode):
                return "episode"
            if cls._has_episode_numbers(watch.get("season_number"), watch.get("episode_number")):
                return "episode"
            if str(watch.get("type") or "").strip().lower() == "episode":
                return "episode"
            if str(watch.get("series_id") or "").strip() and cid.startswith("episode"):
                return "episode"
        return None

    def _shape_session(self, row: dict) -> dict | None:
        content_id = str(row.get("content_id") or "").strip()
        session_id = str(row.get("session_id") or "").strip()
        if not content_id or not session_id:
            return None
        file_id = str(row.get("media_file_id") or "").strip()
        watch = self._watch(content_id)
        season = row.get("season_number")
        episode = row.get("episode_number")
        if season in (None, "") and watch.get("season_number") not in (None, ""):
            season = watch.get("season_number")
        if episode in (None, "") and watch.get("episode_number") not in (None, ""):
            episode = watch.get("episode_number")
        kind = self._resolve_playback_kind(
            str(row.get("media_type") or ""),
            content_id,
            season=season,
            episode=episode,
            watch=watch,
        )
        if kind is None:
            return None
        path = self._path_from_versions(watch.get("versions") or [], file_id)
        if not path:
            path = self._file_path(content_id, file_id=file_id)
        series_name = str(row.get("series_name") or watch.get("series_title") or "").strip()
        title = str(
            row.get("episode_name") or row.get("media_title") or watch.get("title") or series_name
        ).strip()
        series_id = str(watch.get("series_id") or "").strip()
        if kind == "episode" and not series_id and series_name:
            series_id = self._series_id_by_name(series_name)
        bitrate = 0
        try:
            kbps = int(row.get("source_bitrate_kbps") or 0)
        except (TypeError, ValueError):
            kbps = 0
        if kbps > 0:
            bitrate = kbps * 1000
        runtime = row.get("file_duration")
        if not runtime:
            for version in watch.get("versions") or []:
                if file_id and str(version.get("file_id") or "") == file_id:
                    runtime = version.get("duration_seconds")
                    if bitrate <= 0:
                        try:
                            bitrate = int(version.get("bitrate") or 0)
                        except (TypeError, ValueError):
                            bitrate = 0
                    break
        item_type = "Episode" if kind == "episode" else "Movie"
        item = {
            "Type": item_type,
            "Id": content_id,
            "Name": title,
            "Path": path,
            "RunTimeTicks": _seconds_to_ticks(runtime),
            "Bitrate": bitrate,
        }
        if item_type == "Episode":
            item["SeriesId"] = series_id
            item["SeriesName"] = series_name or title
            if season is not None and season != "":
                item["ParentIndexNumber"] = int(season)
            if episode is not None and episode != "":
                item["IndexNumber"] = int(episode)
        return {
            "Id": session_id,
            "UserId": str(row.get("user_id") or ""),
            "UserName": str(row.get("username") or ""),
            "NowPlayingItem": item,
            "PlayState": {
                "PositionTicks": _seconds_to_ticks(row.get("position_seconds")),
                "IsPaused": bool(row.get("is_paused")),
            },
        }

    def _series_id_by_name(self, series_name: str) -> str:
        target = series_name.strip().lower()
        for hit in self.search_series("", series_name, limit=8):
            if str(hit.get("Name") or "").strip().lower() == target:
                return str(hit.get("Id") or "")
        return ""

    def search_library(self, kind: str, search_term: str, limit: int = 20) -> list:
        """Movies or series in the Silo catalog. kind is movie or series."""
        term = (search_term or "").strip()
        if not term:
            return []
        media_type = "series" if kind == "series" else "movie"
        page_limit = max(1, min(int(limit or 20), 40))
        try:
            data = self._get(
                f"{_API}/catalog",
                {"type": media_type, "q": term, "limit": page_limit},
            )
        except requests.RequestException:
            return []
        found = []
        for row in _as_items(data):
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "") not in {media_type, ""}:
                continue
            content_id = str(row.get("content_id") or "").strip()
            title = str(row.get("title") or row.get("name") or "").strip()
            if not content_id or not title:
                continue
            year = row.get("year") or ""
            found.append({"Id": content_id, "Name": title, "Year": year})
        return found[:page_limit]

    def search_series(self, user_id: str, search_term: str, limit: int = 10) -> list:
        del user_id
        term = (search_term or "").strip()
        if not term:
            return []
        page_limit = max(1, min(int(limit or 10), 50))
        try:
            # Current Silo catalog browse (GET). Older /catalog/items?q= is gone.
            data = self._get(
                f"{_API}/catalog",
                {"type": "series", "q": term, "limit": page_limit},
            )
        except requests.RequestException:
            return []
        found = []
        for row in _as_items(data):
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "") not in {"series", ""}:
                continue
            content_id = str(row.get("content_id") or "").strip()
            title = str(row.get("title") or row.get("name") or "").strip()
            if content_id and title:
                found.append({"Id": content_id, "Name": title})
        return found[:page_limit]

    def get_series_name(self, user_id: str, series_id: str) -> str:
        del user_id
        item = self.get_item("", series_id, "Name")
        return str((item or {}).get("Name") or "").strip()

    def get_item(self, user_id: str, item_id: str, fields: str = "Path") -> dict | None:
        del user_id, fields
        content_id = str(item_id or "").strip()
        if not content_id:
            return None
        watch = self._watch(content_id)
        if watch:
            user_data = watch.get("user_data") or {}
            return {
                "Id": content_id,
                "Name": str(watch.get("series_title") or watch.get("title") or ""),
                "Path": self._file_path(content_id),
                "Type": "Episode" if watch.get("series_id") else "Movie",
                "SeriesId": str(watch.get("series_id") or ""),
                "SeriesName": str(watch.get("series_title") or ""),
                "ParentIndexNumber": watch.get("season_number"),
                "IndexNumber": watch.get("episode_number"),
                "RunTimeTicks": _seconds_to_ticks(
                    (user_data or {}).get("duration_seconds") or 0
                ),
                "UserData": {
                    "Played": bool(user_data.get("played")),
                    "PlaybackPositionTicks": _seconds_to_ticks(
                        user_data.get("position_seconds")
                    ),
                },
            }
        try:
            data = self._get(f"{_API}/catalog/items/{self._quote_id(content_id)}")
        except requests.RequestException:
            return None
        if not isinstance(data, dict) or not data.get("content_id"):
            return None
        return {
            "Id": str(data.get("content_id") or content_id),
            "Name": str(data.get("title") or ""),
            "Path": self._file_path(content_id),
            "Type": "Series" if str(data.get("type") or "") == "series" else "Movie",
        }

    def get_series_episodes(
        self,
        user_id: str,
        series_id: str,
        include_user_data: bool = False,
        *,
        resolve_paths: bool = True,
    ) -> list:
        """List catalog episodes for a series.

        Path resolution hits `/watch` per episode and can rate-limit Silo into
        dropping later seasons mid-fetch. Pass ``resolve_paths=False`` for
        watch-status / deletion scans that only need season + played flags.
        """
        del user_id
        series_id = str(series_id or "").strip()
        if not series_id:
            return []
        try:
            seasons = self._collect(
                f"{_API}/catalog/series/{self._quote_id(series_id)}/seasons",
                default_limit=None,
            )
        except requests.RequestException:
            return []
        # Collect every season's episode rows first so path lookups cannot
        # starve later seasons under API pressure.
        season_rows: list[tuple[int, list]] = []
        for season in seasons:
            if not isinstance(season, dict):
                continue
            try:
                number = int(season.get("season_number"))
            except (TypeError, ValueError):
                continue
            try:
                rows = self._collect(
                    f"{_API}/catalog/series/{self._quote_id(series_id)}/seasons/{number}/episodes",
                    default_limit=None,
                )
            except requests.RequestException:
                continue
            season_rows.append((number, rows if isinstance(rows, list) else []))

        series_name = self.get_series_name("", series_id) if resolve_paths else ""
        episodes: list[dict] = []
        for number, rows in season_rows:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                content_id = str(row.get("content_id") or "").strip()
                if not content_id:
                    continue
                user_data = row.get("user_data") or {}
                item = {
                    "Id": content_id,
                    "Name": str(row.get("title") or ""),
                    "Path": self._file_path(content_id) if resolve_paths else "",
                    "SeriesId": series_id,
                    "SeriesName": series_name,
                    "ParentIndexNumber": row.get("season_number", number),
                    "IndexNumber": row.get("episode_number"),
                }
                if include_user_data or user_data:
                    item["UserData"] = {"Played": bool(user_data.get("played"))}
                episodes.append(item)
        return episodes

    def notify_library_paths(self, updates: list[dict]) -> None:
        """Silo picks up new files on its own scan; nothing to push."""
        del updates

    def list_libraries(self) -> list[dict]:
        data = self._request(
            "GET",
            "/api/v1/libraries/",
            params=None,
            json_body=None,
            include_profile=False,
        )
        rows = data if isinstance(data, list) else []
        if isinstance(data, dict):
            nested = data.get("items") or data.get("libraries") or []
            rows = nested if isinstance(nested, list) else []
        return [row for row in rows if isinstance(row, dict)]

    def request_scan(self, path: str) -> None:
        """Ask Silo to scan one library file or folder (POST /api/v1/scan)."""
        target = (path or "").strip()
        if not target:
            raise ValueError("path is required")
        self._request(
            "POST",
            "/api/v1/scan",
            params=None,
            json_body={"path": target},
            include_profile=False,
        )

    def find_item_by_path(self, path: str) -> dict | None:
        del path
        return None

    def display_message(
        self,
        session_id: str,
        *,
        header: str,
        text: str,
        timeout_ms: int | None = 25000,
    ) -> None:
        del timeout_ms
        sid = (session_id or "").strip()
        message = (text or "").strip()
        if not sid or not message:
            raise ValueError("session_id and text are required")
        body = self._command_body()
        body["title"] = (header or "Xtream").strip()[:256]
        body["message"] = message[:2048]
        self._post(f"{_API}/admin/sessions/{self._quote_id(sid)}/message", body)

    def stop_playback(self, session_id: str) -> None:
        """Silo has no remote PlayNow. Stopping here would cut playback without a replacement."""
        del session_id
        raise RuntimeError("Silo non può cambiare l'elemento in riproduzione da remoto")

    def play_item(
        self,
        session_id: str,
        item_id: str,
        *,
        start_position_ticks: int = 0,
    ) -> None:
        del session_id, item_id, start_position_ticks
        raise RuntimeError("Silo non può cambiare l'elemento in riproduzione da remoto")

    def _command_body(self) -> dict:
        with self._lock:
            self._seq += 1
            sequence = self._seq
        return {
            "command_id": str(uuid.uuid4()),
            "sequence": sequence,
        }

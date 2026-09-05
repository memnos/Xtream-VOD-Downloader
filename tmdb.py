"""Lightweight TMDB client with JSON cache for .strm naming.

The cache lives in .data/tmdb_cache.json and is keyed by media type + cleaned
title (+ year for movies), so it is independent of the output folder. This means
you can generate into a test directory and later re-run into the working
directory without paying the TMDB lookup cost again.

Positive title matches are kept indefinitely. TV season episode counts are
also cached, but a stale count would treat newly aired episodes as phantoms:
if validation fails and the season cache is older than
SEASON_COUNTS_TTL_SECONDS, TMDB is queried again. Negative (no-match) results
are cached so repeated syncs do not re-query TMDB for the same miss; they
expire after NEGATIVE_CACHE_TTL_SECONDS and are retried then.
"""

from __future__ import annotations

import os
import re
import threading
import time

import requests

from core import DATA_DIR, _save_json_file, load_json_file

TMDB_CACHE_FILE = os.environ.get(
    "TMDB_CACHE_FILE", os.path.join(DATA_DIR, "tmdb_cache.json")
)
BASE_URL = "https://api.themoviedb.org/3"
# Retry unmatched titles after a week (new TMDB entries, title cleanups, etc.).
NEGATIVE_CACHE_TTL_SECONDS = int(
    os.environ.get("TMDB_NEGATIVE_CACHE_TTL_SECONDS", str(7 * 24 * 3600))
)
# Refresh season episode_count when an episode would be rejected as a phantom.
SEASON_COUNTS_TTL_SECONDS = int(
    os.environ.get("TMDB_SEASON_COUNTS_TTL_SECONDS", str(12 * 3600))
)

YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
COUNTRY_PREFIX_RE = re.compile(r"^\s*\|[^|]*\|\s*")
BRACKET_TAG_RE = re.compile(r"[\[\(\{][^\]\)\}]*[\]\)\}]")
QUALITY_RE = re.compile(
    r"\b(?:4k|uhd|2160p?|1440p?|1080p?|720p?|480p?|fhd|hd|sd|hevc|h\.?265|h\.?264|"
    r"x265|x264|10bit|8bit|hdr10?|hdr|dolby(?:\s?vision)?|dv|atmos|ddp?5\.?1|aac|"
    r"remux|bluray|blu-ray|bdrip|webrip|web-?dl|dvdrip|hdtv)\b",
    re.IGNORECASE,
)
LANG_RE = re.compile(
    r"\b(?:ita|eng|sub(?:ita)?|multi|vose|vo|lat|esp|spa|fra|fre|ger|deu|por|rus)\b",
    re.IGNORECASE,
)
SEASON_EP_RE = re.compile(
    r"\b(?:s\d{1,2}(?:\s?e\d{1,3})?|season\s*\d{1,2}|stagione\s*\d{1,2})\b",
    re.IGNORECASE,
)
SEPARATOR_RE = re.compile(r"[._]+")
SPACE_RE = re.compile(r"\s+")


def extract_year(title: str) -> int | None:
    matches = YEAR_RE.findall(title or "")
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def clean_title(title: str) -> str:
    """Strip country/quality/language tags so TMDB search has a clean query."""
    text = title or ""
    text = COUNTRY_PREFIX_RE.sub("", text)
    text = SEPARATOR_RE.sub(" ", text)
    text = BRACKET_TAG_RE.sub(" ", text)
    text = SEASON_EP_RE.sub(" ", text)
    text = QUALITY_RE.sub(" ", text)
    text = LANG_RE.sub(" ", text)
    text = YEAR_RE.sub(" ", text)
    text = re.sub(r"[-–—:]+\s*$", "", text)
    text = SPACE_RE.sub(" ", text).strip(" -–—:·|")
    return text.strip()


def _cache_key(media: str, cleaned: str, year: int | None) -> str:
    base = cleaned.lower()
    if media == "movie" and year:
        return f"movie:{base}:{year}"
    return f"{media}:{base}"


class TmdbClient:
    def __init__(
        self,
        api_key: str,
        *,
        language: str = "it-IT",
        rate_limit: int = 40,
        window_seconds: float = 10.0,
        negative_ttl_seconds: int | None = None,
        season_counts_ttl_seconds: int | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.language = language or "it-IT"
        self.rate_limit = max(1, int(rate_limit))
        self.window_seconds = float(window_seconds)
        self.negative_ttl_seconds = (
            NEGATIVE_CACHE_TTL_SECONDS
            if negative_ttl_seconds is None
            else max(0, int(negative_ttl_seconds))
        )
        self.season_counts_ttl_seconds = (
            SEASON_COUNTS_TTL_SECONDS
            if season_counts_ttl_seconds is None
            else max(0, int(season_counts_ttl_seconds))
        )
        self._request_times: list[float] = []
        self._rate_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._dirty = False
        self.lookups = 0
        self.cache_hits = 0
        self.load_cache()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def load_cache(self) -> None:
        data = load_json_file(TMDB_CACHE_FILE, {})
        with self._cache_lock:
            self._cache = data if isinstance(data, dict) else {}

    def save_cache(self) -> None:
        with self._cache_lock:
            if not self._dirty:
                return
            snapshot = dict(self._cache)
            self._dirty = False
        _save_json_file(TMDB_CACHE_FILE, snapshot)

    def purge_negative_cache(self) -> int:
        """Remove all negative (no-match) cache entries. Returns count removed."""
        with self._cache_lock:
            stale = [key for key, entry in self._cache.items() if not entry.get("matched")]
            for key in stale:
                self._cache.pop(key, None)
            if stale:
                self._dirty = True
        return len(stale)

    def purge_expired_negative_cache(self) -> int:
        """Remove negative entries older than the TTL. Returns count removed."""
        ttl = self.negative_ttl_seconds
        now = time.time()
        with self._cache_lock:
            stale = [
                key
                for key, entry in self._cache.items()
                if not entry.get("matched")
                and (now - float(entry.get("cached_at") or 0)) >= ttl
            ]
            for key in stale:
                self._cache.pop(key, None)
            if stale:
                self._dirty = True
        return len(stale)

    def _wait_for_slot(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            self._request_times = [t for t in self._request_times if t > cutoff]
            if len(self._request_times) >= self.rate_limit:
                sleep_for = self._request_times[0] + self.window_seconds - now
                if sleep_for > 0:
                    time.sleep(sleep_for)
                now = time.monotonic()
                cutoff = now - self.window_seconds
                self._request_times = [t for t in self._request_times if t > cutoff]
            self._request_times.append(time.monotonic())

    def _get(self, path: str, params: dict) -> dict | None:
        if not self.configured:
            return None
        query = {"api_key": self.api_key, "language": self.language}
        query.update(params)
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            self._wait_for_slot()
            try:
                resp = requests.get(url, params=query, timeout=20)
                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", "1"))
                    time.sleep(min(retry_after, 10) + 0.5)
                    continue
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError):
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        return None

    def _cached(self, key: str) -> dict | None:
        with self._cache_lock:
            entry = self._cache.get(key)
        if not entry:
            return None
        if entry.get("matched"):
            self.cache_hits += 1
            return entry
        # Negative cache: honor TTL so misses can be retried later.
        cached_at = float(entry.get("cached_at") or 0)
        age = time.time() - cached_at
        if cached_at and age < self.negative_ttl_seconds:
            self.cache_hits += 1
            return entry
        return None

    def _store(self, key: str, entry: dict) -> None:
        entry["cached_at"] = time.time()
        with self._cache_lock:
            self._cache[key] = entry
            self._dirty = True

    def _store_negative(self, key: str) -> None:
        self._store(key, {"matched": False})

    def search_movie_results(
        self,
        raw_title: str,
        *,
        year: int | None = None,
        max_results: int = 10,
        use_year_filter: bool = True,
    ) -> list[dict] | None:
        """Return TMDB movie hits, or None if the request failed."""
        cleaned = clean_title(raw_title)
        if not cleaned:
            return []
        if year is None:
            year = extract_year(raw_title)
        self.lookups += 1
        params: dict = {"query": cleaned, "include_adult": True}
        if use_year_filter and year:
            params["year"] = year
        payload = self._get("/search/movie", params)
        if payload is None:
            return None
        out: list[dict] = []
        seen: set[int] = set()
        for result in payload.get("results") or []:
            if not isinstance(result, dict):
                continue
            try:
                tid = int(result.get("id"))
            except (TypeError, ValueError):
                continue
            if tid <= 0 or tid in seen:
                continue
            seen.add(tid)
            out.append(
                {
                    "tmdb_id": tid,
                    "title": result.get("title") or result.get("original_title") or cleaned,
                    "original_title": result.get("original_title") or "",
                    "year": self._year_from(result.get("release_date")),
                    "adult": bool(result.get("adult")),
                    "vote_average": float(result.get("vote_average") or 0),
                    "vote_count": int(result.get("vote_count") or 0),
                }
            )
            if len(out) >= max(1, int(max_results)):
                break
        return out

    def search_movie(self, raw_title: str) -> dict | None:
        cleaned = clean_title(raw_title)
        if not cleaned:
            return None
        year = extract_year(raw_title)
        key = _cache_key("movie", cleaned, year)
        cached = self._cached(key)
        if cached is not None:
            return cached if cached.get("matched") else None

        results = self.search_movie_results(
            raw_title, year=year, max_results=5, use_year_filter=True
        )
        if results is None:
            # Request failed (network/timeout); do not poison cache with a false negative.
            return None
        if not results:
            self._store_negative(key)
            return None
        result = results[0]
        if year:
            for row in results:
                if row.get("year") == year:
                    result = row
                    break
        entry = {
            "matched": True,
            "tmdb_id": result.get("tmdb_id"),
            "title": result.get("title") or cleaned,
            "year": result.get("year"),
            "adult": bool(result.get("adult")),
            "vote_average": float(result.get("vote_average") or 0),
            "vote_count": int(result.get("vote_count") or 0),
        }
        self._store(key, entry)
        return entry

    def search_series(self, raw_name: str) -> dict | None:
        cleaned = clean_title(raw_name)
        if not cleaned:
            return None
        year = extract_year(raw_name)
        key = _cache_key("tv", cleaned, None)
        cached = self._cached(key)
        if cached is not None:
            return cached if cached.get("matched") else None

        self.lookups += 1
        params = {"query": cleaned, "include_adult": True}
        if year:
            params["first_air_date_year"] = year
        payload = self._get("/search/tv", params)
        if payload is None:
            return None
        result = self._best_result(payload, year, date_key="first_air_date")
        if not result:
            self._store_negative(key)
            return None
        entry = {
            "matched": True,
            "tmdb_id": result.get("id"),
            "title": result.get("name") or result.get("original_name") or cleaned,
            "year": self._year_from(result.get("first_air_date")),
            "adult": bool(result.get("adult")),
        }
        self._store(key, entry)
        return entry

    @staticmethod
    def _season_counts_from_entry(entry: dict | None) -> dict[int, int] | None:
        if not entry or not entry.get("matched"):
            return None
        raw = entry.get("seasons") or {}
        out: dict[int, int] = {}
        for season_key, count in raw.items():
            try:
                season_num = int(season_key)
                episode_count = int(count)
            except (TypeError, ValueError):
                continue
            if season_num >= 0 and episode_count > 0:
                out[season_num] = episode_count
        return out or None

    def _tv_seasons_cache_age(self, tmdb_id: int) -> float | None:
        key = f"tv_seasons:{int(tmdb_id)}"
        with self._cache_lock:
            entry = self._cache.get(key)
        if not entry:
            return None
        cached_at = float(entry.get("cached_at") or 0)
        if not cached_at:
            return None
        return time.time() - cached_at

    def get_tv_season_episode_counts(
        self, tmdb_id: int, *, force_refresh: bool = False
    ) -> dict[int, int] | None:
        """Return {season_number: episode_count} for a TMDB TV series (cached)."""
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        if tid <= 0:
            return None
        key = f"tv_seasons:{tid}"
        if not force_refresh:
            cached = self._cached(key)
            if cached is not None:
                if not cached.get("matched"):
                    return None
                return self._season_counts_from_entry(cached)

        self.lookups += 1
        payload = self._get(f"/tv/{tid}", {})
        if payload is None:
            return None
        seasons = payload.get("seasons")
        if not isinstance(seasons, list) or not seasons:
            self._store_negative(key)
            return None
        counts: dict[int, int] = {}
        for season in seasons:
            if not isinstance(season, dict):
                continue
            try:
                season_num = int(season.get("season_number"))
                episode_count = int(season.get("episode_count") or 0)
            except (TypeError, ValueError):
                continue
            if season_num >= 0 and episode_count > 0:
                counts[season_num] = episode_count
        if not counts:
            self._store_negative(key)
            return None
        self._store(
            key,
            {
                "matched": True,
                "tmdb_id": tid,
                "seasons": {str(num): count for num, count in sorted(counts.items())},
            },
        )
        return counts

    def get_movie_runtime(self, tmdb_id: int | str | None) -> int | None:
        """Return TMDB movie runtime in minutes (cached). None if unavailable."""
        try:
            tid = int(tmdb_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if tid <= 0:
            return None
        key = f"movie_runtime:{tid}"
        cached = self._cached(key)
        if cached is not None:
            if not cached.get("matched"):
                return None
            try:
                runtime = int(cached.get("runtime") or 0)
            except (TypeError, ValueError):
                return None
            return runtime if runtime > 0 else None

        self.lookups += 1
        payload = self._get(f"/movie/{tid}", {})
        if payload is None:
            return None
        try:
            runtime = int(payload.get("runtime") or 0)
        except (TypeError, ValueError):
            runtime = 0
        if runtime <= 0:
            self._store_negative(key)
            return None
        self._store(
            key,
            {
                "matched": True,
                "tmdb_id": tid,
                "runtime": runtime,
                "title": payload.get("title") or payload.get("original_title") or "",
            },
        )
        return runtime

    def get_movie_vote(self, tmdb_id: int | str | None) -> tuple[float, int] | None:
        """Return (vote_average, vote_count) from TMDB, cached. None if unavailable."""
        try:
            tid = int(tmdb_id)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if tid <= 0:
            return None
        key = f"movie_vote:{tid}"
        cached = self._cached(key)
        if cached is not None:
            if not cached.get("matched"):
                return None
            try:
                average = float(cached.get("vote_average") or 0)
                count = int(cached.get("vote_count") or 0)
            except (TypeError, ValueError):
                return None
            return average, count

        self.lookups += 1
        payload = self._get(f"/movie/{tid}", {})
        if payload is None:
            return None
        try:
            average = float(payload.get("vote_average") or 0)
            count = int(payload.get("vote_count") or 0)
        except (TypeError, ValueError):
            return None
        self._store(
            key,
            {
                "matched": True,
                "tmdb_id": tid,
                "vote_average": average,
                "vote_count": count,
                "title": payload.get("title") or payload.get("original_title") or "",
            },
        )
        return average, count

    # TMDB TV statuses that mean no further episodes are expected.
    TV_ENDED_STATUSES = frozenset({"Ended", "Canceled", "Cancelled"})

    def get_tv_status(self, tmdb_id: int) -> str | None:
        """Return TMDB TV status string (e.g. Ended, Returning Series), or None."""
        try:
            tid = int(tmdb_id)
        except (TypeError, ValueError):
            return None
        if tid <= 0:
            return None
        key = f"tv_status:{tid}"
        cached = self._cached(key)
        if cached is not None:
            if not cached.get("matched"):
                return None
            status = str(cached.get("status") or "").strip()
            return status or None

        self.lookups += 1
        payload = self._get(f"/tv/{tid}", {})
        if payload is None:
            return None
        status = str(payload.get("status") or "").strip()
        if not status:
            self._store_negative(key)
            return None
        self._store(
            key,
            {
                "matched": True,
                "tmdb_id": tid,
                "status": status,
                "name": payload.get("name") or payload.get("original_name") or "",
            },
        )
        return status

    def is_tv_series_ended(self, tmdb_id: int) -> bool | None:
        """True if TMDB marks the show Ended/Canceled; False if still airing; None if unknown."""
        status = self.get_tv_status(tmdb_id)
        if status is None:
            return None
        return status in self.TV_ENDED_STATUSES

    def is_valid_tv_episode(
        self,
        tmdb_id: int | None,
        season: int,
        episode: int,
    ) -> bool | None:
        """Check season/episode against TMDB episode_count.

        Returns True/False when validation is possible, or None if TMDB data
        is unavailable (caller should fail open and keep the episode).
        """
        if tmdb_id is None:
            return None
        try:
            season_num = int(season)
            episode_num = int(episode)
        except (TypeError, ValueError):
            return False
        if season_num < 0 or episode_num < 1:
            return False
        counts = self.get_tv_season_episode_counts(tmdb_id)
        if counts is None:
            return None
        max_ep = counts.get(season_num)
        if max_ep is not None and episode_num <= max_ep:
            return True
        # Cached episode_count never expired, so a new TMDB episode (or a new
        # season) would stay rejected forever. Re-query when the cache is stale.
        age = self._tv_seasons_cache_age(int(tmdb_id))
        ttl = self.season_counts_ttl_seconds
        if age is not None and age < ttl:
            return False
        refreshed = self.get_tv_season_episode_counts(tmdb_id, force_refresh=True)
        if refreshed is None:
            return False
        max_ep = refreshed.get(season_num)
        if max_ep is None:
            return False
        return episode_num <= max_ep

    @staticmethod
    def _year_from(date_str: str | None) -> int | None:
        if not date_str or len(str(date_str)) < 4:
            return None
        try:
            return int(str(date_str)[:4])
        except ValueError:
            return None

    def _best_result(
        self,
        payload: dict | None,
        year: int | None,
        *,
        date_key: str,
    ) -> dict | None:
        if not payload:
            return None
        results = payload.get("results") or []
        if not results:
            return None
        if year:
            for result in results:
                if self._year_from(result.get(date_key)) == year:
                    return result
        return results[0]

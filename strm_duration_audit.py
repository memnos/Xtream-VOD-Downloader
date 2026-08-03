"""Audit movie .strm files: compare probed duration vs TMDB runtime.

Persists every checked result (ok + errors) so later runs skip already-audited
movies. Errors are derived for the UI from the results store.

Also cleans bad STRMs during the audit:
- probe_failed: delete only after batches of 100 probe attempts, and only if
  the batch had at least one successful probe (avoids wiping the library on a
  provider outage).
- no Italian audio (when language tags are conclusive): delete the movie
  folder, or only .strm+.nfo when a local download exists.

Movies only — series are out of scope for now.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from datetime import datetime
from typing import Any

from core import (
    STRM_DURATION_AUDIT_STATUS_FILE,
    STRM_DURATION_ERRORS_FILE,
    STRM_OUTPUT_MOVIES_PATH,
    _save_json_file,
    build_movie_stream_url,
    build_movie_strm_path_tmdb,
    clean_strm_folder_title,
    exclude_hidden_items,
    format_elapsed_seconds,
    group_catalog_versions,
    item_file_size_bytes,
    load_credentials,
    load_json_file,
    load_strm_sync_config,
    local_download_exists_for_strm,
    probe_stream_media_info,
    read_strm_url,
    write_strm,
)
from discarded_movie_streams import (
    catalog_group_for_title,
    iter_alternate_catalog_versions,
    mark_movie_stream_discarded,
    set_discarded_replaced_by,
)
from tmdb import TmdbClient

TMDB_ID_RE = re.compile(r"\[tmdbid-(\d+)\]", re.IGNORECASE)
MOVIE_STREAM_URL_RE = re.compile(
    r"/movie/[^/]+/[^/]+/(\d+)\.([A-Za-z0-9]+)(?:[?#].*)?$",
    re.IGNORECASE,
)
ITALIAN_LANG_RE = re.compile(r"^(ita|it|italian|italiano)$", re.IGNORECASE)
ITALIAN_TITLE_RE = re.compile(r"\b(?:ita|italian|italiano)\b", re.IGNORECASE)
UNKNOWN_LANG_RE = re.compile(r"^(und|unknown|null|none)?$", re.IGNORECASE)

DEFAULT_THRESHOLD_SEC = 5 * 60
# Xtream providers often allow only one concurrent stream/download.
DEFAULT_WORKERS = 1
DEFAULT_PROBE_TIMEOUT = 45
MAX_WORKERS = 1
# Wait this many probe attempts before deleting probe_failed entries.
PROBE_DELETE_BATCH_SIZE = 100
# Pause audit while Emby/Jellyfin play; resume after this idle period.
PLAYBACK_RESUME_IDLE_SEC = 5 * 60
ERROR_STATUSES = frozenset(
    {"mismatch", "probe_failed", "no_runtime", "no_tmdb", "no_italian"}
)
# Probe outcomes that count toward the provider-health batch.
PROBE_ATTEMPT_STATUSES = frozenset(
    {"ok", "mismatch", "probe_failed", "no_italian", "deleted_no_italian"}
)

_audit_lock = threading.Lock()
_audit_thread: threading.Thread | None = None
_audit_stop = threading.Event()
_status_lock = threading.Lock()
_results_lock = threading.Lock()


def default_audit_status() -> dict[str, Any]:
    return {
        "running": False,
        "paused": False,
        "pause_reason": "",
        "stop_requested": False,
        "progress": 0.0,
        "progress_text": "",
        "checked": 0,
        "ok": 0,
        "mismatch": 0,
        "probe_failed": 0,
        "no_runtime": 0,
        "no_tmdb": 0,
        "no_italian": 0,
        "deleted_probe_failed": 0,
        "deleted_no_italian": 0,
        "deleted_folders": 0,
        "deleted_strm_only": 0,
        "probe_batch_kept": 0,
        "replaced_alternates": 0,
        "no_alternate": 0,
        "skipped": 0,
        "pending": 0,
        "total": 0,
        "threshold_sec": DEFAULT_THRESHOLD_SEC,
        "workers": DEFAULT_WORKERS,
        "force_rescan": False,
        "last_error": "",
        "last_run": "",
        "elapsed_sec": 0.0,
        "heartbeat_at": "",
        "heartbeat_unix": 0.0,
        "current_title": "",
        "last_playback_at": "",
        "playback_idle_sec": 0,
        "log": [],
    }


def load_audit_status() -> dict[str, Any]:
    data = load_json_file(STRM_DURATION_AUDIT_STATUS_FILE, default_audit_status())
    if not isinstance(data, dict):
        return default_audit_status()
    merged = {**default_audit_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


def save_audit_status(status: dict[str, Any]) -> None:
    _save_json_file(STRM_DURATION_AUDIT_STATUS_FILE, status)


def _touch_heartbeat(status: dict[str, Any], *, title: str = "") -> None:
    now = time.time()
    status["heartbeat_unix"] = now
    status["heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
    if title:
        status["current_title"] = title
    started_perf = status.get("_started_perf")
    if isinstance(started_perf, (int, float)) and started_perf > 0:
        status["elapsed_sec"] = time.perf_counter() - float(started_perf)


def audit_heartbeat_age_sec(status: dict[str, Any] | None = None) -> float | None:
    """Seconds since last heartbeat, or None if never set."""
    status = status or load_audit_status()
    hb = float(status.get("heartbeat_unix") or 0)
    if hb <= 0:
        return None
    return max(0.0, time.time() - hb)


def clear_stale_audit_running(*, reason: str = "process restarted") -> bool:
    """If status says running but no live thread, clear the flag. Returns True if cleared."""
    with _audit_lock:
        alive = _audit_thread is not None and _audit_thread.is_alive()
    if alive:
        return False
    status = load_audit_status()
    if not status.get("running"):
        return False
    status["running"] = False
    msg = f"Cleared stale running=True ({reason})"
    if not status.get("last_error"):
        status["last_error"] = f"Audit interrupted ({reason})."
    log = status.setdefault("log", [])
    if isinstance(log, list):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log.append(f"[{timestamp}] {msg}")
        status["log"] = log[-80:]
    _touch_heartbeat(status)
    _save_status(status)
    return True


def is_audit_thread_alive() -> bool:
    with _audit_lock:
        return _audit_thread is not None and _audit_thread.is_alive()


def media_servers_now_playing() -> list[str]:
    """Return labels for any active Emby/Jellyfin NowPlayingItem (any user)."""
    titles: list[str] = []
    try:
        from core import load_auto_download_config
        from emby_watcher import MediaServerClient
    except Exception:
        return titles

    # Fast path: watcher already saw playback for the configured user.
    try:
        from core import load_watcher_status

        ws = load_watcher_status()
        if ws.get("playback_active") and ws.get("current_playing"):
            titles.append(str(ws.get("current_playing")))
    except Exception:
        pass

    auto = load_auto_download_config()
    servers = (
        ("emby", "emby_enabled", "emby_url", "emby_api_key"),
        ("jellyfin", "jellyfin_enabled", "jellyfin_url", "jellyfin_api_key"),
    )
    for server_id, enabled_key, url_key, key_key in servers:
        if not auto.get(enabled_key):
            continue
        url = str(auto.get(url_key) or "").strip()
        api_key = str(auto.get(key_key) or "").strip()
        if not url or not api_key:
            continue
        try:
            client = MediaServerClient(url, api_key, server_id)
            for session in client.get_sessions():
                item = session.get("NowPlayingItem") or {}
                if not item:
                    continue
                name = str(item.get("SeriesName") or item.get("Name") or "").strip()
                user = str(session.get("UserName") or "").strip()
                label = f"{server_id}: {name}" + (f" ({user})" if user else "")
                if label not in titles:
                    titles.append(label)
        except Exception:
            continue
    return titles


def _should_pause_for_playback(
    last_playback_at: float,
) -> tuple[bool, str, float]:
    """Return (pause, reason, updated_last_playback_at)."""
    playing = media_servers_now_playing()
    now = time.time()
    if playing:
        label = playing[0]
        return True, f"Playback active — {label}", now
    if last_playback_at > 0:
        idle = now - last_playback_at
        if idle < PLAYBACK_RESUME_IDLE_SEC:
            remain = int(PLAYBACK_RESUME_IDLE_SEC - idle)
            return (
                True,
                f"Waiting {remain}s after playback stopped (need {PLAYBACK_RESUME_IDLE_SEC // 60}m idle)",
                last_playback_at,
            )
    return False, "", last_playback_at


def request_stop_duration_audit(*, reason: str = "stopped by user") -> bool:
    """Ask the running audit to stop ASAP. Returns True if a stop was signaled."""
    _audit_stop.set()
    status = load_audit_status()
    was_running = bool(status.get("running")) or is_audit_thread_alive()
    status["stop_requested"] = True
    status["paused"] = False
    status["pause_reason"] = ""
    if was_running:
        status["progress_text"] = f"Stopping… ({reason})"
        log = status.setdefault("log", [])
        if isinstance(log, list):
            timestamp = datetime.now().strftime("%H:%M:%S")
            log.append(f"[{timestamp}] Stop requested: {reason}")
            status["log"] = log[-80:]
        _touch_heartbeat(status)
        _save_status(status)
    return was_running


def stop_duration_audit(*, reason: str = "stopped by user", wait_sec: float = 2.0) -> bool:
    """Signal stop and clear running if the thread already exited."""
    signaled = request_stop_duration_audit(reason=reason)
    deadline = time.time() + max(0.0, wait_sec)
    while time.time() < deadline and is_audit_thread_alive():
        time.sleep(0.2)
    if not is_audit_thread_alive():
        status = load_audit_status()
        if status.get("running") or status.get("stop_requested"):
            status["running"] = False
            status["paused"] = False
            status["stop_requested"] = False
            status["progress_text"] = status.get("progress_text") or f"Stopped ({reason})"
            _save_status(status)
    return signaled


def _audit_should_stop(status: dict[str, Any] | None = None) -> bool:
    if _audit_stop.is_set():
        return True
    status = status or load_audit_status()
    return bool(status.get("stop_requested"))


def _wait_while_paused_or_stop(
    status: dict[str, Any],
    *,
    last_playback_at: float,
) -> tuple[bool, float]:
    """Block while playback pause is needed. Returns (should_stop, last_playback_at)."""
    while True:
        if _audit_should_stop(status):
            return True, last_playback_at
        pause, reason, last_playback_at = _should_pause_for_playback(last_playback_at)
        if not pause:
            if status.get("paused"):
                status["paused"] = False
                status["pause_reason"] = ""
                status["playback_idle_sec"] = PLAYBACK_RESUME_IDLE_SEC
                _append_log(status, "Resuming audit after playback idle")
                _touch_heartbeat(status)
                _save_status(status)
            return False, last_playback_at

        status["paused"] = True
        status["pause_reason"] = reason
        status["progress_text"] = f"Paused — {reason}"
        if last_playback_at > 0:
            status["last_playback_at"] = datetime.fromtimestamp(
                last_playback_at
            ).isoformat(timespec="seconds")
            status["playback_idle_sec"] = int(max(0.0, time.time() - last_playback_at))
        _touch_heartbeat(status)
        _save_status(status)
        # Short sleeps so stop is responsive.
        for _ in range(10):
            if _audit_should_stop(status):
                return True, last_playback_at
            time.sleep(0.5)


def default_errors_payload() -> dict[str, Any]:
    return {
        "updated_at": "",
        "threshold_sec": DEFAULT_THRESHOLD_SEC,
        "movies_root": "",
        "results": {},
        "errors": [],
        "summary": {},
    }


def load_duration_errors() -> dict[str, Any]:
    data = load_json_file(STRM_DURATION_ERRORS_FILE, default_errors_payload())
    if not isinstance(data, dict):
        return default_errors_payload()
    merged = {**default_errors_payload(), **data}
    results = merged.get("results")
    if not isinstance(results, dict):
        results = {}
    # Migrate legacy payloads that only had an errors list.
    if not results:
        for err in merged.get("errors") or []:
            if not isinstance(err, dict):
                continue
            path = str(err.get("strm_path") or "")
            if not path:
                continue
            entry = dict(err)
            entry.setdefault("status", err.get("reason") or "mismatch")
            if entry["status"] == "duration_mismatch":
                entry["status"] = "mismatch"
            results[path] = entry
    merged["results"] = results
    merged["errors"] = _errors_from_results(results)
    return merged


def save_duration_errors(payload: dict[str, Any]) -> None:
    results = payload.get("results")
    if isinstance(results, dict):
        payload = {
            **payload,
            "errors": _errors_from_results(results),
            "summary": _summary_from_results(results, payload.get("summary") or {}),
        }
    _save_json_file(STRM_DURATION_ERRORS_FILE, payload)


def _errors_from_results(results: dict[str, Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for path, entry in results.items():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        if status not in ERROR_STATUSES and status != "duration_mismatch":
            continue
        row = dict(entry)
        row.setdefault("strm_path", path)
        if row.get("reason") is None and status == "mismatch":
            row["reason"] = "duration_mismatch"
        errors.append(row)
    errors.sort(key=lambda row: abs(int(row.get("delta_sec") or 0)), reverse=True)
    return errors


def _summary_from_results(results: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = {
        "stored": len(results),
        "ok": 0,
        "mismatch": 0,
        "probe_failed": 0,
        "no_runtime": 0,
        "no_tmdb": 0,
        "no_italian": 0,
        "deleted_probe_failed": 0,
        "deleted_no_italian": 0,
    }
    for entry in results.values():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        if status == "duration_mismatch":
            status = "mismatch"
        if status in summary:
            summary[status] += 1
    if isinstance(extra, dict):
        summary.update({k: v for k, v in extra.items() if k not in summary or k in ("total", "checked", "skipped", "pending")})
    return summary


def is_italian_lang_tag(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if ITALIAN_LANG_RE.match(text):
        return True
    return bool(ITALIAN_TITLE_RE.search(text))


def is_unknown_lang_tag(value: str) -> bool:
    return bool(UNKNOWN_LANG_RE.match(str(value or "").strip()))


def media_has_italian_audio(media: dict[str, Any] | None) -> bool | None:
    """Return True/False when language evidence is conclusive, else None.

    None means tags are missing/unknown — do not delete for missing Italian.
    """
    if not isinstance(media, dict):
        return None
    audio_streams = [
        stream
        for stream in (media.get("streams") or [])
        if isinstance(stream, dict) and str(stream.get("type") or "").lower() == "audio"
    ]
    if not audio_streams:
        return None

    known_langs: list[str] = []
    for stream in audio_streams:
        lang = str(stream.get("language") or "").strip()
        title = str(stream.get("title") or "").strip()
        if is_italian_lang_tag(lang) or is_italian_lang_tag(title):
            return True
        if lang and not is_unknown_lang_tag(lang):
            known_langs.append(lang)
        elif title and ITALIAN_TITLE_RE.search(title):
            return True

    if not known_langs:
        return None
    return False


def audio_languages_from_media(media: dict[str, Any] | None) -> list[str]:
    if not isinstance(media, dict):
        return []
    langs: list[str] = []
    for stream in media.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        if str(stream.get("type") or "").lower() != "audio":
            continue
        lang = str(stream.get("language") or "").strip()
        if lang and lang not in langs:
            langs.append(lang)
    return langs


def _sibling_nfo_path(strm_path: str) -> str:
    return os.path.splitext(strm_path)[0] + ".nfo"


def _path_is_under_root(path: str, root: str) -> bool:
    real_path = os.path.realpath(path)
    real_root = os.path.realpath(root)
    if real_path == real_root:
        return False
    prefix = real_root if real_root.endswith(os.sep) else real_root + os.sep
    return real_path.startswith(prefix)


def delete_bad_movie_strm(
    strm_path: str,
    *,
    movies_root: str,
    keep_folder: bool = False,
) -> dict[str, Any]:
    """Delete a bad movie STRM folder, or only .strm+.nfo when keep_folder=True.

    Returns keys: ok, action (deleted_folder|deleted_strm|refused|missing|error), detail.
    """
    result: dict[str, Any] = {
        "ok": False,
        "action": "refused",
        "detail": "",
        "removed": [],
    }
    if not strm_path or not movies_root:
        result["detail"] = "missing path/root"
        return result
    if not _path_is_under_root(strm_path, movies_root):
        result["detail"] = "path outside movies root"
        return result

    folder = os.path.dirname(os.path.realpath(strm_path))
    real_root = os.path.realpath(movies_root)
    # Never rmtree the movies root itself — only the movie subfolder.
    if keep_folder or folder == real_root:
        try:
            removed: list[str] = []
            if os.path.isfile(strm_path):
                os.remove(strm_path)
                removed.append(strm_path)
            nfo = _sibling_nfo_path(strm_path)
            if os.path.isfile(nfo) and _path_is_under_root(nfo, movies_root):
                os.remove(nfo)
                removed.append(nfo)
            if not removed:
                result["action"] = "missing"
                result["detail"] = "strm already gone"
                return result
            result["ok"] = True
            result["action"] = "deleted_strm"
            result["removed"] = removed
            return result
        except OSError as exc:
            result["action"] = "error"
            result["detail"] = str(exc)
            return result

    if not _path_is_under_root(folder, movies_root) and folder != real_root:
        result["detail"] = "folder outside movies root"
        return result

    try:
        if not os.path.isdir(folder):
            removed = []
            if os.path.isfile(strm_path):
                os.remove(strm_path)
                removed.append(strm_path)
            nfo = _sibling_nfo_path(strm_path)
            if os.path.isfile(nfo) and _path_is_under_root(nfo, movies_root):
                os.remove(nfo)
                removed.append(nfo)
            result["ok"] = bool(removed)
            result["action"] = "deleted_strm" if removed else "missing"
            result["removed"] = removed
            return result

        shutil.rmtree(folder)
        result["ok"] = True
        result["action"] = "deleted_folder"
        result["removed"] = [folder]
        return result
    except OSError as exc:
        result["action"] = "error"
        result["detail"] = str(exc)
        return result


def _append_log(status: dict[str, Any], message: str, *, limit: int = 80) -> None:
    log = status.setdefault("log", [])
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {message}")
    status["log"] = log[-limit:]


def _save_status(status: dict[str, Any]) -> None:
    # Never persist in-memory catalog / credentials on disk.
    public = {
        k: v
        for k, v in status.items()
        if not str(k).startswith("_")
    }
    with _status_lock:
        save_audit_status(public)


def extract_tmdb_id(path: str) -> int | None:
    match = TMDB_ID_RE.search(path or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def extract_stream_id(url: str) -> str:
    match = MOVIE_STREAM_URL_RE.search(url or "")
    if not match:
        return ""
    return match.group(1)


def iter_movie_strm_files(movies_root: str) -> list[str]:
    if not movies_root or not os.path.isdir(movies_root):
        return []
    paths: list[str] = []
    for dirpath, _dirs, files in os.walk(movies_root):
        for name in files:
            if not name.lower().endswith(".strm"):
                continue
            full = os.path.join(dirpath, name)
            if extract_tmdb_id(full) is None:
                continue
            paths.append(full)
    paths.sort()
    return paths


def _title_from_strm_path(strm_path: str) -> str:
    folder = os.path.basename(os.path.dirname(strm_path))
    return clean_strm_folder_title(folder) or folder


def _build_tmdb_client(config: dict | None = None) -> TmdbClient | None:
    config = config or load_strm_sync_config()
    api_key = str(config.get("tmdb_api_key") or os.environ.get("TMDB_API_KEY") or "").strip()
    if not api_key:
        return None
    return TmdbClient(
        api_key,
        language=str(config.get("tmdb_language") or os.environ.get("TMDB_LANGUAGE") or "it-IT"),
        rate_limit=int(config.get("tmdb_rate_limit") or 40),
    )


def _result_entry(
    *,
    status: str,
    strm_path: str,
    stream_id: str,
    tmdb_id: int | None,
    title: str,
    url: str,
    tmdb_runtime_sec: int | None,
    probed_duration_sec: int | None,
    delta_sec: int | None,
    reason: str | None,
    checked_at: str,
    detail: str | None = None,
    media: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "status": status,
        "stream_id": stream_id,
        "tmdb_id": tmdb_id,
        "title": title,
        "strm_path": strm_path,
        "url": url,
        "tmdb_runtime_sec": tmdb_runtime_sec,
        "probed_duration_sec": probed_duration_sec,
        "delta_sec": delta_sec,
        "reason": reason,
        "checked_at": checked_at,
    }
    if detail:
        entry["detail"] = detail
    if media:
        entry["media"] = media
    return entry


def _audit_one(
    strm_path: str,
    *,
    tmdb_client: TmdbClient,
    threshold_sec: int,
    probe_timeout: int,
) -> dict[str, Any]:
    """Return a result dict with keys: status, entry."""
    checked_at = datetime.now().isoformat(timespec="seconds")
    tmdb_id = extract_tmdb_id(strm_path)
    title = _title_from_strm_path(strm_path)
    url = read_strm_url(strm_path) or ""
    stream_id = extract_stream_id(url)

    if tmdb_id is None:
        entry = _result_entry(
            status="no_tmdb",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=None,
            title=title,
            url=url,
            tmdb_runtime_sec=None,
            probed_duration_sec=None,
            delta_sec=None,
            reason="no_tmdb",
            checked_at=checked_at,
        )
        return {"status": "no_tmdb", "entry": entry, "probe_attempt": False}

    runtime_min = tmdb_client.get_movie_runtime(tmdb_id)
    if runtime_min is None or runtime_min <= 0:
        entry = _result_entry(
            status="no_runtime",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=tmdb_id,
            title=title,
            url=url,
            tmdb_runtime_sec=None,
            probed_duration_sec=None,
            delta_sec=None,
            reason="no_runtime",
            checked_at=checked_at,
        )
        return {"status": "no_runtime", "entry": entry, "probe_attempt": False}

    runtime_sec = int(runtime_min) * 60
    if not url:
        entry = _result_entry(
            status="probe_failed",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=tmdb_id,
            title=title,
            url=url,
            tmdb_runtime_sec=runtime_sec,
            probed_duration_sec=None,
            delta_sec=None,
            reason="probe_failed",
            checked_at=checked_at,
        )
        return {"status": "probe_failed", "entry": entry, "probe_attempt": True}

    media = probe_stream_media_info(url, timeout=probe_timeout)
    probed = float((media or {}).get("duration") or 0)
    if media is None or probed <= 0:
        entry = _result_entry(
            status="probe_failed",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=tmdb_id,
            title=title,
            url=url,
            tmdb_runtime_sec=runtime_sec,
            probed_duration_sec=None,
            delta_sec=None,
            reason="probe_failed",
            checked_at=checked_at,
        )
        return {"status": "probe_failed", "entry": entry, "probe_attempt": True}

    probed_sec = int(round(probed))
    delta = probed_sec - runtime_sec
    audio_langs = audio_languages_from_media(media)
    italian = media_has_italian_audio(media)
    if italian is False:
        entry = _result_entry(
            status="no_italian",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=tmdb_id,
            title=title,
            url=url,
            tmdb_runtime_sec=runtime_sec,
            probed_duration_sec=probed_sec,
            delta_sec=delta,
            reason="no_italian",
            checked_at=checked_at,
            detail=f"audio languages: {', '.join(audio_langs) or '—'}",
            media=media,
        )
        entry["audio_languages"] = audio_langs
        entry["has_italian"] = False
        return {"status": "no_italian", "entry": entry, "probe_attempt": True}

    if abs(delta) > threshold_sec:
        entry = _result_entry(
            status="mismatch",
            strm_path=strm_path,
            stream_id=stream_id,
            tmdb_id=tmdb_id,
            title=title,
            url=url,
            tmdb_runtime_sec=runtime_sec,
            probed_duration_sec=probed_sec,
            delta_sec=delta,
            reason="duration_mismatch",
            checked_at=checked_at,
            media=media,
        )
        entry["audio_languages"] = audio_langs
        entry["has_italian"] = italian
        return {"status": "mismatch", "entry": entry, "probe_attempt": True}

    entry = _result_entry(
        status="ok",
        strm_path=strm_path,
        stream_id=stream_id,
        tmdb_id=tmdb_id,
        title=title,
        url=url,
        tmdb_runtime_sec=runtime_sec,
        probed_duration_sec=probed_sec,
        delta_sec=delta,
        reason=None,
        checked_at=checked_at,
        media=media,
    )
    entry["audio_languages"] = audio_langs
    entry["has_italian"] = italian
    return {"status": "ok", "entry": entry, "probe_attempt": True}


def _count_status(results: dict[str, Any], key: str) -> int:
    total = 0
    for entry in results.values():
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("status") or "")
        if status == "duration_mismatch":
            status = "mismatch"
        if status == key:
            total += 1
    return total


def _persist_results(
    *,
    root: str,
    threshold_sec: int,
    results: dict[str, Any],
    status: dict[str, Any],
) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold_sec": threshold_sec,
        "movies_root": root,
        "results": results,
        "summary": {
            "total": status.get("total", 0),
            "checked": status.get("checked", 0),
            "skipped": status.get("skipped", 0),
            "pending": status.get("pending", 0),
            "ok": _count_status(results, "ok"),
            "mismatch": _count_status(results, "mismatch"),
            "probe_failed": _count_status(results, "probe_failed"),
            "no_runtime": _count_status(results, "no_runtime"),
            "no_tmdb": _count_status(results, "no_tmdb"),
            "no_italian": _count_status(results, "no_italian"),
            "deleted_probe_failed": _count_status(results, "deleted_probe_failed"),
            "deleted_no_italian": _count_status(results, "deleted_no_italian"),
            "deleted_folders": int(status.get("deleted_folders") or 0),
            "deleted_strm_only": int(status.get("deleted_strm_only") or 0),
            "probe_batch_kept": int(status.get("probe_batch_kept") or 0),
            "replaced_alternates": int(status.get("replaced_alternates") or 0),
            "no_alternate": int(status.get("no_alternate") or 0),
        },
    }
    with _results_lock:
        save_duration_errors(payload)


def _refresh_status_counts(status: dict[str, Any], results: dict[str, Any]) -> None:
    status["ok"] = _count_status(results, "ok")
    status["mismatch"] = _count_status(results, "mismatch")
    status["probe_failed"] = _count_status(results, "probe_failed")
    status["no_runtime"] = _count_status(results, "no_runtime")
    status["no_tmdb"] = _count_status(results, "no_tmdb")
    status["no_italian"] = _count_status(results, "no_italian")
    status["deleted_probe_failed"] = _count_status(results, "deleted_probe_failed")
    status["deleted_no_italian"] = _count_status(results, "deleted_no_italian")
    status["checked"] = len(results)


def _apply_delete_to_entry(
    entry: dict[str, Any],
    *,
    movies_root: str,
    new_status: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    strm_path = str(entry.get("strm_path") or "")
    has_local = local_download_exists_for_strm(strm_path)
    delete_result = delete_bad_movie_strm(
        strm_path,
        movies_root=movies_root,
        keep_folder=has_local,
    )
    entry = dict(entry)
    entry["had_local"] = has_local
    entry["deleted_action"] = delete_result.get("action")
    entry["deleted_detail"] = delete_result.get("detail") or ""
    entry["deleted_paths"] = delete_result.get("removed") or []
    if delete_result.get("ok"):
        entry["status"] = new_status
        entry["reason"] = new_status
        if delete_result.get("action") == "deleted_folder":
            status["deleted_folders"] = int(status.get("deleted_folders") or 0) + 1
        elif delete_result.get("action") == "deleted_strm":
            status["deleted_strm_only"] = int(status.get("deleted_strm_only") or 0) + 1
        _mark_entry_stream_discarded(entry, reason=new_status)
    else:
        entry["delete_failed"] = True
    return entry


def _mark_entry_stream_discarded(entry: dict[str, Any], *, reason: str) -> None:
    stream_id = str(entry.get("stream_id") or "").strip()
    if not stream_id:
        return
    url = str(entry.get("url") or "")
    ext = "mp4"
    match = MOVIE_STREAM_URL_RE.search(url)
    if match:
        ext = match.group(2)
    size = 0
    media = entry.get("media")
    if isinstance(media, dict):
        size = int(media.get("size") or 0)
    mark_movie_stream_discarded(
        stream_id=stream_id,
        ext=ext,
        size=size,
        name=str(entry.get("title") or ""),
        title=str(entry.get("title") or ""),
        reason=reason,
        tmdb_id=entry.get("tmdb_id") if isinstance(entry.get("tmdb_id"), int) else None,
        url=url,
    )


def _load_movie_catalog_groups(
    status: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, list[dict]]:
    """Lazy-load VOD catalog grouped by title key (once per audit run)."""
    cached = status.get("_catalog_groups")
    if isinstance(cached, dict):
        return cached

    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    if not host or not user or not password:
        _append_log(status, "Alternate versions: missing Xtream credentials — skip")
        status["_catalog_groups"] = {}
        return {}

    _append_log(status, "Loading VOD catalog for alternate versions...")
    _save_status(status)
    try:
        from strm_sync import _fetch_vod_streams

        vod_ids = [str(cid) for cid in (config.get("vod_category_ids") or []) if cid]
        if vod_ids:
            movies: list[dict] = []
            for cat_id in vod_ids:
                movies.extend(_fetch_vod_streams(host, user, password, cat_id))
        else:
            movies = _fetch_vod_streams(host, user, password, None)
            movies = exclude_hidden_items(movies, "vod")
        groups = group_catalog_versions(movies)
        status["_catalog_groups"] = groups
        status["_xtream_host"] = host
        status["_xtream_user"] = user
        status["_xtream_password"] = password
        _append_log(
            status,
            f"VOD catalog ready: {len(movies)} streams, {len(groups)} title groups",
        )
        _save_status(status)
        return groups
    except Exception as exc:  # noqa: BLE001
        _append_log(status, f"VOD catalog load failed: {exc}")
        status["_catalog_groups"] = {}
        _save_status(status)
        return {}


def try_replace_with_alternate_version(
    entry: dict[str, Any],
    *,
    movies_root: str,
    status: dict[str, Any],
    config: dict[str, Any],
    tmdb_client: TmdbClient | None,
    threshold_sec: int,
    probe_timeout: int,
) -> dict[str, Any] | None:
    """Probe other catalog versions; write first working STRM. None if none work."""
    title = str(entry.get("title") or "")
    tmdb_id = entry.get("tmdb_id")
    failed_sid = str(entry.get("stream_id") or "").strip()
    groups = _load_movie_catalog_groups(status, config)
    versions = catalog_group_for_title(groups, title)
    if not versions and title:
        # Try raw folder-ish title from strm path
        versions = catalog_group_for_title(groups, clean_strm_folder_title(title) or title)
    if not versions:
        _append_log(status, f"No catalog alternates for: {title or failed_sid}")
        return None

    allow_4k = bool(config.get("allow_4k", False))
    host = str(status.get("_xtream_host") or "")
    user = str(status.get("_xtream_user") or "")
    password = str(status.get("_xtream_password") or "")
    if not host:
        creds = load_credentials()
        host = str(creds.get("host") or "").strip()
        user = str(creds.get("user") or "").strip()
        password = str(creds.get("password") or "").strip()

    candidates = iter_alternate_catalog_versions(
        versions,
        exclude_stream_ids={failed_sid} if failed_sid else set(),
        allow_4k=allow_4k,
    )
    if not candidates:
        _append_log(status, f"No usable alternates left for: {title}")
        return None

    _append_log(
        status,
        f"Trying {len(candidates)} alternate(s) for: {title}",
    )

    for cand in candidates:
        cand_id = str(cand.get("stream_id") or "")
        cand_name = str(cand.get("name") or "")
        ext = str(cand.get("container_extension") or "mp4")
        url = build_movie_stream_url(host, user, password, cand_id, ext)

        # Prefer same TMDB id when we know it.
        strm_path: str | None = None
        if tmdb_client is not None and tmdb_id:
            match = tmdb_client.search_movie(cand_name)
            if not match or int(match.get("tmdb_id") or 0) != int(tmdb_id):
                continue
            _folder, strm_path = build_movie_strm_path_tmdb(
                match.get("title") or title,
                match.get("year"),
                match.get("tmdb_id"),
                movies_root,
            )
        elif tmdb_client is not None:
            match = tmdb_client.search_movie(cand_name)
            if not match:
                continue
            _folder, strm_path = build_movie_strm_path_tmdb(
                match.get("title") or cand_name,
                match.get("year"),
                match.get("tmdb_id"),
                movies_root,
            )
        else:
            # Fallback: recreate under the previous folder name if possible.
            old_path = str(entry.get("strm_path") or "")
            if old_path:
                strm_path = old_path
            else:
                continue

        status["current_title"] = f"{title} → try {cand_name[:50]}"
        _touch_heartbeat(status, title=status["current_title"])
        _save_status(status)

        media = probe_stream_media_info(url, timeout=probe_timeout)
        probed = float((media or {}).get("duration") or 0)
        if media is None or probed <= 0:
            mark_movie_stream_discarded(
                stream_id=cand_id,
                ext=ext,
                size=item_file_size_bytes(cand),
                name=cand_name,
                title=title,
                reason="probe_failed_alternate",
                tmdb_id=int(tmdb_id) if tmdb_id else None,
                url=url,
            )
            _append_log(status, f"Alternate failed: {cand_name} ({cand_id})")
            continue

        if not write_strm(strm_path, url):
            _append_log(status, f"Alternate probe ok but write failed: {strm_path}")
            continue

        # Update discarded entry with replacement pointer.
        if failed_sid:
            if not set_discarded_replaced_by(failed_sid, cand_id):
                mark_movie_stream_discarded(
                    stream_id=failed_sid,
                    ext="mp4",
                    size=0,
                    name=title,
                    title=title,
                    reason="probe_failed",
                    tmdb_id=int(tmdb_id) if tmdb_id else None,
                    url=str(entry.get("url") or ""),
                    replaced_by=cand_id,
                )

        probed_sec = int(round(probed))
        runtime_sec = entry.get("tmdb_runtime_sec")
        delta = None
        result_status = "ok"
        reason = None
        if runtime_sec:
            delta = probed_sec - int(runtime_sec)
            if abs(delta) > threshold_sec:
                result_status = "mismatch"
                reason = "duration_mismatch"

        italian = media_has_italian_audio(media)
        audio_langs = audio_languages_from_media(media)
        checked_at = datetime.now().isoformat(timespec="seconds")
        new_entry = _result_entry(
            status=result_status if italian is not False else "no_italian",
            strm_path=strm_path,
            stream_id=cand_id,
            tmdb_id=int(tmdb_id) if tmdb_id else extract_tmdb_id(strm_path),
            title=title,
            url=url,
            tmdb_runtime_sec=int(runtime_sec) if runtime_sec else None,
            probed_duration_sec=probed_sec,
            delta_sec=delta,
            reason=reason if italian is not False else "no_italian",
            checked_at=checked_at,
            detail=f"replaced broken stream {failed_sid} with {cand_id}",
            media=media,
        )
        new_entry["audio_languages"] = audio_langs
        new_entry["has_italian"] = italian
        new_entry["replaced_from_stream_id"] = failed_sid
        new_entry["catalog_name"] = cand_name

        if italian is False:
            # Written but no Italian — leave for handle path / delete rules.
            new_entry["status"] = "no_italian"
            new_entry["reason"] = "no_italian"

        _append_log(
            status,
            f"Replaced with alternate: {title} → {cand_name} ({cand_id})",
        )
        return {"status": new_entry["status"], "entry": new_entry, "path": strm_path}

    return None


def flush_probe_failure_batch(
    batch: list[dict[str, Any]],
    *,
    results: dict[str, Any],
    status: dict[str, Any],
    movies_root: str,
    config: dict[str, Any] | None = None,
    tmdb_client: TmdbClient | None = None,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
) -> None:
    """Delete probe_failed items only if the batch had ≥1 successful probe."""
    if not batch:
        return
    failures = [item for item in batch if item.get("status") == "probe_failed"]
    successes = [item for item in batch if item.get("status") != "probe_failed"]
    if not failures:
        batch.clear()
        return

    if not successes:
        status["probe_batch_kept"] = int(status.get("probe_batch_kept") or 0) + len(failures)
        _append_log(
            status,
            f"Probe batch: kept {len(failures)} probe_failed "
            f"(all {len(batch)} probes failed — likely provider issue)",
        )
        batch.clear()
        return

    config = config or load_strm_sync_config()
    deleted = 0
    replaced = 0
    no_alt = 0
    for item in failures:
        path = str(item.get("path") or "")
        entry = results.get(path) or item.get("entry") or {}
        if not isinstance(entry, dict):
            continue
        updated = _apply_delete_to_entry(
            entry,
            movies_root=movies_root,
            new_status="deleted_probe_failed",
            status=status,
        )
        results[path] = updated
        if updated.get("status") != "deleted_probe_failed":
            continue
        deleted += 1
        title = updated.get("title") or path
        action = updated.get("deleted_action")
        _append_log(status, f"Deleted probe_failed ({action}): {title}")

        # If a local file remains, only the broken .strm was removed — still try replace.
        alt = try_replace_with_alternate_version(
            updated,
            movies_root=movies_root,
            status=status,
            config=config,
            tmdb_client=tmdb_client,
            threshold_sec=threshold_sec,
            probe_timeout=probe_timeout,
        )
        if alt and alt.get("entry") and alt.get("path"):
            new_path = str(alt["path"])
            new_entry = alt["entry"]
            results[new_path] = new_entry
            updated["replaced_with"] = new_path
            updated["replaced_stream_id"] = new_entry.get("stream_id")
            results[path] = updated
            replaced += 1
            status["replaced_alternates"] = int(status.get("replaced_alternates") or 0) + 1
            if new_entry.get("status") == "no_italian":
                handle_no_italian_result(
                    new_path,
                    new_entry,
                    results=results,
                    status=status,
                    movies_root=movies_root,
                    try_alternates=False,
                )
        else:
            no_alt += 1
            status["no_alternate"] = int(status.get("no_alternate") or 0) + 1
            updated["no_alternate"] = True
            results[path] = updated
            _append_log(status, f"No working alternate — skipped: {title}")

    _append_log(
        status,
        f"Probe batch: deleted {deleted}/{len(failures)} probe_failed, "
        f"replaced {replaced}, no alternate {no_alt} "
        f"(batch {len(batch)}, ok-ish {len(successes)})",
    )
    batch.clear()


def handle_no_italian_result(
    path: str,
    entry: dict[str, Any],
    *,
    results: dict[str, Any],
    status: dict[str, Any],
    movies_root: str,
    try_alternates: bool = False,
) -> None:
    updated = _apply_delete_to_entry(
        entry,
        movies_root=movies_root,
        new_status="deleted_no_italian",
        status=status,
    )
    results[path] = updated
    title = updated.get("title") or path
    langs = ", ".join(updated.get("audio_languages") or []) or "—"
    if updated.get("status") == "deleted_no_italian":
        action = updated.get("deleted_action")
        local_note = " (kept folder, local file present)" if updated.get("had_local") else ""
        _append_log(
            status,
            f"Deleted no-Italian ({action}){local_note}: {title} [{langs}]",
        )
    else:
        _append_log(
            status,
            f"No Italian but delete failed: {title} — {updated.get('deleted_detail') or updated.get('deleted_action')}",
        )


def run_duration_audit(
    *,
    movies_root: str | None = None,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    workers: int = DEFAULT_WORKERS,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
    config: dict | None = None,
    limit: int | None = None,
    force_rescan: bool = False,
    only_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Scan movie STRMs; skip already-checked unless force_rescan=True.

    If only_paths is set, audit just those files (always re-check them).
    """
    config = config or load_strm_sync_config()
    root = (movies_root or config.get("movies_output") or STRM_OUTPUT_MOVIES_PATH or "").strip()
    threshold_sec = max(30, int(threshold_sec))
    workers = max(1, min(MAX_WORKERS, int(workers)))
    probe_timeout = max(10, min(120, int(probe_timeout)))

    status = default_audit_status()
    status["running"] = True
    status["stop_requested"] = False
    status["paused"] = False
    status["threshold_sec"] = threshold_sec
    status["workers"] = workers
    status["force_rescan"] = bool(force_rescan)
    status["progress_text"] = "Listing movie .strm files..."
    status["_started_perf"] = time.perf_counter()
    _touch_heartbeat(status)
    _save_status(status)

    started = time.perf_counter()
    status["_started_perf"] = started

    try:
        tmdb_client = _build_tmdb_client(config)
        if tmdb_client is None:
            status["last_error"] = "TMDB API key missing — configure it in STRM sync settings."
            _append_log(status, status["last_error"])
            return status

        store = load_duration_errors()
        results: dict[str, Any] = dict(store.get("results") or {})
        if force_rescan and not only_paths:
            results = {}
            _append_log(status, "Force rescan: cleared previous results")

        if only_paths is not None:
            paths = []
            seen: set[str] = set()
            for raw in only_paths:
                path = os.path.realpath(str(raw or "").strip())
                if not path or path in seen:
                    continue
                seen.add(path)
                if os.path.isfile(path):
                    paths.append(path)
            # Re-audit these even if already stored.
            for path in paths:
                results.pop(path, None)
            pending = list(paths)
            skipped = 0
            _append_log(
                status,
                f"Scoped audit: {len(pending)} path(s) from sync/new movies",
            )
        else:
            paths = iter_movie_strm_files(root)
            if limit is not None and limit > 0:
                paths = paths[: int(limit)]
            pending = [path for path in paths if force_rescan or path not in results]
            skipped = len(paths) - len(pending)

        status["total"] = len(paths)
        status["skipped"] = skipped
        status["pending"] = len(pending)
        status["ok"] = _count_status(results, "ok")
        status["mismatch"] = _count_status(results, "mismatch")
        status["probe_failed"] = _count_status(results, "probe_failed")
        status["no_runtime"] = _count_status(results, "no_runtime")
        status["no_tmdb"] = _count_status(results, "no_tmdb")
        status["no_italian"] = _count_status(results, "no_italian")
        status["deleted_probe_failed"] = _count_status(results, "deleted_probe_failed")
        status["deleted_no_italian"] = _count_status(results, "deleted_no_italian")
        status["checked"] = len(results)

        _append_log(
            status,
            f"Duration audit: {len(paths)} movies in {root} "
            f"(pending={len(pending)}, skipped={skipped}, "
            f"threshold ±{threshold_sec}s, workers={workers}, "
            f"probe-delete batch={PROBE_DELETE_BATCH_SIZE})",
        )
        _save_status(status)

        if not paths:
            status["progress"] = 1.0
            status["progress_text"] = "No movie .strm with [tmdbid-…] found."
            _append_log(status, status["progress_text"])
        elif not pending:
            status["progress"] = 1.0
            status["progress_text"] = (
                f"Nothing pending — all {len(paths)} movies already checked "
                f"(mismatch={status['mismatch']}, ok={status['ok']})."
            )
            _append_log(status, status["progress_text"])
            _persist_results(
                root=root,
                threshold_sec=threshold_sec,
                results=results,
                status=status,
            )
        else:
            done = 0
            probe_batch: list[dict[str, Any]] = []
            last_playback_at = 0.0
            stopped_early = False

            for path in pending:
                should_stop, last_playback_at = _wait_while_paused_or_stop(
                    status, last_playback_at=last_playback_at
                )
                if should_stop:
                    stopped_early = True
                    _append_log(status, "Audit stopped by request")
                    break

                try:
                    result = _audit_one(
                        path,
                        tmdb_client=tmdb_client,
                        threshold_sec=threshold_sec,
                        probe_timeout=probe_timeout,
                    )
                except Exception as exc:  # noqa: BLE001 — keep audit running
                    entry = _result_entry(
                        status="probe_failed",
                        strm_path=path,
                        stream_id="",
                        tmdb_id=extract_tmdb_id(path),
                        title=_title_from_strm_path(path),
                        url=read_strm_url(path) or "",
                        tmdb_runtime_sec=None,
                        probed_duration_sec=None,
                        delta_sec=None,
                        reason="probe_failed",
                        checked_at=datetime.now().isoformat(timespec="seconds"),
                        detail=str(exc),
                    )
                    result = {
                        "status": "probe_failed",
                        "entry": entry,
                        "probe_attempt": True,
                    }

                done += 1
                entry = dict(result.get("entry") or {})
                status_name = str(result.get("status") or entry.get("status") or "")
                results[path] = entry

                if status_name == "no_italian":
                    handle_no_italian_result(
                        path,
                        entry,
                        results=results,
                        status=status,
                        movies_root=root,
                    )
                    # Successful probe (media readable) — counts for provider health.
                    probe_batch.append(
                        {
                            "path": path,
                            "status": "deleted_no_italian",
                            "entry": results[path],
                        }
                    )
                elif result.get("probe_attempt"):
                    probe_batch.append(
                        {
                            "path": path,
                            "status": status_name,
                            "entry": results[path],
                        }
                    )

                if len(probe_batch) >= PROBE_DELETE_BATCH_SIZE:
                    flush_probe_failure_batch(
                        probe_batch,
                        results=results,
                        status=status,
                        movies_root=root,
                        config=config,
                        tmdb_client=tmdb_client,
                        threshold_sec=threshold_sec,
                        probe_timeout=probe_timeout,
                    )

                _refresh_status_counts(status, results)
                status["pending"] = max(len(pending) - done, 0)
                status["progress"] = (skipped + done) / max(len(paths), 1)
                title = str((results.get(path) or {}).get("title") or "")
                status["progress_text"] = (
                    f"Movies — {skipped + done}/{len(paths)} "
                    f"(new {done}/{len(pending)}, skipped={skipped}, "
                    f"mismatch={status['mismatch']}, "
                    f"probe_failed={status['probe_failed']}, "
                    f"no_italian={status.get('deleted_no_italian', 0)}, "
                    f"deleted_pf={status.get('deleted_probe_failed', 0)})"
                )
                _touch_heartbeat(status, title=title)
                # Status/heartbeat every movie so the UI can detect stalls.
                _save_status(status)
                if done % 10 == 0 or done == len(pending):
                    tmdb_client.save_cache()
                    _persist_results(
                        root=root,
                        threshold_sec=threshold_sec,
                        results=results,
                        status=status,
                    )

            if probe_batch:
                # Only flush deletes if we weren't hard-stopped mid-batch without successes.
                if not stopped_early or any(
                    item.get("status") != "probe_failed" for item in probe_batch
                ):
                    flush_probe_failure_batch(
                        probe_batch,
                        results=results,
                        status=status,
                        movies_root=root,
                        config=config,
                        tmdb_client=tmdb_client,
                        threshold_sec=threshold_sec,
                        probe_timeout=probe_timeout,
                    )
                _refresh_status_counts(status, results)

            tmdb_client.save_cache()
            _persist_results(
                root=root,
                threshold_sec=threshold_sec,
                results=results,
                status=status,
            )

            if stopped_early:
                status["progress_text"] = (
                    f"Stopped: {status['mismatch']} mismatch, "
                    f"{status.get('deleted_probe_failed', 0)} probe failed deleted, "
                    f"{status['ok']} ok, checked {status['checked']}/"
                    f"{status['total']} "
                    f"({format_elapsed_seconds(time.perf_counter() - started)})"
                )
                _append_log(status, status["progress_text"])
                status["elapsed_sec"] = time.perf_counter() - started
                status["last_run"] = datetime.now().isoformat(timespec="seconds")
                return status

        status["elapsed_sec"] = time.perf_counter() - started
        status["last_run"] = datetime.now().isoformat(timespec="seconds")
        status["progress"] = 1.0
        status["progress_text"] = (
            f"Done: {status['mismatch']} mismatch, "
            f"{status['probe_failed']} probe failed kept, "
            f"{status.get('deleted_probe_failed', 0)} probe failed deleted, "
            f"{status.get('replaced_alternates', 0)} replaced, "
            f"{status.get('no_alternate', 0)} no alternate, "
            f"{status.get('deleted_no_italian', 0)} no-Italian deleted, "
            f"{status['no_runtime']} no TMDB runtime, "
            f"{status['ok']} ok, skipped {status['skipped']} "
            f"({format_elapsed_seconds(status['elapsed_sec'])})"
        )
        _append_log(status, status["progress_text"])
        _append_log(
            status,
            f"Results DB: {len(results)} stored "
            f"({_count_status(results, 'mismatch')} mismatch) → {STRM_DURATION_ERRORS_FILE}",
        )
        return status
    except Exception as exc:  # noqa: BLE001
        status["last_error"] = str(exc)
        _append_log(status, f"FATAL: {exc}")
        return status
    finally:
        status["running"] = False
        status["paused"] = False
        status["stop_requested"] = False
        status["pause_reason"] = ""
        status["elapsed_sec"] = status.get("elapsed_sec") or (time.perf_counter() - started)
        _save_status(status)


def is_duration_audit_running() -> bool:
    if is_audit_thread_alive():
        return True
    # Stale JSON "running" after container restart must not block a new start.
    clear_stale_audit_running()
    return False


def start_duration_audit(
    *,
    movies_root: str | None = None,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    workers: int = DEFAULT_WORKERS,
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT,
    config: dict | None = None,
    limit: int | None = None,
    force_rescan: bool = False,
    only_paths: list[str] | None = None,
) -> bool:
    global _audit_thread
    clear_stale_audit_running()
    with _audit_lock:
        if _audit_thread is not None and _audit_thread.is_alive():
            return False
        if load_audit_status().get("running"):
            return False

        _audit_stop.clear()

        def _worker() -> None:
            try:
                run_duration_audit(
                    movies_root=movies_root,
                    threshold_sec=threshold_sec,
                    workers=workers,
                    probe_timeout=probe_timeout,
                    config=config,
                    limit=limit,
                    force_rescan=force_rescan,
                    only_paths=only_paths,
                )
            finally:
                global _audit_thread
                with _audit_lock:
                    _audit_thread = None

        _audit_thread = threading.Thread(
            target=_worker, name="strm-duration-audit", daemon=True
        )
        _audit_thread.start()
        return True


# Clear orphaned running flag left by container/process restarts.
clear_stale_audit_running()

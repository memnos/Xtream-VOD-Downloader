"""Push audited STRM media info into Jellyfin via STRM Media Import plugin."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime
from typing import Any

import requests

from core import (
    DATA_DIR,
    STRM_OUTPUT_MOVIES_PATH,
    _save_json_file,
    load_auto_download_config,
    load_json_file,
    load_strm_sync_config,
)
from strm_duration_audit import load_duration_errors, save_duration_errors

PUSH_STATUS_FILE = os.environ.get(
    "STRM_JF_PUSH_STATUS_FILE",
    os.path.join(DATA_DIR, "strm_jf_push_status.json"),
)

_push_lock = threading.Lock()
_push_thread: threading.Thread | None = None


def default_push_status() -> dict[str, Any]:
    return {
        "running": False,
        "progress": 0.0,
        "progress_text": "",
        "total": 0,
        "applied": 0,
        "missing": 0,
        "failed": 0,
        "skipped_no_media": 0,
        "skipped_already": 0,
        "last_error": "",
        "last_run": "",
        "log": [],
    }


def load_push_status() -> dict[str, Any]:
    data = load_json_file(PUSH_STATUS_FILE, default_push_status())
    if not isinstance(data, dict):
        return default_push_status()
    merged = {**default_push_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


def save_push_status(status: dict[str, Any]) -> None:
    _save_json_file(PUSH_STATUS_FILE, status)


def _append_log(status: dict[str, Any], message: str, *, limit: int = 80) -> None:
    log = status.setdefault("log", [])
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {message}")
    status["log"] = log[-limit:]


def map_strm_path_to_jellyfin(
    strm_path: str,
    *,
    strm_root: str,
    jellyfin_root: str,
) -> str:
    """Map container STRM path to Jellyfin library path."""
    path = (strm_path or "").replace("\\", "/")
    src = (strm_root or "").replace("\\", "/").rstrip("/")
    dst = (jellyfin_root or "").replace("\\", "/").rstrip("/")
    if src and path.startswith(src + "/"):
        return dst + path[len(src) :]
    if src and path == src:
        return dst
    # Already a JF path
    if dst and path.startswith(dst + "/"):
        return path
    return path


def _jellyfin_config() -> tuple[str, str]:
    auto = load_auto_download_config()
    url = str(auto.get("jellyfin_url") or "").rstrip("/")
    key = str(auto.get("jellyfin_api_key") or "").strip()
    return url, key


def jellyfin_import_available() -> tuple[bool, str]:
    url, key = _jellyfin_config()
    if not url or not key:
        return False, "Configure Jellyfin URL and API key in Automatic download settings."
    try:
        resp = requests.get(
            f"{url}/StrmMediaImport/Ping",
            headers={"Authorization": f'MediaBrowser Token="{key}"'},
            timeout=10,
        )
        if resp.status_code == 404:
            return False, "Plugin STRM Media Import not found on Jellyfin (install + restart JF)."
        if resp.status_code in (401, 403):
            return False, "Jellyfin rejected the API key (need admin key)."
        resp.raise_for_status()
        return True, "Plugin ready."
    except requests.RequestException as exc:
        return False, f"Cannot reach Jellyfin: {exc}"


def _build_import_item(entry: dict[str, Any], jf_path: str) -> dict[str, Any] | None:
    media = entry.get("media")
    if not isinstance(media, dict):
        return None
    duration = float(media.get("duration") or entry.get("probed_duration_sec") or 0)
    streams = media.get("streams") or []
    if duration <= 0 and not streams:
        return None
    mapped_streams = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        mapped_streams.append(
            {
                "Index": int(stream.get("index") or 0),
                "Type": stream.get("type") or "Video",
                "Codec": stream.get("codec") or "",
                "Profile": stream.get("profile") or "",
                "BitRate": int(stream.get("bit_rate") or 0),
                "Width": int(stream.get("width") or 0),
                "Height": int(stream.get("height") or 0),
                "Channels": int(stream.get("channels") or 0),
                "SampleRate": int(stream.get("sample_rate") or 0),
                "AverageFrameRate": stream.get("average_frame_rate") or "",
                "PixelFormat": stream.get("pixel_format") or "",
                "Language": stream.get("language") or "",
                "Title": stream.get("title") or "",
                "IsDefault": bool(stream.get("is_default")),
                "IsExternal": bool(stream.get("is_external")),
            }
        )
    return {
        "Path": jf_path,
        "DurationSec": duration if duration > 0 else None,
        "Size": int(media.get("size") or 0) or None,
        "BitRate": int(media.get("bitrate") or 0) or None,
        "Container": media.get("container") or None,
        "Width": int(media.get("width") or 0) or None,
        "Height": int(media.get("height") or 0) or None,
        "Streams": mapped_streams,
    }


def media_push_fingerprint(entry: dict[str, Any]) -> str | None:
    """Stable hash of the media payload that would be sent to Jellyfin."""
    item = _build_import_item(entry, "__fingerprint__")
    if item is None:
        return None
    payload = {k: v for k, v in item.items() if k != "Path"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _already_pushed(entry: dict[str, Any], fingerprint: str) -> bool:
    prev = str(entry.get("jf_pushed_fingerprint") or "").strip()
    return bool(prev) and prev == fingerprint


def _mark_pushed(
    results: dict[str, Any],
    *,
    strm_path: str,
    fingerprint: str,
    item_id: str | None,
) -> None:
    entry = results.get(strm_path)
    if not isinstance(entry, dict):
        # Path key may differ by realpath — try realpath match.
        real = os.path.realpath(strm_path)
        for key, value in results.items():
            if isinstance(value, dict) and (
                key == strm_path or os.path.realpath(str(key)) == real
            ):
                entry = value
                strm_path = key
                break
    if not isinstance(entry, dict):
        return
    entry["jf_pushed_fingerprint"] = fingerprint
    entry["jf_pushed_at"] = datetime.now().isoformat(timespec="seconds")
    if item_id:
        entry["jf_item_id"] = item_id
    results[strm_path] = entry


def run_jellyfin_push(
    *,
    strm_root: str | None = None,
    jellyfin_movies_root: str = "/media/movies",
    batch_size: int = 25,
    only_with_media: bool = True,
    only_paths: list[str] | None = None,
    force_repush: bool = False,
) -> dict[str, Any]:
    status = default_push_status()
    status["running"] = True
    status["progress_text"] = "Preparing push..."
    save_push_status(status)
    started = time.perf_counter()

    try:
        url, key = _jellyfin_config()
        if not url or not key:
            status["last_error"] = "Jellyfin URL/API key missing."
            _append_log(status, status["last_error"])
            return status

        ok, msg = jellyfin_import_available()
        if not ok:
            status["last_error"] = msg
            _append_log(status, msg)
            return status

        sync_cfg = load_strm_sync_config()
        root = (strm_root or sync_cfg.get("movies_output") or STRM_OUTPUT_MOVIES_PATH or "").strip()
        store = load_duration_errors()
        results = store.get("results") or {}
        if not isinstance(results, dict):
            results = {}
        path_filter: set[str] | None = None
        if only_paths is not None:
            path_filter = set()
            for raw in only_paths:
                p = str(raw or "").strip()
                if not p:
                    continue
                path_filter.add(p)
                path_filter.add(os.path.realpath(p))

        pending: list[tuple[str, str, dict[str, Any]]] = []  # strm_path, fingerprint, item
        skipped_no_media = 0
        skipped_already = 0
        for strm_path, entry in results.items():
            if not isinstance(entry, dict):
                continue
            if path_filter is not None:
                real = os.path.realpath(str(strm_path))
                if str(strm_path) not in path_filter and real not in path_filter:
                    continue
            if only_with_media and not isinstance(entry.get("media"), dict):
                skipped_no_media += 1
                continue
            fingerprint = media_push_fingerprint(entry)
            if fingerprint is None:
                skipped_no_media += 1
                continue
            if not force_repush and _already_pushed(entry, fingerprint):
                skipped_already += 1
                continue
            jf_path = map_strm_path_to_jellyfin(
                strm_path, strm_root=root, jellyfin_root=jellyfin_movies_root
            )
            payload_item = _build_import_item(entry, jf_path)
            if payload_item is None:
                skipped_no_media += 1
                continue
            pending.append((str(strm_path), fingerprint, payload_item))

        status["skipped_no_media"] = skipped_no_media
        status["skipped_already"] = skipped_already
        status["total"] = len(pending)
        scope = f", scoped={len(path_filter or [])} paths" if path_filter is not None else ""
        force_note = ", force_repush" if force_repush else ""
        _append_log(
            status,
            f"Pushing {len(pending)} items to Jellyfin "
            f"(skipped_already={skipped_already}, skipped_no_media={skipped_no_media}, "
            f"map {root} → {jellyfin_movies_root}{scope}{force_note})",
        )
        save_push_status(status)

        if not pending:
            status["progress"] = 1.0
            status["last_run"] = datetime.now().isoformat(timespec="seconds")
            status["progress_text"] = (
                f"Nothing to push — already sent={skipped_already}, "
                f"no media={skipped_no_media}"
            )
            _append_log(status, status["progress_text"])
            return status

        headers = {
            "Authorization": f'MediaBrowser Token="{key}"',
            "Content-Type": "application/json",
        }
        apply_url = f"{url}/StrmMediaImport/Apply"
        done = 0
        dirty = False
        for offset in range(0, len(pending), max(1, batch_size)):
            batch = pending[offset : offset + max(1, batch_size)]
            items = [item for _path, _fp, item in batch]
            resp = requests.post(
                apply_url,
                headers=headers,
                json={"Items": items},
                timeout=120,
            )
            if resp.status_code >= 400:
                status["last_error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
                _append_log(status, status["last_error"])
                status["failed"] += len(batch)
            else:
                data = resp.json() if resp.content else {}
                status["applied"] += int(data.get("Applied") or 0)
                status["missing"] += int(data.get("Missing") or 0)
                status["failed"] += int(data.get("Failed") or 0)
                result_rows = [
                    row for row in (data.get("Results") or []) if isinstance(row, dict)
                ]
                if result_rows:
                    by_path = {
                        str(row.get("Path") or ""): row
                        for row in result_rows
                        if row.get("Path")
                    }
                    for strm_path, fingerprint, item in batch:
                        jf_path = str(item.get("Path") or "")
                        row = by_path.get(jf_path)
                        if row is not None and row.get("Ok") is True:
                            _mark_pushed(
                                results,
                                strm_path=strm_path,
                                fingerprint=fingerprint,
                                item_id=str(row.get("ItemId") or "") or None,
                            )
                            dirty = True
                elif (
                    int(data.get("Applied") or 0) >= len(batch)
                    and int(data.get("Failed") or 0) == 0
                    and int(data.get("Missing") or 0) == 0
                ):
                    # Older plugin builds without per-item Results.
                    for strm_path, fingerprint, _item in batch:
                        _mark_pushed(
                            results,
                            strm_path=strm_path,
                            fingerprint=fingerprint,
                            item_id=None,
                        )
                        dirty = True
            done += len(batch)
            status["progress"] = done / max(len(pending), 1)
            status["progress_text"] = (
                f"Pushed {done}/{len(pending)} "
                f"(applied={status['applied']}, missing={status['missing']}, "
                f"failed={status['failed']}, skipped_already={skipped_already})"
            )
            save_push_status(status)
            if dirty and done % (batch_size * 4) == 0:
                store["results"] = results
                save_duration_errors(store)
                dirty = False

        if dirty:
            store["results"] = results
            save_duration_errors(store)

        status["progress"] = 1.0
        status["last_run"] = datetime.now().isoformat(timespec="seconds")
        status["progress_text"] = (
            f"Done: applied={status['applied']}, missing={status['missing']}, "
            f"failed={status['failed']}, skipped_already={skipped_already}, "
            f"skipped_no_media={skipped_no_media} "
            f"({time.perf_counter() - started:.1f}s)"
        )
        _append_log(status, status["progress_text"])
        return status
    except Exception as exc:  # noqa: BLE001
        status["last_error"] = str(exc)
        _append_log(status, f"FATAL: {exc}")
        return status
    finally:
        status["running"] = False
        save_push_status(status)


def is_jellyfin_push_running() -> bool:
    status = load_push_status()
    if status.get("running"):
        return True
    with _push_lock:
        return _push_thread is not None and _push_thread.is_alive()


def start_jellyfin_push(
    *,
    strm_root: str | None = None,
    jellyfin_movies_root: str = "/media/movies",
    batch_size: int = 25,
    only_paths: list[str] | None = None,
    force_repush: bool = False,
) -> bool:
    global _push_thread
    with _push_lock:
        if _push_thread is not None and _push_thread.is_alive():
            return False
        if load_push_status().get("running"):
            return False

        def _worker() -> None:
            try:
                run_jellyfin_push(
                    strm_root=strm_root,
                    jellyfin_movies_root=jellyfin_movies_root,
                    batch_size=batch_size,
                    only_paths=only_paths,
                    force_repush=force_repush,
                )
            finally:
                global _push_thread
                with _push_lock:
                    _push_thread = None

        _push_thread = threading.Thread(
            target=_worker, name="strm-jf-push", daemon=True
        )
        _push_thread.start()
        return True

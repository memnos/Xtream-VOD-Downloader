"""Persist discarded (broken / unwanted) movie stream IDs for sync + audit.

A discarded stream is blocked from STRM recreation until its fingerprint changes
(stream_id + extension + reported size). Same id/ext/size stays blocked.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from typing import Any, Callable

from core import (
    DISCARDED_MOVIE_STREAMS_FILE,
    _save_json_file,
    catalog_title_key,
    item_file_size_bytes,
    load_json_file,
    pick_best_catalog_item,
    sort_catalog_versions,
)

_lock = threading.Lock()


def default_discarded_payload() -> dict[str, Any]:
    return {"updated_at": "", "streams": {}}


def load_discarded_streams() -> dict[str, Any]:
    data = load_json_file(DISCARDED_MOVIE_STREAMS_FILE, default_discarded_payload())
    if not isinstance(data, dict):
        return default_discarded_payload()
    streams = data.get("streams")
    if not isinstance(streams, dict):
        streams = {}
    return {"updated_at": str(data.get("updated_at") or ""), "streams": streams}


def save_discarded_streams(payload: dict[str, Any]) -> None:
    payload = {
        **payload,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_json_file(DISCARDED_MOVIE_STREAMS_FILE, payload)


def stream_fingerprint(
    *,
    stream_id: str | int,
    ext: str = "mp4",
    size: int = 0,
) -> str:
    sid = str(stream_id or "").strip()
    ext_clean = str(ext or "mp4").lstrip(".").lower() or "mp4"
    size_i = int(size or 0)
    return f"{sid}|{ext_clean}|{size_i}"


def fingerprint_from_item(item: dict[str, Any]) -> str:
    return stream_fingerprint(
        stream_id=item.get("stream_id") or "",
        ext=str(item.get("container_extension") or "mp4"),
        size=item_file_size_bytes(item),
    )


def fingerprint_from_url_and_meta(
    *,
    stream_id: str | int,
    ext: str = "mp4",
    size: int = 0,
) -> str:
    return stream_fingerprint(stream_id=stream_id, ext=ext, size=size)


def mark_movie_stream_discarded(
    *,
    stream_id: str | int,
    ext: str = "mp4",
    size: int = 0,
    name: str = "",
    reason: str = "probe_failed",
    tmdb_id: int | None = None,
    title: str = "",
    url: str = "",
    replaced_by: str | int | None = None,
) -> dict[str, Any]:
    sid = str(stream_id or "").strip()
    if not sid:
        return {}
    entry = {
        "stream_id": sid,
        "ext": str(ext or "mp4").lstrip(".").lower() or "mp4",
        "size": int(size or 0),
        "fingerprint": stream_fingerprint(stream_id=sid, ext=ext, size=size),
        "name": name or title,
        "title": title or name,
        "reason": reason,
        "tmdb_id": tmdb_id,
        "url": url,
        "discarded_at": datetime.now().isoformat(timespec="seconds"),
        "replaced_by": str(replaced_by) if replaced_by is not None else None,
    }
    with _lock:
        payload = load_discarded_streams()
        payload["streams"][sid] = entry
        save_discarded_streams(payload)
    return entry


def set_discarded_replaced_by(stream_id: str | int, replaced_by: str | int) -> bool:
    sid = str(stream_id or "").strip()
    if not sid:
        return False
    with _lock:
        payload = load_discarded_streams()
        entry = payload["streams"].get(sid)
        if not isinstance(entry, dict):
            return False
        entry["replaced_by"] = str(replaced_by)
        entry["replaced_at"] = datetime.now().isoformat(timespec="seconds")
        payload["streams"][sid] = entry
        save_discarded_streams(payload)
    return True


def clear_discarded_stream(stream_id: str | int) -> bool:
    sid = str(stream_id or "").strip()
    if not sid:
        return False
    with _lock:
        payload = load_discarded_streams()
        if sid not in payload["streams"]:
            return False
        del payload["streams"][sid]
        save_discarded_streams(payload)
    return True


def is_movie_stream_discarded(
    item: dict[str, Any] | None = None,
    *,
    stream_id: str | int | None = None,
    ext: str = "mp4",
    size: int = 0,
    store: dict[str, Any] | None = None,
    auto_clear_changed: bool = True,
) -> bool:
    """True if this exact stream fingerprint is still blocked.

    If the catalog entry changed (different ext/size for same id), clears the
    discard entry (when auto_clear_changed) and returns False.
    """
    if item is not None:
        sid = str(item.get("stream_id") or "").strip()
        ext = str(item.get("container_extension") or "mp4")
        size = item_file_size_bytes(item)
    else:
        sid = str(stream_id or "").strip()
    if not sid:
        return False

    payload = store if store is not None else load_discarded_streams()
    streams = payload.get("streams") or {}
    entry = streams.get(sid)
    if not isinstance(entry, dict):
        return False

    current_fp = stream_fingerprint(stream_id=sid, ext=ext, size=size)
    stored_fp = str(entry.get("fingerprint") or "")
    if stored_fp and current_fp != stored_fp:
        if auto_clear_changed:
            clear_discarded_stream(sid)
            if store is not None:
                streams = store.get("streams")
                if isinstance(streams, dict):
                    streams.pop(sid, None)
        return False
    return True


def discarded_skip_predicate(
    store: dict[str, Any] | None = None,
) -> Callable[[dict], bool]:
    """Predicate for pick_best_catalog_item(skip_item=...): True = skip."""
    payload = store if store is not None else load_discarded_streams()

    def _skip(item: dict) -> bool:
        return is_movie_stream_discarded(item, store=payload, auto_clear_changed=True)

    return _skip


def pick_best_non_discarded_catalog_item(
    items: list[dict],
    *,
    allow_4k: bool = False,
    name_key: str = "name",
    probes: dict[str, dict] | None = None,
    store: dict[str, Any] | None = None,
) -> dict | None:
    return pick_best_catalog_item(
        items,
        allow_4k=allow_4k,
        name_key=name_key,
        probes=probes,
        skip_item=discarded_skip_predicate(store),
    )


def iter_alternate_catalog_versions(
    items: list[dict],
    *,
    exclude_stream_ids: set[str] | None = None,
    allow_4k: bool = False,
    name_key: str = "name",
    probes: dict[str, dict] | None = None,
    store: dict[str, Any] | None = None,
) -> list[dict]:
    """Return quality-sorted versions, skipping discarded / excluded ids."""
    exclude = {str(x) for x in (exclude_stream_ids or set())}
    payload = store if store is not None else load_discarded_streams()
    ordered = sort_catalog_versions(items, probes, name_key=name_key)
    out: list[dict] = []
    for item in ordered:
        sid = str(item.get("stream_id") or "")
        if sid and sid in exclude:
            continue
        if not allow_4k:
            from core import is_4k_title

            if is_4k_title(str(item.get(name_key) or "")):
                continue
        if is_movie_stream_discarded(item, store=payload, auto_clear_changed=True):
            continue
        out.append(item)
    return out


def catalog_group_for_title(
    groups: dict[str, list[dict]],
    title: str,
) -> list[dict]:
    key = catalog_title_key(title or "")
    if not key:
        return []
    return list(groups.get(key) or [])

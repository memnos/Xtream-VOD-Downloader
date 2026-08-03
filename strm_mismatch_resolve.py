"""Resolve duration-mismatch movies using provider VOD title + probed duration.

Folder year / NFO / current tmdbid are treated as untrusted (they come from the
wrong match). The trusted signals are:
  - probed stream duration
  - Xtream get_vod_info name (provider title before/without our folder rename)

After analysis, high-confidence candidates can be applied on disk (rename folder
+ .strm, rewrite .nfo) and Jellyfin is notified so the item is re-identified.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from xml.dom import minidom

from core import (
    DATA_DIR,
    STRM_OUTPUT_MOVIES_PATH,
    _save_json_file,
    build_movie_strm_path_tmdb,
    finalize_strm_path,
    load_auto_download_config,
    load_credentials,
    load_json_file,
    load_strm_sync_config,
    prepare_strm_dir,
    request_xtream_api,
    tmdb_movie_folder_name,
)
from strm_duration_audit import (
    DEFAULT_THRESHOLD_SEC,
    _build_tmdb_client,
    load_duration_errors,
    save_duration_errors,
)
from strm_jellyfin_push import map_strm_path_to_jellyfin, run_jellyfin_push
from tmdb import clean_title, extract_year

MISMATCH_RESOLVE_STATUS_FILE = os.environ.get(
    "STRM_MISMATCH_RESOLVE_STATUS_FILE",
    os.path.join(DATA_DIR, "strm_mismatch_resolve_status.json"),
)
MISMATCH_RESOLVE_RESULTS_FILE = os.environ.get(
    "STRM_MISMATCH_RESOLVE_RESULTS_FILE",
    os.path.join(DATA_DIR, "strm_mismatch_candidates.json"),
)
MISMATCH_APPLY_STATUS_FILE = os.environ.get(
    "STRM_MISMATCH_APPLY_STATUS_FILE",
    os.path.join(DATA_DIR, "strm_mismatch_apply_status.json"),
)

DEFAULT_TITLE_SIMILARITY = 0.55
DEFAULT_APPLY_MIN_SIMILARITY = 0.80
DEFAULT_MAX_CANDIDATES = 8

_TMDB_TAG_RE = re.compile(r"\s*\[tmdbid-\d+\]\s*", re.IGNORECASE)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tbn"}

_resolve_lock = threading.Lock()
_resolve_thread: threading.Thread | None = None
_resolve_stop = threading.Event()

_apply_lock = threading.Lock()
_apply_thread: threading.Thread | None = None
_apply_stop = threading.Event()

_vod_info_cache: dict[str, dict[str, Any] | None] = {}
_vod_info_lock = threading.Lock()


def default_resolve_status() -> dict[str, Any]:
    return {
        "running": False,
        "progress": 0.0,
        "progress_text": "",
        "total": 0,
        "checked": 0,
        "with_candidate": 0,
        "no_candidate": 0,
        "last_error": "",
        "last_run": "",
        "log": [],
    }


def default_apply_status() -> dict[str, Any]:
    return {
        "running": False,
        "progress": 0.0,
        "progress_text": "",
        "total": 0,
        "applied": 0,
        "skipped": 0,
        "failed": 0,
        "last_error": "",
        "last_run": "",
        "log": [],
    }


def load_resolve_status() -> dict[str, Any]:
    data = load_json_file(MISMATCH_RESOLVE_STATUS_FILE, default_resolve_status())
    if not isinstance(data, dict):
        return default_resolve_status()
    merged = {**default_resolve_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


def save_resolve_status(status: dict[str, Any]) -> None:
    _save_json_file(MISMATCH_RESOLVE_STATUS_FILE, status)


def load_apply_status() -> dict[str, Any]:
    data = load_json_file(MISMATCH_APPLY_STATUS_FILE, default_apply_status())
    if not isinstance(data, dict):
        return default_apply_status()
    merged = {**default_apply_status(), **data}
    log = merged.get("log", [])
    merged["log"] = log if isinstance(log, list) else []
    return merged


def save_apply_status(status: dict[str, Any]) -> None:
    _save_json_file(MISMATCH_APPLY_STATUS_FILE, status)


def _append_log(status: dict[str, Any], message: str, *, limit: int = 100) -> None:
    log = status.setdefault("log", [])
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {message}")
    status["log"] = log[-limit:]


def _title_similarity(a: str, b: str) -> float:
    left = clean_title(a).lower()
    right = clean_title(b).lower()
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def strip_folder_metadata(title: str) -> str:
    """Remove [tmdbid-…] (and rely on clean_title for year) — folder metadata is untrusted."""
    text = _TMDB_TAG_RE.sub(" ", title or "")
    return clean_title(text)


def fetch_vod_info(stream_id: str | int | None) -> dict[str, Any] | None:
    """Return Xtream get_vod_info payload (cached)."""
    if stream_id is None or str(stream_id).strip() == "":
        return None
    key = str(stream_id).strip()
    with _vod_info_lock:
        if key in _vod_info_cache:
            return _vod_info_cache[key]
    creds = load_credentials()
    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    password = str(creds.get("password") or "").strip()
    if not host or not user or not password:
        with _vod_info_lock:
            _vod_info_cache[key] = None
        return None
    try:
        data = request_xtream_api(
            host,
            {
                "username": user,
                "password": password,
                "action": "get_vod_info",
                "vod_id": key,
            },
            timeout=30,
        )
    except Exception:
        data = None
    if not isinstance(data, dict):
        with _vod_info_lock:
            _vod_info_cache[key] = None
        return None
    with _vod_info_lock:
        _vod_info_cache[key] = data
    return data


def provider_title_from_vod(stream_id: str | int | None) -> str:
    info = fetch_vod_info(stream_id)
    if not info:
        return ""
    movie = info.get("movie_data") if isinstance(info.get("movie_data"), dict) else {}
    detail = info.get("info") if isinstance(info.get("info"), dict) else {}
    for source in (movie, detail):
        for key in ("name", "title", "o_name"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _search_queries_from_provider(provider_title: str) -> list[str]:
    """Build search strings from provider title only — never force year filter."""
    cleaned = clean_title(provider_title)
    queries: list[str] = []
    if cleaned:
        queries.append(cleaned)
        for sep in (" - ", ": ", " – ", " — "):
            if sep in cleaned:
                head = cleaned.split(sep, 1)[0].strip()
                if head and head.lower() != cleaned.lower():
                    queries.append(head)
                break
    # Deduplicate
    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def find_runtime_candidates(
    entry: dict[str, Any],
    *,
    tmdb_client: Any,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    min_title_similarity: float = DEFAULT_TITLE_SIMILARITY,
    max_search_results: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Return analysis for one mismatch entry using provider title + probed duration."""
    folder_title = str(entry.get("title") or "")
    probed = int(entry.get("probed_duration_sec") or 0)
    current_id = entry.get("tmdb_id")
    try:
        current_id_int = int(current_id) if current_id is not None else None
    except (TypeError, ValueError):
        current_id_int = None
    current_runtime = entry.get("tmdb_runtime_sec")
    try:
        current_runtime_int = int(current_runtime) if current_runtime is not None else None
    except (TypeError, ValueError):
        current_runtime_int = None

    provider_title = provider_title_from_vod(entry.get("stream_id"))
    search_title = provider_title or strip_folder_metadata(folder_title)

    result: dict[str, Any] = {
        "strm_path": entry.get("strm_path") or "",
        "title": folder_title,
        "provider_title": provider_title,
        "search_title": search_title,
        "stream_id": entry.get("stream_id"),
        "current_tmdb_id": current_id_int,
        "current_tmdb_runtime_sec": current_runtime_int,
        "probed_duration_sec": probed if probed > 0 else None,
        "current_delta_sec": entry.get("delta_sec"),
        "candidates": [],
        "best": None,
        "applied": False,
    }
    if probed <= 0 or not search_title.strip():
        result["reason"] = "missing_probed_or_title"
        return result

    # Soft hint only: year inside provider string (not folder). Never used as API filter.
    provider_year = extract_year(provider_title) if provider_title else None

    hits: dict[int, dict[str, Any]] = {}
    for query in _search_queries_from_provider(search_title):
        rows = tmdb_client.search_movie_results(
            query,
            year=None,
            max_results=max_search_results,
            use_year_filter=False,
        )
        if not rows:
            continue
        for row in rows:
            tid = int(row["tmdb_id"])
            if current_id_int is not None and tid == current_id_int:
                continue
            sim = max(
                _title_similarity(search_title, str(row.get("title") or "")),
                _title_similarity(search_title, str(row.get("original_title") or "")),
                _title_similarity(query, str(row.get("title") or "")),
                _title_similarity(query, str(row.get("original_title") or "")),
            )
            prev = hits.get(tid)
            if prev is None or sim > float(prev.get("title_similarity") or 0):
                hits[tid] = {
                    "tmdb_id": tid,
                    "title": row.get("title") or "",
                    "original_title": row.get("original_title") or "",
                    "year": row.get("year"),
                    "title_similarity": round(sim, 3),
                    "query": query,
                }

    candidates: list[dict[str, Any]] = []
    for tid, meta in hits.items():
        if float(meta.get("title_similarity") or 0) < min_title_similarity:
            continue
        runtime_min = tmdb_client.get_movie_runtime(tid)
        if runtime_min is None:
            continue
        runtime_sec = int(runtime_min) * 60
        delta = runtime_sec - probed
        if abs(delta) > threshold_sec:
            continue
        year_bonus = 0
        if provider_year and meta.get("year") == provider_year:
            year_bonus = 1
        candidates.append(
            {
                **meta,
                "tmdb_runtime_sec": runtime_sec,
                "delta_vs_probed_sec": delta,
                "abs_delta_sec": abs(delta),
                "year_bonus": year_bonus,
            }
        )

    candidates.sort(
        key=lambda c: (
            -int(c.get("year_bonus") or 0),
            -float(c.get("title_similarity") or 0),
            int(c.get("abs_delta_sec") or 10**9),
        )
    )
    result["candidates"] = candidates[:5]
    if candidates:
        result["best"] = candidates[0]
        result["reason"] = "candidate_found"
        best = candidates[0]
        sim = float(best.get("title_similarity") or 0)
        core_provider = clean_title(search_title).lower()
        core_title = clean_title(str(best.get("title") or "")).lower()
        core_original = clean_title(str(best.get("original_title") or "")).lower()
        exact_core = bool(core_provider) and (
            core_provider == core_title or core_provider == core_original
        )
        # Provider year is often wrong too — never require it.
        # Apply only on exact cleaned-title match or very high similarity.
        result["apply_ready"] = bool(
            exact_core or sim >= max(DEFAULT_APPLY_MIN_SIMILARITY, 0.90)
        )
        result["exact_core"] = exact_core
        result["provider_year"] = provider_year
    else:
        result["reason"] = "no_runtime_match"
        result["apply_ready"] = False
        result["exact_core"] = False
        result["provider_year"] = provider_year
    return result


def iter_mismatch_entries(
    results: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    store = results if results is not None else (load_duration_errors().get("results") or {})
    out: list[dict[str, Any]] = []
    for path, entry in (store or {}).items():
        if not isinstance(entry, dict):
            continue
        # Successfully retagged movies must never re-enter mismatch analysis.
        if entry.get("mismatch_resolved") or entry.get("retagged_at"):
            continue
        if entry.get("status") != "mismatch":
            continue
        row = dict(entry)
        row.setdefault("strm_path", path)
        out.append(row)
    out.sort(key=lambda e: abs(int(e.get("delta_sec") or 0)), reverse=True)
    return out


def run_mismatch_resolve(
    *,
    limit: int | None = None,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    min_title_similarity: float = DEFAULT_TITLE_SIMILARITY,
    config: dict | None = None,
) -> dict[str, Any]:
    """Analyze mismatches and write candidate report. Does not rename STRMs."""
    _resolve_stop.clear()
    status = default_resolve_status()
    status["running"] = True
    status["progress_text"] = "Loading mismatches..."
    save_resolve_status(status)
    started = time.perf_counter()

    try:
        cfg = config or load_strm_sync_config()
        tmdb_client = _build_tmdb_client(cfg)
        if tmdb_client is None:
            status["last_error"] = "TMDB API key missing."
            _append_log(status, status["last_error"])
            return status

        mismatches = iter_mismatch_entries()
        if limit is not None and limit > 0:
            mismatches = mismatches[: int(limit)]
        status["total"] = len(mismatches)
        _append_log(
            status,
            f"Analyzing {len(mismatches)} mismatches via provider VOD title "
            f"(threshold ±{threshold_sec}s, title≥{min_title_similarity:.2f}, no folder-year filter)",
        )
        save_resolve_status(status)

        findings: list[dict[str, Any]] = []
        with_candidate = 0
        no_candidate = 0
        for idx, entry in enumerate(mismatches, start=1):
            if _resolve_stop.is_set():
                _append_log(status, "Stopped by request")
                break
            analysis = find_runtime_candidates(
                entry,
                tmdb_client=tmdb_client,
                threshold_sec=threshold_sec,
                min_title_similarity=min_title_similarity,
            )
            findings.append(analysis)
            if analysis.get("best"):
                with_candidate += 1
            else:
                no_candidate += 1
            status["checked"] = idx
            status["with_candidate"] = with_candidate
            status["no_candidate"] = no_candidate
            status["progress"] = idx / max(len(mismatches), 1)
            label = analysis.get("provider_title") or entry.get("title") or ""
            status["progress_text"] = (
                f"Mismatch resolve {idx}/{len(mismatches)} "
                f"(candidates={with_candidate}, none={no_candidate}) — {label}"
            )
            if idx == 1 or idx % 10 == 0 or analysis.get("best"):
                save_resolve_status(status)

        tmdb_client.save_cache()
        apply_ready = sum(1 for f in findings if f.get("apply_ready"))
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "threshold_sec": threshold_sec,
            "min_title_similarity": min_title_similarity,
            "apply_min_similarity": DEFAULT_APPLY_MIN_SIMILARITY,
            "summary": {
                "checked": len(findings),
                "with_candidate": with_candidate,
                "no_candidate": no_candidate,
                "apply_ready": apply_ready,
            },
            "findings": findings,
        }
        _save_json_file(MISMATCH_RESOLVE_RESULTS_FILE, payload)

        status["progress"] = 1.0
        status["last_run"] = datetime.now().isoformat(timespec="seconds")
        status["progress_text"] = (
            f"Done: {with_candidate} with candidate ({apply_ready} apply-ready), "
            f"{no_candidate} without "
            f"({time.perf_counter() - started:.1f}s) → {MISMATCH_RESOLVE_RESULTS_FILE}"
        )
        _append_log(status, status["progress_text"])
        return status
    except Exception as exc:  # noqa: BLE001
        status["last_error"] = str(exc)
        _append_log(status, f"FATAL: {exc}")
        return status
    finally:
        status["running"] = False
        save_resolve_status(status)


def is_mismatch_resolve_running() -> bool:
    with _resolve_lock:
        if _resolve_thread is not None and _resolve_thread.is_alive():
            return True
    status = load_resolve_status()
    if status.get("running"):
        status["running"] = False
        save_resolve_status(status)
    return False


def start_mismatch_resolve(
    *,
    limit: int | None = None,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    min_title_similarity: float = DEFAULT_TITLE_SIMILARITY,
    config: dict | None = None,
) -> bool:
    global _resolve_thread
    with _resolve_lock:
        if _resolve_thread is not None and _resolve_thread.is_alive():
            return False
        if load_resolve_status().get("running"):
            return False

        def _worker() -> None:
            try:
                run_mismatch_resolve(
                    limit=limit,
                    threshold_sec=threshold_sec,
                    min_title_similarity=min_title_similarity,
                    config=config,
                )
            finally:
                global _resolve_thread
                with _resolve_lock:
                    _resolve_thread = None

        _resolve_thread = threading.Thread(
            target=_worker, name="strm-mismatch-resolve", daemon=True
        )
        _resolve_thread.start()
        return True


def write_movie_nfo(
    nfo_path: str,
    *,
    title: str,
    year: int | None,
    tmdb_id: int | str,
    runtime_min: int | None = None,
) -> None:
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = title
    if year:
        ET.SubElement(root, "year").text = str(int(year))
    uid = ET.SubElement(root, "uniqueid", type="tmdb", default="true")
    uid.text = str(tmdb_id)
    ET.SubElement(root, "tmdbid").text = str(tmdb_id)
    if runtime_min and int(runtime_min) > 0:
        ET.SubElement(root, "runtime").text = str(int(runtime_min))
    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ", encoding="utf-8")
    # minidom adds XML declaration; write bytes
    prepare_strm_dir(os.path.dirname(nfo_path))
    with open(nfo_path, "wb") as fh:
        fh.write(pretty)
    finalize_strm_path(nfo_path)


def _delete_sidecar_images(folder: str) -> list[str]:
    removed: list[str] = []
    if not os.path.isdir(folder):
        return removed
    for name in os.listdir(folder):
        ext = os.path.splitext(name)[1].lower()
        if ext not in _IMAGE_EXTS:
            continue
        path = os.path.join(folder, name)
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def _rename_movie_folder(
    old_strm: str,
    *,
    title: str,
    year: int | None,
    tmdb_id: int | str,
    movies_root: str,
) -> dict[str, Any]:
    old_strm = os.path.realpath(old_strm)
    old_folder = os.path.dirname(old_strm)
    new_folder, new_strm = build_movie_strm_path_tmdb(title, year, tmdb_id, movies_root)
    new_folder = os.path.realpath(new_folder) if os.path.exists(new_folder) else new_folder
    result = {
        "ok": False,
        "old_strm": old_strm,
        "old_folder": old_folder,
        "new_strm": new_strm,
        "new_folder": new_folder,
        "detail": "",
    }
    if not os.path.isfile(old_strm):
        result["detail"] = "old_strm_missing"
        return result
    if os.path.realpath(old_folder) == os.path.realpath(new_folder) or old_folder == new_folder:
        # Same folder name — still ensure strm basename + nfo
        if os.path.basename(old_strm) != os.path.basename(new_strm):
            if os.path.exists(new_strm):
                result["detail"] = "new_strm_exists"
                return result
            os.rename(old_strm, new_strm)
            finalize_strm_path(new_strm)
        result["ok"] = True
        result["detail"] = "same_folder"
        result["new_strm"] = new_strm if os.path.isfile(new_strm) else old_strm
        return result
    if os.path.exists(new_folder):
        result["detail"] = "destination_exists"
        return result

    parent = os.path.dirname(new_folder)
    prepare_strm_dir(parent)
    shutil.move(old_folder, new_folder)
    # After move, strm may still have old basename.
    moved_strm = os.path.join(new_folder, os.path.basename(old_strm))
    if os.path.isfile(moved_strm) and moved_strm != new_strm:
        if os.path.exists(new_strm):
            result["detail"] = "new_strm_exists_after_move"
            result["new_strm"] = moved_strm
            result["ok"] = True
            return result
        os.rename(moved_strm, new_strm)
    # Rename leftover .nfo with old basename
    old_nfo = os.path.splitext(moved_strm)[0] + ".nfo"
    if os.path.isfile(old_nfo):
        try:
            os.remove(old_nfo)
        except OSError:
            pass
    finalize_strm_path(new_strm)
    finalize_strm_path(new_folder, fix_children=True)
    result["ok"] = True
    result["detail"] = "renamed"
    result["new_strm"] = new_strm
    result["new_folder"] = new_folder
    return result


def _notify_jellyfin_path_change(
    *,
    old_folder: str,
    new_folder: str,
    movies_root: str,
    jellyfin_movies_root: str,
    new_tmdb_id: int | str,
    new_strm: str,
) -> str:
    auto = load_auto_download_config()
    if not auto.get("jellyfin_enabled"):
        return "jellyfin_disabled"
    url = str(auto.get("jellyfin_url") or "").strip()
    key = str(auto.get("jellyfin_api_key") or "").strip()
    if not url or not key:
        return "jellyfin_not_configured"
    try:
        from emby_watcher import MediaServerClient

        client = MediaServerClient(url, key, "jellyfin")
        old_jf = map_strm_path_to_jellyfin(
            old_folder, strm_root=movies_root, jellyfin_root=jellyfin_movies_root
        )
        new_jf = map_strm_path_to_jellyfin(
            new_folder, strm_root=movies_root, jellyfin_root=jellyfin_movies_root
        )
        updates = []
        if old_jf and old_jf != new_jf:
            updates.append({"Path": old_jf, "UpdateType": "Deleted"})
        if new_jf:
            updates.append({"Path": new_jf, "UpdateType": "Created"})
        client.notify_library_paths(updates)
        time.sleep(2)
        items = client.find_movies_by_tmdb_id(new_tmdb_id)
        refreshed = 0
        for item in items:
            item_id = str(item.get("Id") or "")
            if not item_id:
                continue
            try:
                client.refresh_item_metadata(item_id, replace_all=True)
                refreshed += 1
            except Exception:
                continue
        # Push probed media info onto the new path.
        try:
            run_jellyfin_push(
                strm_root=movies_root,
                jellyfin_movies_root=jellyfin_movies_root,
                only_paths=[new_strm],
                force_repush=True,
            )
        except Exception as exc:  # noqa: BLE001
            return f"paths_notified refreshed={refreshed} push_err={exc}"
        return f"paths_notified refreshed={refreshed}"
    except Exception as exc:  # noqa: BLE001
        return f"jellyfin_error:{exc}"


def apply_one_finding(
    finding: dict[str, Any],
    *,
    movies_root: str,
    jellyfin_movies_root: str = "/media/movies",
    min_similarity: float = DEFAULT_APPLY_MIN_SIMILARITY,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
) -> dict[str, Any]:
    """Rename on disk + rewrite NFO + update audit store + notify JF."""
    best = finding.get("best") if isinstance(finding.get("best"), dict) else None
    out: dict[str, Any] = {"ok": False, "detail": "", "finding": finding}
    if not best:
        out["detail"] = "no_candidate"
        return out
    sim = float(best.get("title_similarity") or 0)
    provider_title = str(finding.get("provider_title") or finding.get("search_title") or "")
    core_provider = clean_title(provider_title).lower()
    core_title = clean_title(str(best.get("title") or "")).lower()
    core_original = clean_title(str(best.get("original_title") or "")).lower()
    exact_core = bool(core_provider) and (
        core_provider == core_title or core_provider == core_original
    )
    if not exact_core and sim < max(min_similarity, 0.90):
        out["detail"] = f"confidence_too_low:sim={sim},exact_core={exact_core}"
        return out
    old_strm = str(finding.get("strm_path") or "")
    if not old_strm or not os.path.isfile(old_strm):
        out["detail"] = "strm_missing"
        return out

    title = str(best.get("title") or "").strip()
    year = best.get("year")
    try:
        year_int = int(year) if year is not None else None
    except (TypeError, ValueError):
        year_int = None
    tmdb_id = best.get("tmdb_id")
    runtime_sec = int(best.get("tmdb_runtime_sec") or 0)
    runtime_min = (runtime_sec // 60) if runtime_sec > 0 else None

    rename = _rename_movie_folder(
        old_strm,
        title=title,
        year=year_int,
        tmdb_id=tmdb_id,
        movies_root=movies_root,
    )
    if not rename.get("ok"):
        out["detail"] = rename.get("detail") or "rename_failed"
        out["rename"] = rename
        return out

    new_strm = str(rename.get("new_strm") or "")
    new_folder = str(rename.get("new_folder") or os.path.dirname(new_strm))
    old_folder = str(rename.get("old_folder") or os.path.dirname(old_strm))
    _delete_sidecar_images(new_folder)

    nfo_path = os.path.splitext(new_strm)[0] + ".nfo"
    # Remove any other .nfo in folder
    for name in os.listdir(new_folder) if os.path.isdir(new_folder) else []:
        if name.lower().endswith(".nfo"):
            try:
                os.remove(os.path.join(new_folder, name))
            except OSError:
                pass
    write_movie_nfo(
        nfo_path,
        title=title,
        year=year_int,
        tmdb_id=tmdb_id,
        runtime_min=runtime_min,
    )

    # Update duration audit store: move key + mark ok if within threshold.
    store = load_duration_errors()
    results = store.get("results") if isinstance(store.get("results"), dict) else {}
    old_keys = {old_strm, os.path.realpath(old_strm), finding.get("strm_path")}
    entry = None
    for key in list(results.keys()):
        if key in old_keys or os.path.realpath(str(key)) in {
            os.path.realpath(old_strm),
            os.path.realpath(str(finding.get("strm_path") or old_strm)),
        }:
            entry = results.pop(key)
            break
    if not isinstance(entry, dict):
        entry = {}
    probed = int(finding.get("probed_duration_sec") or entry.get("probed_duration_sec") or 0)
    new_delta = (probed - runtime_sec) if probed and runtime_sec else None
    now_iso = datetime.now().isoformat(timespec="seconds")
    entry.update(
        {
            "strm_path": new_strm,
            "title": tmdb_movie_folder_name(title, year_int, tmdb_id),
            "tmdb_id": int(tmdb_id) if tmdb_id is not None else None,
            "tmdb_runtime_sec": runtime_sec or None,
            "probed_duration_sec": probed or entry.get("probed_duration_sec"),
            "delta_sec": new_delta,
            # Retag applied successfully: leave the mismatch queue permanently.
            "status": "ok",
            "reason": "retagged_from_mismatch",
            "mismatch_resolved": True,
            "retagged_at": now_iso,
            "checked_at": now_iso,
            "retagged_from_tmdb_id": finding.get("current_tmdb_id"),
            "retagged_provider_title": finding.get("provider_title") or "",
            "jf_pushed_fingerprint": "",
            "jf_pushed_at": "",
        }
    )
    results[new_strm] = entry
    store["results"] = results
    save_duration_errors(store)

    jf_note = _notify_jellyfin_path_change(
        old_folder=old_folder,
        new_folder=new_folder,
        movies_root=movies_root,
        jellyfin_movies_root=jellyfin_movies_root,
        new_tmdb_id=tmdb_id,
        new_strm=new_strm,
    )

    out["ok"] = True
    out["detail"] = rename.get("detail") or "applied"
    out["new_strm"] = new_strm
    out["new_folder"] = new_folder
    out["nfo"] = nfo_path
    out["jellyfin"] = jf_note
    out["new_status"] = entry.get("status")
    return out


def load_resolve_findings() -> list[dict[str, Any]]:
    data = load_json_file(MISMATCH_RESOLVE_RESULTS_FILE, {})
    if not isinstance(data, dict):
        return []
    findings = data.get("findings") or []
    return [f for f in findings if isinstance(f, dict)]


def run_mismatch_apply(
    *,
    min_similarity: float = DEFAULT_APPLY_MIN_SIMILARITY,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    movies_root: str | None = None,
    jellyfin_movies_root: str = "/media/movies",
    only_apply_ready: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """Apply high-confidence findings from the last analysis report."""
    _apply_stop.clear()
    status = default_apply_status()
    status["running"] = True
    status["progress_text"] = "Loading analysis report..."
    save_apply_status(status)
    started = time.perf_counter()

    try:
        cfg = load_strm_sync_config()
        root = (
            movies_root
            or cfg.get("movies_output")
            or STRM_OUTPUT_MOVIES_PATH
            or ""
        ).strip()
        findings = load_resolve_findings()
        pending = []
        for finding in findings:
            best = finding.get("best")
            if not isinstance(best, dict):
                continue
            if only_apply_ready and not finding.get("apply_ready"):
                sim = float(best.get("title_similarity") or 0)
                if sim < min_similarity:
                    continue
            elif float(best.get("title_similarity") or 0) < min_similarity:
                continue
            if finding.get("applied"):
                continue
            pending.append(finding)
        if limit is not None and limit > 0:
            pending = pending[: int(limit)]

        status["total"] = len(pending)
        _append_log(
            status,
            f"Applying {len(pending)} retags "
            f"(min_sim≥{min_similarity:.2f}, root={root})",
        )
        save_apply_status(status)

        applied = 0
        skipped = 0
        failed = 0
        report = load_json_file(MISMATCH_RESOLVE_RESULTS_FILE, {})
        report_findings = list(report.get("findings") or []) if isinstance(report, dict) else []

        for idx, finding in enumerate(pending, start=1):
            if _apply_stop.is_set():
                _append_log(status, "Stopped by request")
                break
            result = apply_one_finding(
                finding,
                movies_root=root,
                jellyfin_movies_root=jellyfin_movies_root,
                min_similarity=min_similarity,
                threshold_sec=threshold_sec,
            )
            label = finding.get("provider_title") or finding.get("title") or ""
            if result.get("ok"):
                applied += 1
                finding["applied"] = True
                finding["applied_at"] = datetime.now().isoformat(timespec="seconds")
                finding["applied_new_strm"] = result.get("new_strm")
                _append_log(
                    status,
                    f"OK {label} → tmdb {finding.get('best', {}).get('tmdb_id')} "
                    f"({result.get('jellyfin')})",
                )
            else:
                detail = str(result.get("detail") or "failed")
                if detail.startswith("similarity") or detail in {
                    "no_candidate",
                    "destination_exists",
                    "strm_missing",
                }:
                    skipped += 1
                else:
                    failed += 1
                _append_log(status, f"SKIP/FAIL {label}: {detail}")

            # Mirror applied flag onto saved report by strm_path.
            old_path = finding.get("strm_path")
            for row in report_findings:
                if isinstance(row, dict) and row.get("strm_path") == old_path:
                    row.update(
                        {
                            "applied": bool(result.get("ok")),
                            "apply_detail": result.get("detail"),
                            "applied_new_strm": result.get("new_strm"),
                        }
                    )
                    break

            status["applied"] = applied
            status["skipped"] = skipped
            status["failed"] = failed
            status["progress"] = idx / max(len(pending), 1)
            status["progress_text"] = (
                f"Apply {idx}/{len(pending)} "
                f"(ok={applied}, skipped={skipped}, failed={failed}) — {label}"
            )
            save_apply_status(status)

        if isinstance(report, dict):
            report["findings"] = report_findings
            report["apply_updated_at"] = datetime.now().isoformat(timespec="seconds")
            report["apply_summary"] = {
                "applied": applied,
                "skipped": skipped,
                "failed": failed,
            }
            _save_json_file(MISMATCH_RESOLVE_RESULTS_FILE, report)

        status["progress"] = 1.0
        status["last_run"] = datetime.now().isoformat(timespec="seconds")
        status["progress_text"] = (
            f"Done apply: ok={applied}, skipped={skipped}, failed={failed} "
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
        save_apply_status(status)


def is_mismatch_apply_running() -> bool:
    with _apply_lock:
        if _apply_thread is not None and _apply_thread.is_alive():
            return True
    status = load_apply_status()
    if status.get("running"):
        status["running"] = False
        save_apply_status(status)
    return False


def start_mismatch_apply(
    *,
    min_similarity: float = DEFAULT_APPLY_MIN_SIMILARITY,
    threshold_sec: int = DEFAULT_THRESHOLD_SEC,
    movies_root: str | None = None,
    jellyfin_movies_root: str = "/media/movies",
    limit: int | None = None,
) -> bool:
    global _apply_thread
    with _apply_lock:
        if _apply_thread is not None and _apply_thread.is_alive():
            return False
        if load_apply_status().get("running"):
            return False

        def _worker() -> None:
            try:
                run_mismatch_apply(
                    min_similarity=min_similarity,
                    threshold_sec=threshold_sec,
                    movies_root=movies_root,
                    jellyfin_movies_root=jellyfin_movies_root,
                    limit=limit,
                )
            finally:
                global _apply_thread
                with _apply_lock:
                    _apply_thread = None

        _apply_thread = threading.Thread(
            target=_worker, name="strm-mismatch-apply", daemon=True
        )
        _apply_thread.start()
        return True

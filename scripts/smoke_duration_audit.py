#!/usr/bin/env python3
"""Smoke test for strm duration audit."""
from __future__ import annotations

import os
import time
from datetime import datetime

from core import STRM_DURATION_ERRORS_FILE, _save_json_file, load_strm_sync_config
from strm_duration_audit import (
    _audit_one,
    _build_tmdb_client,
    iter_movie_strm_files,
)


def main() -> None:
    cfg = load_strm_sync_config()
    root = cfg.get("movies_output") or "/strm/movies"
    print("movies_output", root)
    paths = iter_movie_strm_files(root)
    print("movie strm with tmdbid:", len(paths))

    hits = [p for p in paths if "The Terminal (2004)" in p or "tmdbid-594]" in p]
    print("terminal hits", hits[:3])

    client = _build_tmdb_client(cfg)
    if client is None:
        raise SystemExit("no tmdb client")

    if hits:
        res = _audit_one(hits[0], tmdb_client=client, threshold_sec=300, probe_timeout=45)
        print("The Terminal result:", res)

    sample: list[str] = []
    if hits:
        sample.append(hits[0])
    mid = len(paths) // 2
    for p in paths[0:10] + paths[mid : mid + 10]:
        if p not in sample:
            sample.append(p)
        if len(sample) >= 25:
            break

    errors = []
    stats = {"ok": 0, "mismatch": 0, "probe_failed": 0, "no_runtime": 0, "no_tmdb": 0}
    t0 = time.perf_counter()
    for i, path in enumerate(sample, 1):
        result = _audit_one(path, tmdb_client=client, threshold_sec=300, probe_timeout=45)
        status = result["status"]
        stats[status] = stats.get(status, 0) + 1
        if result.get("error"):
            errors.append(result["error"])
        folder = os.path.basename(os.path.dirname(path))
        print(f"[{i}/{len(sample)}] {status}: {folder}")

    client.save_cache()
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "threshold_sec": 300,
        "movies_root": root,
        "summary": {"total": len(sample), "checked": len(sample), **stats},
        "errors": sorted(
            errors, key=lambda e: abs(int(e.get("delta_sec") or 0)), reverse=True
        ),
        "note": "smoke sample (not full library)",
    }
    _save_json_file(STRM_DURATION_ERRORS_FILE, payload)
    print("STATS", stats, "errors", len(errors), f"elapsed={time.perf_counter() - t0:.1f}s")
    for err in errors[:10]:
        print(
            " ERR",
            err.get("reason"),
            err.get("title"),
            "tmdb",
            err.get("tmdb_id"),
            "runtime",
            err.get("tmdb_runtime_sec"),
            "probed",
            err.get("probed_duration_sec"),
            "delta",
            err.get("delta_sec"),
        )


if __name__ == "__main__":
    main()

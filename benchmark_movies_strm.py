#!/usr/bin/env python3
"""Benchmark: generate movie .strm files via xtream-downloader."""

from __future__ import annotations

import os
import shutil
import sys
import time

from core import (
    dedupe_catalog_by_quality,
    exclude_hidden_items,
    load_credentials,
    load_strm_sync_config,
)
from strm_sync import _fetch_vod_streams, _sync_movie_item

OUTPUT = os.environ.get("BENCHMARK_OUTPUT", "/benchmark/xtream-movies")


def main() -> int:
    creds = load_credentials()
    host = creds.get("host", "").strip()
    user = creds.get("user", "").strip()
    password = creds.get("password", "").strip()
    if not host or not user or not password:
        print("ERROR: missing Xtream credentials in .data", file=sys.stderr)
        return 1

    config = load_strm_sync_config()
    allow_4k = bool(config.get("allow_4k", False))
    movies_output = OUTPUT

    if os.path.isdir(movies_output):
        shutil.rmtree(movies_output)
    os.makedirs(movies_output, exist_ok=True)

    print(f"Output: {movies_output}")
    print("Fetching movie catalog from Xtream API...")
    t0 = time.perf_counter()
    movies = _fetch_vod_streams(host, user, password, None)
    fetch_elapsed = time.perf_counter() - t0
    movies = exclude_hidden_items(movies, "vod")
    deduped, total_versions = dedupe_catalog_by_quality(movies, allow_4k=allow_4k)
    print(
        f"Catalog: {len(deduped)} movies (from {total_versions} versions) "
        f"in {fetch_elapsed:.1f}s"
    )

    created = updated = skipped = 0
    t1 = time.perf_counter()
    for idx, item in enumerate(deduped, 1):
        result = _sync_movie_item(
            item,
            host,
            user,
            password,
            movies_output,
            update_existing=True,
        )
        if result == "created":
            created += 1
        elif result == "updated":
            updated += 1
        else:
            skipped += 1
        if idx % 500 == 0:
            print(f"  ... {idx}/{len(deduped)}")
    write_elapsed = time.perf_counter() - t1
    total_elapsed = time.perf_counter() - t0

    strm_count = sum(
        1
        for root, _dirs, files in os.walk(movies_output)
        for name in files
        if name.lower().endswith(".strm")
    )

    print("--- xtream-downloader results ---")
    print(f"Movies processed: {len(deduped)}")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Skipped: {skipped}")
    print(f".strm files on disk: {strm_count}")
    print(f"API fetch time: {fetch_elapsed:.2f}s")
    print(f"Write time: {write_elapsed:.2f}s")
    print(f"Total time: {total_elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

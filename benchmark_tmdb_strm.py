#!/usr/bin/env python3
"""Test TMDB-based .strm generation on a small sample into the test dir."""

from __future__ import annotations

import os
import sys
import time

from core import (
    dedupe_catalog_by_quality,
    exclude_hidden_items,
    is_adult_category,
    load_credentials,
    load_strm_sync_config,
    title_matches_terms,
)
from strm_sync import (
    _fetch_categories,
    _fetch_series_catalog,
    _fetch_vod_streams,
    _sync_movie_item,
    _sync_series_item,
)
from tmdb import TmdbClient

SAMPLE = int(os.environ.get("SAMPLE", "40"))
MOVIES_OUT = os.environ.get("MOVIES_OUT", "/strm-test/movies")
SERIES_OUT = os.environ.get("SERIES_OUT", "/strm-test/series")


def main() -> int:
    creds = load_credentials()
    host = creds.get("host", "").strip()
    user = creds.get("user", "").strip()
    password = creds.get("password", "").strip()
    if not host or not user or not password:
        print("ERROR: missing credentials", file=sys.stderr)
        return 1

    config = load_strm_sync_config()
    config["use_tmdb"] = True
    config["exclude_adult"] = True
    api_key = config.get("tmdb_api_key") or os.environ.get("TMDB_API_KEY", "")
    if not api_key:
        print("ERROR: no TMDB API key", file=sys.stderr)
        return 1

    client = TmdbClient(api_key, language=config.get("tmdb_language", "it-IT"),
                        rate_limit=int(config.get("tmdb_rate_limit", 40)))

    print("=== MOVIES (sample) ===")
    vod_cat_map = _fetch_categories(host, user, password, "get_vod_categories")
    movies = _fetch_vod_streams(host, user, password, None)
    movies = exclude_hidden_items(movies, "vod")
    deduped, _ = dedupe_catalog_by_quality(movies, allow_4k=config.get("allow_4k", False))

    adult_terms = config.get("adult_terms", [])
    adult_cat_count = sum(
        1 for m in deduped
        if is_adult_category(vod_cat_map.get(str(m.get("category_id") or ""), ""))
        or title_matches_terms(str(m.get("name") or ""), adult_terms)
    )
    print(f"Catalog movies: {len(deduped)} · adult-flagged (pre-TMDB): {adult_cat_count}")

    # Non-adult sample for naming demo
    sample = []
    for m in deduped:
        cat = vod_cat_map.get(str(m.get("category_id") or ""), "")
        if is_adult_category(cat) or title_matches_terms(str(m.get("name") or ""), adult_terms):
            continue
        sample.append(m)
        if len(sample) >= SAMPLE:
            break

    t0 = time.perf_counter()
    created = matched = unmatched = 0
    examples = []
    for item in sample:
        status, path = _sync_movie_item(
            item, host, user, password, MOVIES_OUT,
            update_existing=True, tmdb_client=client, config=config,
        )
        if path and "[tmdbid-" in path:
            matched += 1
        elif path:
            unmatched += 1
        if status.startswith("created"):
            created += 1
        if len(examples) < 12 and path:
            examples.append((str(item.get("name"))[:45], os.path.basename(os.path.dirname(path))))
    elapsed = time.perf_counter() - t0
    client.save_cache()

    print(f"Sample: {len(sample)} movies in {elapsed:.1f}s "
          f"({elapsed/max(len(sample),1)*1000:.0f} ms/movie) · "
          f"matched={matched} unmatched={unmatched} lookups={client.lookups} cache_hits={client.cache_hits}")
    print("Naming examples (Xtream -> folder):")
    for raw, folder in examples:
        print(f"  {raw!r:48} -> {folder!r}")

    print("\n=== SERIES (sample) ===")
    series_cat_map = _fetch_categories(host, user, password, "get_series_categories")
    series_list = _fetch_series_catalog(host, user, password, None)
    series_list = exclude_hidden_items(series_list, "series")
    s_sample = []
    for s in series_list:
        cat = series_cat_map.get(str(s.get("category_id") or ""), "")
        if is_adult_category(cat) or title_matches_terms(str(s.get("name") or ""), adult_terms):
            continue
        s_sample.append(s)
        if len(s_sample) >= 10:
            break

    t1 = time.perf_counter()
    ep_created = 0
    for s in s_sample:
        counts = _sync_series_item(
            s, host, user, password, SERIES_OUT,
            update_existing=True, tmdb_client=client, config=config,
        )
        ep_created += counts["created"]
    elapsed_s = time.perf_counter() - t1
    client.save_cache()
    print(f"Sample: {len(s_sample)} series, {ep_created} episodes in {elapsed_s:.1f}s · "
          f"lookups={client.lookups} cache_hits={client.cache_hits}")

    # Show a couple of generated series folders
    if os.path.isdir(SERIES_OUT):
        folders = sorted(os.listdir(SERIES_OUT))[:8]
        print("Series folders:")
        for f in folders:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

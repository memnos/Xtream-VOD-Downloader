#!/usr/bin/env python3
"""Merge duplicate Emby items and refresh libraries."""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
USER = "Fabio"

TV_LIBS = {"VOD SERIES", "Serie Tv"}
MOVIE_LIBS = {"VOD FILM"}


def request(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def get(path, params=None):
    return request("GET", path, params=params)


def post(path, params=None, body=None):
    return request("POST", path, params=params, body=body)


def delete(path, params=None):
    return request("DELETE", path, params=params)


def provider_key(provider_ids):
    if not provider_ids:
        return None
    for key in ("Tmdb", "Tvdb", "Imdb"):
        val = provider_ids.get(key)
        if val:
            return f"{key}:{val}"
    return None


def fetch_all_items(user_id, parent_id, item_type, extra_fields=""):
    fields = "Path,ProviderIds,MediaSources,ProductionYear,ChildCount"
    if extra_fields:
        fields += "," + extra_fields
    items = []
    start = 0
    limit = 200
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": str(limit),
                "Fields": fields,
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    return items


def pick_primary_item(group, item_type):
    """Prefer local download folders over strm; then item with most children."""

    def score(item):
        path = (item.get("Path") or "").lower()
        is_strm_root = "/strm/" in path
        is_local_tv = path.startswith("/data/tv/") or path.startswith("/data/tv-2/")
        child_count = int(item.get("ChildCount") or 0)
        media_count = len(item.get("MediaSources") or [])
        path_bonus = 0
        if is_local_tv:
            path_bonus = 1_000_000
        elif not is_strm_root:
            path_bonus = 500_000
        return (
            path_bonus + child_count * 1000 + media_count * 100,
            len(path),
        )

    return sorted(group, key=score, reverse=True)[0]


def merge_movie_group(group, dry_run):
    primary = pick_primary_item(group, "Movie")
    others = [g for g in group if g["Id"] != primary["Id"]]
    ids = [g["Id"] for g in group]
    print(f"  MERGE movies -> keep {primary['Name']} ({primary['Id']})")
    for item in others:
        print(f"    + {item.get('Path')}")
    if dry_run:
        return True
    post("/emby/Videos/MergeVersions", params={"Ids": ",".join(ids)})
    return True


def remove_duplicate_series(group, dry_run):
    primary = pick_primary_item(group, "Series")
    others = [g for g in group if g["Id"] != primary["Id"]]
    ids = [primary["Id"]] + [g["Id"] for g in others]
    print(f"  SERIES group -> merge {len(ids)} entries, primary {primary['Name']} ({primary['Id']}) @ {primary.get('Path')}")
    for item in others:
        print(f"    + {item.get('Path')}")
    if dry_run:
        return len(others)
    try:
        post("/emby/Videos/MergeVersions", params={"Ids": ",".join(ids)})
        return len(others)
    except Exception as exc:
        print(f"    MERGE FAILED: {exc}")
        return 0


def update_library_options(lib, dry_run):
    raw_opts = lib.get("LibraryOptions") or {}
    opts = dict(raw_opts[0] if isinstance(raw_opts, list) else raw_opts)
    lib_id = lib.get("ItemId") or lib.get("Id")
    name = lib.get("Name")
    changed = False
    desired = {
        "EnableAutomaticSeriesGrouping": True,
        "EnableMultiVersionByMetadata": True,
        "EnableMultiVersionByFiles": True,
    }
    for key, val in desired.items():
        if opts.get(key) is not True:
            opts[key] = val
            changed = True
    print(f"  Library {name}: EnableAutomaticSeriesGrouping={opts.get('EnableAutomaticSeriesGrouping')}, "
          f"EnableMultiVersionByMetadata={opts.get('EnableMultiVersionByMetadata')}")
    if changed and not dry_run:
        post(
            "/emby/Library/VirtualFolders/LibraryOptions",
            body={"Id": lib_id, "LibraryOptions": opts},
        )
    return lib_id


def refresh_library(lib_id, dry_run):
    if dry_run:
        print(f"  Would refresh library {lib_id}")
        return
    post(
        f"/emby/Items/{lib_id}/Refresh",
        params={
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply
    if dry_run:
        print("DRY RUN (pass --apply to execute)\n")

    users = get("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == USER)
    folders = get("/emby/Library/VirtualFolders")

    movie_merges = 0
    series_removed = 0
    refreshed = []

    for lib in folders:
        name = lib.get("Name")
        ctype = lib.get("CollectionType")
        if name in TV_LIBS and ctype == "tvshows":
            print(f"\n=== TV LIBRARY: {name} ===")
            lib_id = update_library_options(lib, dry_run)
            items = fetch_all_items(user_id, lib_id, "Series")
            groups = defaultdict(list)
            for item in items:
                pk = provider_key(item.get("ProviderIds") or {})
                if pk:
                    groups[pk].append(item)
            dupes = {k: v for k, v in groups.items() if len(v) > 1}
            print(f"  Duplicate series groups: {len(dupes)}")
            for pk, group in sorted(dupes.items(), key=lambda x: -len(x[1])):
                print(f"  [{pk}] x{len(group)}")
                series_removed += remove_duplicate_series(group, dry_run)
            refreshed.append(lib_id)

        elif name in MOVIE_LIBS and ctype == "movies":
            print(f"\n=== MOVIE LIBRARY: {name} ===")
            lib_id = update_library_options(lib, dry_run)
            items = fetch_all_items(user_id, lib_id, "Movie")
            groups = defaultdict(list)
            for item in items:
                pk = provider_key(item.get("ProviderIds") or {})
                if pk:
                    groups[pk].append(item)
            dupes = {k: v for k, v in groups.items() if len(v) > 1}
            print(f"  Duplicate movie groups: {len(dupes)}")
            for pk, group in sorted(dupes.items(), key=lambda x: -len(x[1])):
                print(f"  [{pk}] x{len(group)}")
                merge_movie_group(group, dry_run)
                movie_merges += 1
            refreshed.append(lib_id)

    print("\n=== REFRESH LIBRARIES ===")
    for lib_id in refreshed:
        refresh_library(lib_id, dry_run)

    print(f"\nSummary: movie merges={movie_merges}, series entries removed={series_removed}, libraries refreshed={len(refreshed)}")
    if dry_run:
        print("Re-run with --apply to perform changes.")


if __name__ == "__main__":
    main()

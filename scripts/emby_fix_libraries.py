#!/usr/bin/env python3
"""Fix Emby duplicate series/movies by enabling merge options and refreshing libraries."""
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

TARGET_LIBS = {
    "VOD FILM": "movies",
    "VOD SERIES": "tvshows",
    "Serie Tv": "tvshows",
}


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


def provider_key(provider_ids):
    if not provider_ids:
        return None
    for key in ("Tmdb", "Tvdb", "Imdb"):
        val = provider_ids.get(key)
        if val:
            return f"{key}:{val}"
    return None


def fetch_series_count(user_id, parent_id):
    items = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "ProviderIds",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    groups = defaultdict(list)
    for item in items:
        pk = provider_key(item.get("ProviderIds") or {})
        if pk:
            groups[pk].append(item)
    dupes = sum(1 for g in groups.values() if len(g) > 1)
    return len(items), dupes


def update_library_options(lib):
    raw_opts = lib.get("LibraryOptions") or {}
    opts = dict(raw_opts[0] if isinstance(raw_opts, list) else raw_opts)
    lib_id = lib.get("ItemId") or lib.get("Id")
    name = lib.get("Name")

    opts["EnableAutomaticSeriesGrouping"] = True
    opts["EnableMultiVersionByMetadata"] = True
    opts["EnableMultiVersionByFiles"] = True

    post(
        "/emby/Library/VirtualFolders/LibraryOptions",
        body={"Id": lib_id, "LibraryOptions": opts},
    )
    print(f"  Updated options for {name}")
    return lib_id


def refresh_library(lib_id, name):
    post(
        f"/emby/Items/{lib_id}/Refresh",
        params={
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "Default",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
        },
    )
    print(f"  Started refresh for {name} ({lib_id})")


def wait_for_tasks(timeout=900):
    print("  Waiting for library tasks to finish...")
    start = time.time()
    while time.time() - start < timeout:
        tasks = get("/emby/ScheduledTasks")
        running = [
            t
            for t in tasks
            if t.get("State") == "Running"
            and any(k in (t.get("Name") or "").lower() for k in ("scan", "library", "refresh"))
        ]
        if not running:
            print("  No active scan/refresh tasks.")
            return True
        names = ", ".join(t.get("Name", "?") for t in running[:3])
        print(f"  Still running: {names}")
        time.sleep(15)
    print("  Timeout waiting for tasks.")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", action="store_true", help="Wait for scan completion")
    args = parser.parse_args()

    users = get("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == USER)
    folders = get("/emby/Library/VirtualFolders")

    print("=== BEFORE ===")
    before = {}
    for lib in folders:
        name = lib.get("Name")
        if name not in TARGET_LIBS:
            continue
        total, dupes = fetch_series_count(user_id, lib["ItemId"]) if TARGET_LIBS[name] == "tvshows" else (0, 0)
        before[name] = (total, dupes)
        if TARGET_LIBS[name] == "tvshows":
            print(f"  {name}: {total} series, {dupes} duplicate TMDB groups")

    print("\n=== UPDATE OPTIONS + REFRESH ===")
    refreshed = []
    for lib in folders:
        name = lib.get("Name")
        if name not in TARGET_LIBS:
            continue
        lib_id = update_library_options(lib)
        refresh_library(lib_id, name)
        refreshed.append(name)

    post("/emby/Library/Refresh")

    if args.wait:
        wait_for_tasks()

    print("\n=== AFTER ===")
    for lib in folders:
        name = lib.get("Name")
        if name not in TARGET_LIBS or TARGET_LIBS[name] != "tvshows":
            continue
        total, dupes = fetch_series_count(user_id, lib["ItemId"])
        prev = before.get(name, (0, 0))
        print(f"  {name}: {total} series, {dupes} duplicate TMDB groups (was {prev[1]})")


if __name__ == "__main__":
    main()

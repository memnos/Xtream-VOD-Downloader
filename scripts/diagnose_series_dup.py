#!/usr/bin/env python3
"""Diagnose duplicate series entries (e.g. Big Bang Theory strm + local)."""
import json
import urllib.parse
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
USER = "Fabio"
SERIES_NAME = "Big Bang"


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def main():
    users = get("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == USER)
    folders = get("/emby/Library/VirtualFolders")
    vod = next(f for f in folders if f.get("Name") == "VOD SERIES")
    lib_id = vod.get("ItemId") or vod.get("Id")

    items = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": lib_id,
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "Path,ProviderIds,ChildCount",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        start += len(chunk)
        if start >= batch.get("TotalRecordCount", 0) or not chunk:
            break

    matches = [i for i in items if SERIES_NAME.lower() in (i.get("Name") or "").lower()]
    print(f"Found {len(matches)} series matching '{SERIES_NAME}' in VOD SERIES:\n")
    for s in matches:
        sid = s["Id"]
        eps = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": sid,
                "IncludeItemTypes": "Episode",
                "Recursive": "true",
                "Limit": "1",
                "Fields": "Path,MediaSources",
            },
        )
        total_eps = eps.get("TotalRecordCount", 0)
        sample = eps.get("Items", [])
        sample_path = sample[0].get("Path", "") if sample else ""
        print(f"  ID {sid}")
        print(f"    Name: {s.get('Name')}")
        print(f"    Path: {s.get('Path')}")
        print(f"    Tmdb: {(s.get('ProviderIds') or {}).get('Tmdb')}")
        print(f"    ChildCount: {s.get('ChildCount')} | Episodes (recursive): {total_eps}")
        if sample_path:
            print(f"    Sample ep: {sample_path}")
        print()

    if len(matches) == 2:
        print("Cause: same Tmdb ID across strm and local folders.")
        print("Run repair_series.py then unite_series_versions.py --episodes-only to merge as multi-version.")


if __name__ == "__main__":
    main()

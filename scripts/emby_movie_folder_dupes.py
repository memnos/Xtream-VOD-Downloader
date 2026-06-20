#!/usr/bin/env python3
"""Find movie duplicates by normalized title/year in VOD FILM."""
import json
import re
import urllib.parse
import urllib.request
from collections import defaultdict

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


def normalize_title(name):
    name = name.lower().strip()
    name = re.sub(r"\s*\[.*?\]\s*", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name


def folder_key(path):
    if not path:
        return None
    folder = path.rstrip("/").rsplit("/", 1)[-1]
    folder = re.sub(r"\s*\(\d{4}\)\s*$", "", folder).strip().lower()
    return folder


def main():
    users = get("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")
    folders = get("/emby/Library/VirtualFolders")
    vod = next(f for f in folders if f.get("Name") == "VOD FILM")
    parent_id = vod["ItemId"]

    items = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "Path,ProviderIds,ProductionYear",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break

    by_folder = defaultdict(list)
    by_tmdb = defaultdict(list)
    for item in items:
        path = item.get("Path") or ""
        by_folder[folder_key(path)].append(item)
        tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
        if tmdb:
            by_tmdb[tmdb].append(item)

    print(f"Total movies in VOD FILM: {len(items)}")
    print(f"Unique folder keys: {len(by_folder)}")

    folder_dupes = {k: v for k, v in by_folder.items() if k and len(v) > 1}
    print(f"Duplicate groups by folder name: {len(folder_dupes)}")
    for key, group in sorted(folder_dupes.items(), key=lambda x: -len(x[1]))[:20]:
        print(f"\n  [{key}] x{len(group)}")
        for item in group:
            tmdb = (item.get("ProviderIds") or {}).get("Tmdb", "?")
            src = "strm" if "/strm/" in (item.get("Path") or "") else "local"
            print(f"    - {item['Name']} | tmdb={tmdb} | {src} | {item.get('Path')}")

    # movies present in both roots
    local_folders = {folder_key(i.get("Path")) for i in items if "/strm/" not in (i.get("Path") or "")}
    strm_folders = {folder_key(i.get("Path")) for i in items if "/strm/" in (i.get("Path") or "")}
    overlap = local_folders & strm_folders
    print(f"\nFolder names present in BOTH local and strm: {len(overlap)}")
    for key in sorted(list(overlap))[:10]:
        print(f"  - {key}")


if __name__ == "__main__":
    main()

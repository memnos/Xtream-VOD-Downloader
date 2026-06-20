#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


users = get("/emby/Users")
user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")
folders = get("/emby/Library/VirtualFolders")
vod = next(f for f in folders if f.get("Name") == "VOD FILM")

items = []
start = 0
while True:
    batch = get(
        f"/emby/Users/{user_id}/Items",
        {
            "ParentId": vod["ItemId"],
            "IncludeItemTypes": "Movie",
            "Recursive": "true",
            "StartIndex": str(start),
            "Limit": "200",
            "Fields": "Path,ProviderIds,MediaSources",
        },
    )
    chunk = batch.get("Items", [])
    items.extend(chunk)
    total = batch.get("TotalRecordCount", 0)
    start += len(chunk)
    if start >= total or not chunk:
        break

for needle in ["Harry Potter e la pietra", "Avatar (2009)", "/strm/movies/Avatar"]:
    print(f"\n=== Items matching '{needle}' ===")
    for item in items:
        path = item.get("Path") or ""
        name = item.get("Name") or ""
        if needle.lower() in path.lower() or needle.lower() in name.lower():
            tmdb = (item.get("ProviderIds") or {}).get("Tmdb", "?")
            sources = item.get("MediaSources") or []
            print(f"  {name} | id={item['Id']} | tmdb={tmdb} | sources={len(sources)}")
            print(f"    {path}")
            for src in sources[:3]:
                print(f"      source: {src.get('Path')}")

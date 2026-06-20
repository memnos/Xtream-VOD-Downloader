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

# find item by known strm id
for item_id in ["5806068", "5866788"]:
    item = get(f"/emby/Users/{user_id}/Items/{item_id}", {"Fields": "Path,ProviderIds,MediaSources,AlternateVersions"})
    print(f"\nItem {item_id}:")
    print(f"  Name: {item.get('Name')}")
    print(f"  Path: {item.get('Path')}")
    print(f"  MediaSources: {len(item.get('MediaSources') or [])}")
    for s in item.get("MediaSources") or []:
        print(f"    - {s.get('Path')}")
    alts = item.get("AlternateVersions") or item.get("MediaSources") or []
    print(f"  AlternateVersions: {item.get('AlternateVersions')}")

# count movies with both local and strm in same tmdb - search overlapping
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

multi = [i for i in items if len(i.get("MediaSources") or []) > 1]
print(f"\nVOD FILM total movies: {len(items)}")
print(f"Movies with multiple media sources: {len(multi)}")
for m in multi[:10]:
    print(f"  {m['Name']}: {len(m['MediaSources'])} versions")

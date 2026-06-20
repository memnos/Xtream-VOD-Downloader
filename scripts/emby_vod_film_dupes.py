#!/usr/bin/env python3
import json
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
            "Fields": "Path,ProviderIds",
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
    tmdb = (item.get("ProviderIds") or {}).get("Tmdb")
    if tmdb:
        groups[tmdb].append(item)

dupes = {k: v for k, v in groups.items() if len(v) > 1}
print(f"VOD FILM movies: {len(items)}")
print(f"Duplicate TMDB groups in API listing: {len(dupes)}")
for k, v in list(dupes.items())[:10]:
    print(f"  Tmdb={k} x{len(v)}")
    for i in v:
        print(f"    {i['Id']} | {i.get('Path')}")

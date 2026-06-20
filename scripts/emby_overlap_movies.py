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

for title in ["A Beautiful Mind", "Django Unchained", "Cast Away", "Gran Torino", "Greenland"]:
    print(f"\n=== {title} ===")
    hits = [i for i in items if title.lower() in (i.get("Name") or "").lower()]
    for item in hits:
        tmdb = (item.get("ProviderIds") or {}).get("Tmdb", "?")
        sources = item.get("MediaSources") or []
        print(f"  {item['Name']} | id={item['Id']} | tmdb={tmdb} | versions={len(sources)}")
        print(f"    primary: {item.get('Path')}")
        for src in sources:
            print(f"    source: {src.get('Path')}")

# count local vs strm indexed movies
local = [i for i in items if "/strm/" not in (i.get("Path") or "")]
strm = [i for i in items if "/strm/" in (i.get("Path") or "")]
multi = [i for i in items if len(i.get("MediaSources") or []) > 1]
print(f"\nTotal={len(items)} local={len(local)} strm-primary={len(strm)} multi-version={len(multi)}")

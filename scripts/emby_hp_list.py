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
            "Fields": "Path,ProviderIds",
        },
    )
    chunk = batch.get("Items", [])
    items.extend(chunk)
    total = batch.get("TotalRecordCount", 0)
    start += len(chunk)
    if start >= total or not chunk:
        break

hp = [i for i in items if (i.get("ProviderIds") or {}).get("Tmdb") == "671"]
print(f"Harry Potter tmdb=671 entries in VOD FILM list: {len(hp)}")
for i in hp:
    print(f"  {i['Id']} | {i.get('Path')}")

ids = {i["Id"] for i in items}
print(f"5866788 in list: {'5866788' in ids}")
print(f"5806068 in list: {'5806068' in ids}")

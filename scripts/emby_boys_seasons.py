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
vod = next(f for f in folders if f.get("Name") == "VOD SERIES")

series = get(
    f"/emby/Users/{user_id}/Items",
    {
        "ParentId": vod["ItemId"],
        "SearchTerm": "The Boys",
        "IncludeItemTypes": "Series",
        "Recursive": "true",
        "Fields": "Path,ChildCount",
    },
)
print("Series:", series.get("TotalRecordCount"))
for s in series.get("Items", []):
    print(f"  {s['Name']} | children={s.get('ChildCount')}")
    seasons = get(
        f"/emby/Users/{user_id}/Items",
        {
            "ParentId": s["Id"],
            "IncludeItemTypes": "Season",
            "Fields": "Path",
        },
    )
    for season in seasons.get("Items", []):
        print(f"    {season.get('Name')} | {season.get('Path')}")

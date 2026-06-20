#!/usr/bin/env python3
"""Diagnose series hierarchy after merge."""
import json
import sqlite3
import urllib.parse
import urllib.request

API_KEY = open("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read()
import pathlib
cfg = json.loads(pathlib.Path("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read_text())
API_KEY = cfg["emby_api_key"]
BASE = cfg["emby_url"].rstrip("/")
DB = cfg["library_db"]


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


users = get("/emby/Users")
user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")

for name in ["The Boys", "Andor", "The Last of Us", "Slow Horses"]:
    print(f"\n{'='*50}\n{name}")
    res = get(
        f"/emby/Users/{user_id}/Items",
        {"SearchTerm": name, "IncludeItemTypes": "Series", "Recursive": "true", "Fields": "Path,ChildCount,ProviderIds"},
    )
    for s in res.get("Items", [])[:3]:
        print(f"  SERIES {s['Name']} id={s['Id']} children={s.get('ChildCount')} path={s.get('Path')}")
        seasons = get(
            f"/emby/Users/{user_id}/Items",
            {"ParentId": s["Id"], "IncludeItemTypes": "Season", "Fields": "Path,IndexNumber,ChildCount"},
        )
        print(f"    seasons listed: {len(seasons.get('Items', []))}")
        for sn in seasons.get("Items", [])[:8]:
            print(f"      S{sn.get('IndexNumber')} {sn.get('Name')} children={sn.get('ChildCount')} | {sn.get('Path')}")
        eps = get(
            f"/emby/Users/{user_id}/Items",
            {"ParentId": s["Id"], "IncludeItemTypes": "Episode", "Recursive": "true", "Fields": "Path"},
        )
        print(f"    episodes recursive: {eps.get('TotalRecordCount', 0)}")

conn = sqlite3.connect(DB)
print("\n\n=== DB sample: The Boys ===")
rows = conn.execute(
    "SELECT Id, type, Name, Path, ParentId, SeriesId FROM MediaItems WHERE Path LIKE '%The Boys%' AND type IN (6,7,8) ORDER BY type, Path LIMIT 40"
).fetchall()
for r in rows:
    print(r)
conn.close()

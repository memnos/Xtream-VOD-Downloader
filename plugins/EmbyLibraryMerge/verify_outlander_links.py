#!/usr/bin/env python3
import json
import sqlite3
import urllib.parse
import urllib.request

cfg = json.loads(open("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read())
BASE = cfg["emby_url"].rstrip("/")
KEY = cfg["emby_api_key"]
SID = 5899873

c = sqlite3.connect(cfg["library_db"])
print("=== Episodi S08/S09 con path mkv in STRM tree ===")
for r in c.execute(
    "SELECT Id, Name, Path, ParentIndexNumber, IndexNumber FROM MediaItems "
    "WHERE SeriesId=? AND type=8 AND ParentIndexNumber IN (8,9) "
    "AND Path LIKE '%Outlander%' ORDER BY ParentIndexNumber, IndexNumber, Path",
    (SID,),
):
    print(r)
c.close()

users = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/emby/Users", headers={"X-Emby-Token": KEY})).read())
uid = users[0]["Id"]
for s, e in [(8, 4), (8, 5), (9, 1)]:
    data = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                BASE + f"/emby/Shows/{SID}/Episodes?"
                + urllib.parse.urlencode({
                    "UserId": uid,
                    "Season": str(s),
                    "IndexNumber": str(e),
                    "Fields": "MediaSources,Path",
                }),
                headers={"X-Emby-Token": KEY},
            )
        ).read()
    )
    items = data.get("Items", [])
    if not items:
        print(f"\nS{s}E{e}: non trovato")
        continue
    item = items[0]
    sources = item.get("MediaSources") or []
    print(f"\nS{s}E{e}: {item.get('Name')} | {len(sources)} media source(s)")
    for src in sources:
        print(f"  - {src.get('Path') or src.get('Name')}")

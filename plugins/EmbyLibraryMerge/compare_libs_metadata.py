#!/usr/bin/env python3
import json
import sqlite3
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DB = "/var/lib/emby_config/data/library.db"
uid = json.loads(urllib.request.urlopen(
    urllib.request.Request(BASE + "/emby/Users", headers={"X-Emby-Token": KEY})
).read())[0]["Id"]

def lib_series(name):
    folders = json.loads(urllib.request.urlopen(
        urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
    ).read())
    lib = next(f for f in folders if f["Name"] == name)
    items = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            BASE + f"/emby/Users/{uid}/Items?ParentId={lib['ItemId']}&IncludeItemTypes=Series&Recursive=true&Limit=500&Fields=ProviderIds,Overview,PrimaryImageTag,Path",
            headers={"X-Emby-Token": KEY},
        )
    ).read()).get("Items", [])
    return items

for lib_name in ("Serie Tv", "VOD SERIES"):
    items = lib_series(lib_name)
    local_tv = [i for i in items if (i.get("Path") or "").startswith("/data/tv/")]
    ok = [i for i in local_tv if (i.get("ProviderIds") or {}).get("Tmdb") and i.get("PrimaryImageTag")]
    print(f"\n{lib_name}: {len(items)} serie totali, {len(local_tv)} sotto /data/tv")
    print(f"  con TMDB+poster: {len(ok)}")
    for i in ok[:5]:
        print(f"    {i['Name']}")

# same show in both libs?
conn = sqlite3.connect(DB)
print("\n=== The Big Bang Theory entries ===")
for r in conn.execute("SELECT Id,Name,Path,ProviderIds FROM MediaItems WHERE type=6 AND Name LIKE '%Big Bang%'"):
    print(r)

print("\n=== 9-1-1 entries ===")
for r in conn.execute("SELECT Id,Name,Path,ProviderIds FROM MediaItems WHERE type=6 AND Name LIKE '%9-1-1%'"):
    print(r)

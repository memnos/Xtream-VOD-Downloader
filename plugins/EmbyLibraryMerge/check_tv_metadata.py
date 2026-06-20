#!/usr/bin/env python3
"""Check local TV series metadata (tvshow.nfo, posters) vs Emby DB."""
import json
import sqlite3
import subprocess
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DB = "/var/lib/emby_config/data/library.db"
CONTAINER = "embyserver"

# list series folders in /data/tv
out = subprocess.run(
    f"docker exec {CONTAINER} find /data/tv -maxdepth 1 -mindepth 1 -type d 2>/dev/null",
    shell=True, capture_output=True, text=True,
)
folders = sorted([f.strip() for f in out.stdout.splitlines() if f.strip()])

has_nfo = []
no_nfo = []
for folder in folders:
    r = subprocess.run(
        f"docker exec {CONTAINER} test -f '{folder}/tvshow.nfo' && echo yes || echo no",
        shell=True, capture_output=True, text=True,
    )
    name = folder.split("/")[-1]
    if r.stdout.strip() == "yes":
        has_nfo.append(name)
    else:
        no_nfo.append(name)

print(f"=== /data/tv: {len(folders)} cartelle ===")
print(f"  con tvshow.nfo: {len(has_nfo)}")
print(f"  senza tvshow.nfo: {len(no_nfo)}")
if no_nfo[:15]:
    print("  Esempi senza nfo:", ", ".join(no_nfo[:15]))

conn = sqlite3.connect(DB)
print("\n=== Serie in DB con path /data/tv (type=6) ===")
rows = conn.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE type=6 AND Path LIKE '/data/tv/%' ORDER BY Name"
).fetchall()
print(f"Totale: {len(rows)}")
with_tmdb = [r for r in rows if r[3] and "Tmdb=" in (r[3] or "")]
without_tmdb = [r for r in rows if not r[3] or "Tmdb=" not in (r[3] or "")]
print(f"  con TMDB: {len(with_tmdb)}")
print(f"  senza TMDB: {len(without_tmdb)}")
for r in without_tmdb[:10]:
    print(f"    {r[1]} @ {r[2]}")

# Serie Tv library items via API
users = json.loads(urllib.request.urlopen(
    urllib.request.Request(BASE + "/emby/Users", headers={"X-Emby-Token": KEY})
).read())
uid = users[0]["Id"]
folders_api = json.loads(urllib.request.urlopen(
    urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
).read())
serie_tv = next(f for f in folders_api if f["Name"] == "Serie Tv")
lib_id = serie_tv["ItemId"]

items = []
start = 0
while True:
    batch = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            BASE + f"/emby/Users/{uid}/Items?"
            + f"ParentId={lib_id}&IncludeItemTypes=Series&Recursive=true&StartIndex={start}&Limit=100"
            + "&Fields=ProviderIds,Overview,PrimaryImageTag,Path",
            headers={"X-Emby-Token": KEY},
        )
    ).read())
    chunk = batch.get("Items", [])
    items.extend(chunk)
    if start + len(chunk) >= batch.get("TotalRecordCount", 0) or not chunk:
        break
    start += len(chunk)

local = [i for i in items if (i.get("Path") or "").startswith("/data/tv")]
with_poster = [i for i in local if i.get("PrimaryImageTag")]
with_overview = [i for i in local if (i.get("Overview") or "").strip()]
with_tmdb_api = [i for i in local if (i.get("ProviderIds") or {}).get("Tmdb")]

print(f"\n=== API libreria 'Serie Tv' - serie sotto /data/tv: {len(local)} ===")
print(f"  con poster: {len(with_poster)}")
print(f"  con trama: {len(with_overview)}")
print(f"  con TMDB: {len(with_tmdb_api)}")
print("\n  Con metadati completi:")
for i in sorted(local, key=lambda x: x.get("Name", "")):
    has_p = bool(i.get("PrimaryImageTag"))
    has_o = bool((i.get("Overview") or "").strip())
    has_t = bool((i.get("ProviderIds") or {}).get("Tmdb"))
    if has_p and has_o and has_t:
        print(f"    ✓ {i['Name']}")
print("\n  Senza poster/trama/TMDB (primi 15):")
count = 0
for i in sorted(local, key=lambda x: x.get("Name", "")):
    has_p = bool(i.get("PrimaryImageTag"))
    has_o = bool((i.get("Overview") or "").strip())
    has_t = bool((i.get("ProviderIds") or {}).get("Tmdb"))
    if not (has_p and has_o and has_t):
        print(f"    ✗ {i['Name']} | poster={has_p} trama={has_o} tmdb={has_t} | {i.get('Path','')[:60]}")
        count += 1
        if count >= 15:
            break

conn.close()

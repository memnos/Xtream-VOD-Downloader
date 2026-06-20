#!/usr/bin/env python3
import json
import subprocess
import sqlite3
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
CONTAINER = "embyserver"
DB = "/var/lib/emby_config/data/library.db"

# library options compare
req = urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
for lib in json.loads(urllib.request.urlopen(req, timeout=30).read()):
    if lib.get("Name") not in ("Serie Tv", "VOD SERIES"):
        continue
    o = lib.get("LibraryOptions") or {}
    print("=" * 40, lib["Name"])
    for k in sorted(o):
        if "metadata" in k.lower() or "Metadata" in k or "Fetcher" in k or "Internet" in k or "Provider" in k or "Saver" in k:
            print(f"  {k}: {o[k]}")

conn = sqlite3.connect(DB)
# sample series: episodes count
samples = conn.execute(
    "SELECT Id, Name, Path FROM MediaItems WHERE type=6 AND Path LIKE '/data/tv/%' LIMIT 20"
).fetchall()
print("\n=== Episodi per serie campione /data/tv ===")
for sid, name, path in samples:
    eps = conn.execute("SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=8", (sid,)).fetchone()[0]
    mkv = subprocess.run(
        f"docker exec {CONTAINER} find '{path}' -type f \\( -name '*.mkv' -o -name '*.mp4' \\) 2>/dev/null | wc -l",
        shell=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  {name[:40]:40} DB_eps={eps:3} disk_videos={mkv}")

# empty series count
empty = conn.execute(
    "SELECT COUNT(*) FROM MediaItems s WHERE s.type=6 AND s.Path LIKE '/data/tv/%' "
    "AND NOT EXISTS (SELECT 1 FROM MediaItems e WHERE e.SeriesId=s.Id AND e.type=8)"
).fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM MediaItems WHERE type=6 AND Path LIKE '/data/tv/%'").fetchone()[0]
print(f"\nSerie /data/tv senza episodi in DB: {empty}/{total}")

# permission test: can emby read a random folder
test = "/data/tv/Stranger Things"
r = subprocess.run(
    f"docker exec {CONTAINER} sh -c \"ls '{test}' 2>&1 | head -3; test -r '{test}' && echo READ_OK || echo READ_FAIL\"",
    shell=True, capture_output=True, text=True,
)
print(f"\nPermessi test {test}:")
print(r.stdout)

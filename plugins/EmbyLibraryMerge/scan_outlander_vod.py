#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import time
import urllib.request

cfg = json.loads(open("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read())
BASE = cfg["emby_url"].rstrip("/")
KEY = cfg["emby_api_key"]
HDR = {"X-Emby-Token": KEY, "Content-Type": "application/json"}

# notify all local mkv under Outlander
out = subprocess.run(
    "docker exec embyserver find /data/tv/Outlander -name '*.mkv'",
    shell=True, capture_output=True, text=True,
)
files = [f.strip() for f in out.stdout.splitlines() if f.strip()]
files.append("/data/tv/Outlander")
print(f"Notifying {len(files)} paths for Outlander")

for i in range(0, len(files), 20):
    batch = files[i : i + 20]
    urllib.request.urlopen(
        urllib.request.Request(
            BASE + "/emby/Library/Media/Updated",
            data=json.dumps({"Updates": [{"Path": p, "UpdateType": "Created"} for p in batch]}).encode(),
            headers=HDR, method="POST",
        ), timeout=120,
    )

# refresh VOD SERIES
folders = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})).read())
vod = next(f for f in folders if f["Name"] == "VOD SERIES")
urllib.request.urlopen(
    urllib.request.Request(
        BASE + f"/emby/Items/{vod['ItemId']}/Refresh?Recursive=true",
        headers={"X-Emby-Token": KEY}, method="POST",
    ), timeout=120,
)
print("VOD SERIES refresh avviato")
time.sleep(30)

c = sqlite3.connect(cfg["library_db"])
local = c.execute("SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE '/data/tv/Outlander%'").fetchone()[0]
total_local = c.execute("SELECT COUNT(*) FROM MediaItems WHERE type=8 AND (Path LIKE '/data/tv/%' OR Path LIKE '/data/tv-2/%')").fetchone()[0]
out_eps = c.execute("SELECT COUNT(*) FROM MediaItems WHERE SeriesId=5899873 AND type=8").fetchone()[0]
print(f"Outlander local episodes: {local}")
print(f"Outlander total episodes (series 5899873): {out_eps}")
print(f"All local episodes in DB: {total_local}")

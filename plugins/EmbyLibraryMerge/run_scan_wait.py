#!/usr/bin/env python3
import json
import time
import urllib.request

cfg = json.loads(open("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read())
BASE = cfg["emby_url"].rstrip("/")
KEY = cfg["emby_api_key"]
HDR = {"X-Emby-Token": KEY, "Content-Type": "application/json"}

paths = ["/data/tv/Outlander", "/data/tv/Downton Abbey", "/data/tv/The Last Kingdom",
         "/data/tv/The Big Bang Theory (2007)", "/data/tv/Outlander (2014)"]
updates = [{"Path": p, "UpdateType": "Created"} for p in paths]
req = urllib.request.Request(BASE + "/emby/Library/Media/Updated", data=json.dumps({"Updates": updates}).encode(), headers=HDR, method="POST")
urllib.request.urlopen(req, timeout=120)

tasks = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/emby/ScheduledTasks", headers={"X-Emby-Token": KEY})).read())
tid = next(t["Id"] for t in tasks if (t.get("Name") or "").lower() == "scan media library")
urllib.request.urlopen(urllib.request.Request(BASE + f"/emby/ScheduledTasks/Running/{tid}", headers={"X-Emby-Token": KEY}, method="POST"), timeout=60)
print("Scan avviato, attendo...")
for _ in range(240):
    tasks = json.loads(urllib.request.urlopen(urllib.request.Request(BASE + "/emby/ScheduledTasks", headers={"X-Emby-Token": KEY})).read())
    scan = next((t for t in tasks if t["Id"] == tid), None)
    if scan and scan.get("State") == "Running":
        print(f"  {scan.get('CurrentProgressPercentage', 0):.1f}%")
        time.sleep(10)
    else:
        print("Scan completato.")
        break

#!/usr/bin/env python3
import json
import sqlite3
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"

def get(path):
    req = urllib.request.Request(BASE + path, headers={"X-Emby-Token": KEY})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("Outlander series:")
for r in c.execute(
    "SELECT Id, Name, Path, type FROM MediaItems "
    "WHERE Path LIKE '%Outlander%' AND type IN (6,7,8) ORDER BY type, Path"
):
    print(r)
print("\nLocal Outlander episodes:", c.execute(
    "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE '/data/tv/Outlander%'"
).fetchone()[0])

print("\nScheduled tasks:")
for t in get("/emby/ScheduledTasks"):
    if "scan" in (t.get("Name") or "").lower():
        print(t.get("Id"), t.get("Name"), t.get("State"))

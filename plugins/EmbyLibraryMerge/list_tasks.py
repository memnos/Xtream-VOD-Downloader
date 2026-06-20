#!/usr/bin/env python3
import json
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
req = urllib.request.Request(
    "http://127.0.0.1:8096/emby/ScheduledTasks?api_key=" + KEY,
    headers={"X-Emby-Token": KEY},
)
tasks = json.loads(urllib.request.urlopen(req, timeout=30).read())
print("=== Attività Library Merge ===")
for t in tasks:
    if t.get("Category") == "Library Merge" or "Merge" in (t.get("Name") or ""):
        print(f"  {t['Name']} | categoria: {t.get('Category')} | key: {t.get('Key')}")

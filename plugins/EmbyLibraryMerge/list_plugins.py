#!/usr/bin/env python3
import json
import urllib.request
KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
req = urllib.request.Request(
    "http://127.0.0.1:8096/emby/Plugins?api_key=" + KEY,
    headers={"X-Emby-Token": KEY},
)
plugins = json.loads(urllib.request.urlopen(req, timeout=30).read())
for p in plugins:
    name = p.get("Name") or ""
    if "merge" in name.lower() or "library" in name.lower() or "strm" in name.lower():
        print(name, "|", p.get("Status"), "|", p.get("Id"))
print("---")
print("Total plugins:", len(plugins))
missing = "Library Merge" in [p.get("Name") for p in plugins]
print("Library Merge present:", missing)

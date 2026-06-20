#!/usr/bin/env python3
import json
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
req = urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
for lib in json.loads(urllib.request.urlopen(req, timeout=30).read()):
    if lib.get("Name") != "Serie Tv":
        continue
    print(json.dumps(lib.get("LibraryOptions") or {}, indent=2))

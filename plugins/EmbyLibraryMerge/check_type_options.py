#!/usr/bin/env python3
import json
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
req = urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
for lib in json.loads(urllib.request.urlopen(req, timeout=30).read()):
    if lib.get("Name") not in ("Serie Tv", "Film"):
        continue
    print("=" * 50, lib["Name"])
    o = lib.get("LibraryOptions") or {}
    print("TypeOptions:", json.dumps(o.get("TypeOptions"), indent=2))
    for k in sorted(o):
        if "Fetcher" in k or "Metadata" in k or "Internet" in k or "Reader" in k or "Language" in k:
            print(f"  {k}: {o[k]}")

#!/usr/bin/env python3
"""Rimuove percorsi vecchi dalle librerie Emby (strm, tv-2) dopo mergerfs."""
import json
import urllib.parse
import urllib.request

API = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
headers = {"X-Emby-Token": API}

KEEP = {
    "movies": {"/data/movies"},
    "tvshows": {"/data/tv"},
}


def get(path):
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def post(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


libs = get("/emby/Library/VirtualFolders")
removed = 0
for lib in libs:
    name = lib.get("Name", "")
    ctype = (lib.get("CollectionType") or "").lower()
    if ctype not in KEEP:
        continue
    want = KEEP[ctype]
    for path in lib.get("Locations") or []:
        if path in want:
            print(f"KEEP {name}: {path}")
            continue
        try:
            post(
                "/emby/Library/VirtualFolders/Delete",
                {
                    "id": lib.get("ItemId") or lib.get("Id"),
                    "Path": path,
                    "RefreshLibrary": "false",
                },
            )
            print(f"DEL  {name}: {path}")
            removed += 1
        except urllib.error.HTTPError as e:
            print(f"FAIL {name}: {path} -> {e.code} {e.read().decode()[:200]}")

print(f"\nPercorsi rimossi: {removed}")
libs = get("/emby/Library/VirtualFolders")
for lib in libs:
    locs = lib.get("Locations") or []
    if locs:
        print(f"{lib.get('Name')} -> {locs}")

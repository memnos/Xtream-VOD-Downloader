#!/usr/bin/env python3
"""Ripristina librerie Emby VOD sui mount mergerfs."""
import json
import urllib.parse
import urllib.request

API = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
headers = {"X-Emby-Token": API, "Content-Type": "application/json"}

LIBS = [
    ("VOD FILM", "movies", ["/data/movies"]),
    ("VOD SERIES", "tvshows", ["/data/tv"]),
    ("Serie Tv", "tvshows", ["/data/tv"]),
]


def get(path):
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def post(path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


existing = {lib.get("Name"): lib for lib in get("/emby/Library/VirtualFolders")}

for name, ctype, paths in LIBS:
    lib = existing.get(name)
    if lib:
        lib_id = lib.get("ItemId") or lib.get("Id")
        have = set(lib.get("Locations") or [])
        for path in paths:
            if path in have:
                print(f"OK  {name}: {path} già presente")
                continue
            post(
                "/emby/Library/VirtualFolders/Paths",
                body={"Id": lib_id, "Path": path, "RefreshLibrary": False},
            )
            print(f"ADD {name}: {path}")
        continue

    post(
        "/emby/Library/VirtualFolders",
        body={
            "Name": name,
            "CollectionType": ctype,
            "RefreshLibrary": False,
            "Paths": paths,
        },
    )
    print(f"NEW {name}: {paths}")

print("\n--- stato finale ---")
for lib in get("/emby/Library/VirtualFolders"):
    locs = lib.get("Locations") or []
    if locs:
        print(f"{lib.get('Name')} -> {locs}")

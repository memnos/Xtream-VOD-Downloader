#!/usr/bin/env python3
"""Aggiorna le librerie Emby per usare solo /data/movies e /data/tv (mergerfs)."""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "plugins" / "EmbyLibraryMerge" / "host-config.json"
BASE = "http://127.0.0.1:8096"

MOVIES_PATHS = {"/data/movies"}
SERIES_PATHS = {"/data/tv"}

LIBRARY_TARGETS = {
    "movies": MOVIES_PATHS,
    "tvshows": SERIES_PATHS,
    "mixed": MOVIES_PATHS | SERIES_PATHS,
}


def load_api_key() -> str:
    if CONFIG.is_file():
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        key = data.get("ApiKey") or data.get("api_key") or data.get("emby_api_key")
        if key:
            return key
    raise SystemExit(f"API key non trovata in {CONFIG}")


def request(method: str, path: str, *, params: dict | None = None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    headers = {"X-Emby-Token": load_api_key(), "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def collection_type(name: str, collection_type_value: str | None) -> str | None:
    if collection_type_value:
        return collection_type_value.lower()
    upper = name.upper()
    if "FILM" in upper or "MOVIE" in upper:
        return "movies"
    if "SERIE" in upper or "SERIES" in upper or "TV" in upper:
        return "tvshows"
    return None


def main() -> int:
    libs = request("GET", "/emby/Library/VirtualFolders")
    changed = 0
    for lib in libs:
        name = lib.get("Name", "")
        ctype = collection_type(name, lib.get("CollectionType"))
        if not ctype or ctype not in LIBRARY_TARGETS:
            continue
        want = sorted(LIBRARY_TARGETS[ctype])
        have = sorted(lib.get("Locations") or [])
        if have == want:
            print(f"OK  {name}: {have}")
            continue
        body = {
            "Id": lib.get("ItemId") or lib.get("Id"),
            "Name": name,
            "CollectionType": ctype,
            "Locations": want,
            "RefreshLibrary": False,
        }
        request("POST", "/emby/Library/VirtualFolders/Name", params={"name": name, "id": body["Id"]}, body=body)
        print(f"UPD {name}: {have} -> {want}")
        changed += 1
    print(f"\nLibrerie aggiornate: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

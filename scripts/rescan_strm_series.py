#!/usr/bin/env python3
"""Re-scan strm series folders so Emby re-discovers episodes after a bad merge."""
import json
import urllib.parse
import urllib.request
from pathlib import Path

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
CONFIG = Path(__file__).resolve().parents[1] / "plugins" / "EmbyLibraryMerge" / "host-config.json"

PATHS = [
    "/data/strm/series/The Big Bang Theory (2007)",
    "/data/tv/Outlander (2014)",
    "/data/tv-2/The Big Bang Theory",
]


def api(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read().decode()


def main():
    updates = [{"Path": p, "UpdateType": "Created"} for p in PATHS]
    api("POST", "/emby/Library/Media/Updated", body={"Updates": updates})
    print("Notified Emby of paths:")
    for p in PATHS:
        print(f"  {p}")

    folders = json.loads(api("GET", "/emby/Library/VirtualFolders"))
    for lib in folders:
        if lib.get("Name") == "VOD SERIES":
            lib_id = lib.get("ItemId") or lib.get("Id")
            api(
                "POST",
                f"/emby/Items/{lib_id}/Refresh",
                params={"Recursive": "true", "MetadataRefreshMode": "Default"},
            )
            print(f"Refresh started on VOD SERIES ({lib_id})")


if __name__ == "__main__":
    main()

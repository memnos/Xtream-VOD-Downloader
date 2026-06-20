#!/usr/bin/env python3
"""Remove wrongly identified series from disk and Emby."""
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DOCKER = "embyserver"
HEADERS = {"X-Emby-Token": KEY}

SERIES = [
    ("5970016", "Cucina al mercato con Ruben"),
    ("5969770", "Hell's Kitchen USA"),
    ("5969794", "Taratata"),
    ("5969839", "Willy Coyote e Road Runner"),
]


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60).read())


def delete_item(item_id: str) -> None:
    req = urllib.request.Request(BASE + f"/emby/Items/{item_id}", headers=HEADERS, method="DELETE")
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def rm_folder(folder: str) -> None:
    proc = subprocess.run(["docker", "exec", DOCKER, "rm", "-rf", folder], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"rm failed: {folder}")


def main():
    uid = get("/emby/Users")[0]["Id"]
    for item_id, label in SERIES:
        item = get(f"/emby/Users/{uid}/Items/{item_id}", {"Fields": "Name,Path"})
        path = item.get("Path") or ""
        folder = str(Path(path)) if path else ""
        print(f"\n{label} ({item.get('Name')})")
        print(f"  id={item_id}  cartella={folder}")
        delete_item(item_id)
        print("  Emby: eliminato")
        if folder:
            rm_folder(folder)
            print("  Disco: eliminato")
    print("\nFatto.")


if __name__ == "__main__":
    main()

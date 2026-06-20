#!/usr/bin/env python3
"""Remove unfound movies from disk and Emby library."""
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DOCKER = "embyserver"
HEADERS = {"X-Emby-Token": KEY}
REPORT = Path(__file__).with_name("missing_metadata_report.json")

NAMES = {
    "Apocalis'napoli",
    "Estonia Coastal Route",
    "Formula criminale",
    "Geordie Stories Nathan & Dad",
    "Il delitto di Avetrana",
    "Ritorno al Breithorn",
}


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60).read())


def delete_item(item_id: str) -> None:
    req = urllib.request.Request(
        BASE + f"/emby/Items/{item_id}",
        headers=HEADERS,
        method="DELETE",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        r.read()


def rm_folder(folder: str) -> None:
    proc = subprocess.run(
        ["docker", "exec", DOCKER, "rm", "-rf", folder],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"rm -rf {folder}: {proc.stderr.strip()}")


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    ids = data["Film"]["missing_ids"]
    names = data["Film"]["missing_names"]
    uid = get("/emby/Users")[0]["Id"]

    for item_id, name in zip(ids, names):
        if name not in NAMES:
            print(f"SKIP {name} (non nella lista attesa)")
            continue
        item = get(f"/emby/Users/{uid}/Items/{item_id}", {"Fields": "Path,Name"})
        path = item.get("Path") or ""
        folder = str(Path(path).parent) if path else ""
        print(f"\n{name}")
        print(f"  Emby id: {item_id}")
        print(f"  Cartella: {folder}")

        try:
            delete_item(item_id)
            print("  Emby: eliminato")
        except Exception as exc:
            print(f"  Emby: ERRORE {exc}")

        if folder:
            try:
                rm_folder(folder)
                print("  Disco: cartella eliminata")
            except Exception as exc:
                print(f"  Disco: ERRORE {exc}")
        else:
            print("  Disco: percorso assente, skip")

    print("\nFatto.")


if __name__ == "__main__":
    main()

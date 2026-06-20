#!/usr/bin/env python3
"""Write movie.nfo for films absent from TMDB and refresh in Emby."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
HEADERS = {"X-Emby-Token": KEY, "Content-Type": "application/json"}
REPORT = Path(__file__).with_name("missing_metadata_report.json")
DOCKER = "embyserver"

NFO_DATA = {
    "Apocalis'napoli": {
        "year": 2025,
        "plot": "Ciro, segnato da un passato criminale, cerca vendetta dopo l'uccisione del padre e del fratello, innescando uno scontro tra fazioni della camorra napoletana.",
    },
    "Estonia Coastal Route": {
        "year": 2026,
        "plot": "Documentario sul percorso costiero dell'Estonia.",
    },
    "Formula criminale": {
        "year": 2025,
        "plot": "Thriller poliziesco italiano su un commissario che indaga sul traffico di fentanyl.",
    },
    "Geordie Stories Nathan & Dad": {
        "year": 2025,
        "plot": "Documentario che segue Nathan Henry e suo padre Glen dopo la diagnosi di un tumore terminale.",
    },
    "Il delitto di Avetrana": {
        "year": 2018,
        "plot": "Documentario della serie Tutta la verità sul caso Sarah Scazzi (Discovery/Nove, 2018).",
    },
    "Ritorno al Breithorn": {
        "year": 2024,
        "plot": "Documentario di Dario Tubaldo e Luca Cusani sul ritorno sul Breithorn dieci anni dopo una valanga.",
    },
}


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120).read())


def post(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    urllib.request.urlopen(urllib.request.Request(url, data=b"", headers=HEADERS, method="POST"), timeout=120).read()


def uid() -> str:
    return get("/emby/Users")[0]["Id"]


def write_nfo(folder: Path, title: str, year: int, plot: str, imdb: str | None = None) -> str:
    root = ET.Element("movie")
    ET.SubElement(root, "title").text = title
    ET.SubElement(root, "year").text = str(year)
    ET.SubElement(root, "plot").text = plot
    if imdb:
        uid_el = ET.SubElement(root, "uniqueid", attrib={"type": "imdb", "default": "true"})
        uid_el.text = imdb
    content = b'<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n' + ET.tostring(root, encoding="utf-8")
    remote = str(folder / "movie.nfo")
    proc = subprocess.run(
        ["docker", "exec", "-i", DOCKER, "tee", remote],
        input=content,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode() or f"failed writing {remote}")
    return remote


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    user = uid()
    for item_id in data["Film"]["missing_ids"]:
        item = get(f"/emby/Users/{user}/Items/{item_id}", {"Fields": "Name,Path"})
        name = item["Name"]
        path = item.get("Path") or ""
        folder = Path(path).parent
        meta = NFO_DATA.get(name)
        if not meta:
            print(f"SKIP {name}: no nfo template")
            continue
        nfo = write_nfo(folder, name, meta["year"], meta["plot"], meta.get("imdb"))
        print(f"NFO {nfo}")
        post(
            f"/emby/Items/{item_id}/Refresh",
            {
                "Recursive": "false",
                "MetadataRefreshMode": "FullRefresh",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "true",
            },
        )
        time.sleep(3)
    print("Done")


if __name__ == "__main__":
    main()

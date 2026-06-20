#!/usr/bin/env python3
"""Report series/movies missing metadata in Emby libraries."""
from __future__ import annotations

import json
import sqlite3
import urllib.parse
import urllib.request

from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DB = "/var/lib/emby_config/data/library.db"
HEADERS = {"X-Emby-Token": KEY}


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120).read())


def fetch_items(parent_id: str, item_type: str) -> list[dict]:
    uid = get("/emby/Users")[0]["Id"]
    items: list[dict] = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{uid}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags,Path,ProductionYear",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    return items


def has_metadata(item: dict) -> bool:
    providers = item.get("ProviderIds") or {}
    has_id = any(providers.get(k) for k in ("Tmdb", "Tvdb", "Imdb"))
    has_overview = bool((item.get("Overview") or "").strip())
    has_image = bool(item.get("PrimaryImageTag") or item.get("ImageTags"))
    return has_id and (has_overview or has_image)


def main():
    libs = {l["Name"]: l for l in get("/emby/Library/VirtualFolders")}
    report: dict[str, dict] = {}

    for lib_name, item_type in [("Serie Tv", "Series"), ("Film", "Movie")]:
        lib = libs.get(lib_name)
        if not lib:
            print(f"Libreria assente: {lib_name}")
            continue
        items = fetch_items(lib["ItemId"], item_type)
        missing = [i for i in items if not has_metadata(i)]
        partial = [
            i
            for i in missing
            if (i.get("Overview") or "").strip()
            and not any((i.get("ProviderIds") or {}).get(k) for k in ("Tmdb", "Tvdb", "Imdb"))
        ]
        report[lib_name] = {"total": len(items), "missing": missing, "partial": partial}
        print(f"\n=== {lib_name} ({item_type}) ===")
        print(f"  Totale: {len(items)}")
        print(f"  Senza metadati: {len(missing)}")
        if partial:
            print(f"  Di cui con sola trama locale (no TMDB): {len(partial)}")
        for i in missing[:12]:
            p = i.get("Path") or ""
            print(f"    - {i.get('Name')} ({i.get('ProductionYear') or '?'}) | {p[:70]}")

    # DB cross-check
    conn = sqlite3.connect(DB)
    no_tmdb_series = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=6 AND Path LIKE '/data/tv/%' "
        "AND (ProviderIds IS NULL OR ProviderIds NOT LIKE '%Tmdb=%')"
    ).fetchone()[0]
    no_tmdb_movies = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=3 AND Path LIKE '/data/movies/%' "
        "AND (ProviderIds IS NULL OR ProviderIds NOT LIKE '%Tmdb=%')"
    ).fetchone()[0]
    conn.close()
    print(f"\n=== DB ===")
    print(f"  Serie /data/tv senza TMDB: {no_tmdb_series}")
    print(f"  Film /data/movies senza TMDB: {no_tmdb_movies}")

    out = Path(__file__).with_name("missing_metadata_report.json")
    out.write_text(
        json.dumps(
            {
                k: {
                    "total": v["total"],
                    "missing_ids": [i["Id"] for i in v["missing"]],
                    "missing_names": [i.get("Name") for i in v["missing"]],
                }
                for k, v in report.items()
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nReport salvato: {out}")


if __name__ == "__main__":
    main()

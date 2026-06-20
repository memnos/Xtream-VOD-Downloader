#!/usr/bin/env python3
"""Refresh metadata for series/movies missing TMDB/poster/overview."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
HEADERS = {"X-Emby-Token": KEY}
REPORT = Path(__file__).with_name("missing_metadata_report.json")
BATCH = 25


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120).read())


def post(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, data=b"", headers=HEADERS, method="POST")
    urllib.request.urlopen(req, timeout=120).read()


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
                "Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags,Path",
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


def refresh_item(item_id: str) -> None:
    post(
        f"/emby/Items/{item_id}/Refresh",
        {
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "Default",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
        },
    )


def wait_idle(timeout: int = 7200) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = get("/emby/ScheduledTasks")
        active = [t for t in tasks if t.get("State") == "Running" and "scan" in (t.get("Name") or "").lower()]
        if not active:
            return
        print(f"  Scan attivo: {active[0].get('Name')} {active[0].get('CurrentProgressPercentage', 0):.0f}%")
        time.sleep(15)


def collect_missing() -> dict[str, list[str]]:
    libs = {l["Name"]: l for l in get("/emby/Library/VirtualFolders")}
    out: dict[str, list[str]] = {}
    for lib_name, item_type in [("Serie Tv", "Series"), ("Film", "Movie")]:
        lib = libs.get(lib_name)
        if not lib:
            continue
        items = fetch_items(lib["ItemId"], item_type)
        missing = [i["Id"] for i in items if not has_metadata(i)]
        out[lib_name] = missing
        print(f"{lib_name}: {len(missing)}/{len(items)} senza metadati")
    return out


def main():
    missing = collect_missing()
    all_ids: list[str] = []
    for ids in missing.values():
        all_ids.extend(ids)

    if not all_ids:
        print("Nessun elemento senza metadati.")
        return 0

    print(f"\nRefresh mirato su {len(all_ids)} elementi...")
    for i in range(0, len(all_ids), BATCH):
        batch = all_ids[i : i + BATCH]
        for item_id in batch:
            try:
                refresh_item(item_id)
            except Exception as exc:
                print(f"  skip {item_id}: {exc}")
        print(f"  batch {i // BATCH + 1}/{(len(all_ids) + BATCH - 1) // BATCH} inviato")
        time.sleep(2)

    print("\nAttendo fine task libreria...")
    wait_idle()

    # second pass for stubborn items
    still = collect_missing()
    stubborn = [i for ids in still.values() for i in ids]
    if stubborn:
        print(f"\nSecondo passaggio su {len(stubborn)} elementi ancora senza metadati...")
        for item_id in stubborn[:100]:
            try:
                refresh_item(item_id)
            except Exception:
                pass
        wait_idle(timeout=3600)

    final = collect_missing()
    print("\n=== Risultato finale ===")
    for lib, ids in final.items():
        print(f"  {lib}: ancora senza metadati = {len(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

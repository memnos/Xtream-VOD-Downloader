#!/usr/bin/env python3
"""Identify and apply metadata for items missing TMDB via Emby RemoteSearch."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
HEADERS = {"X-Emby-Token": KEY, "Content-Type": "application/json"}
YEAR_RE = re.compile(r"\((\d{4})\)\s*$")


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120).read())


def post(path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


def uid() -> str:
    return get("/emby/Users")[0]["Id"]


def parse_year(name: str, path: str) -> int | None:
    for text in (path, name):
        m = YEAR_RE.search(text)
        if m:
            return int(m.group(1))
    return None


def clean_name(name: str) -> str:
    return YEAR_RE.sub("", name).strip()


def has_metadata(item: dict) -> bool:
    providers = item.get("ProviderIds") or {}
    has_id = any(providers.get(k) for k in ("Tmdb", "Tvdb", "Imdb"))
    has_overview = bool((item.get("Overview") or "").strip())
    has_image = bool(item.get("PrimaryImageTag") or item.get("ImageTags"))
    return has_id and (has_overview or has_image)


def fetch_items(parent_id: str, item_type: str) -> list[dict]:
    user = uid()
    items: list[dict] = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags,Path,ProductionYear,Name",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    return items


def pick_best_result(results: list[dict], name: str, year: int | None, item_type: str = "Series") -> dict | None:
    if not results:
        return None
    base = clean_name(name).casefold()
    parts = [p.strip().casefold() for p in clean_name(name).split(" - ") if p.strip()]
    scored: list[tuple[int, dict]] = []
    for r in results:
        score = 0
        rname = (r.get("Name") or "").casefold()
        rorig = (r.get("OriginalTitle") or "").casefold()
        if rname == base or rorig == base:
            score += 100
        elif any(rname == p or rorig == p for p in parts):
            score += 90
        elif rname.startswith(base) or base.startswith(rname):
            score += 50
        if year and r.get("ProductionYear") == year:
            score += 40
        elif year and r.get("ProductionYear") and r.get("ProductionYear") != year:
            score -= 25
        if (r.get("ProviderIds") or {}).get("Tmdb"):
            score += 5
        scored.append((score, r))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    if item_type == "Movie" and best_score >= 50:
        return best
    if best_score < 40 and year:
        return None
    return best


def search_variants(name: str, path: str, item_type: str) -> list[tuple[str, int | None]]:
    year = parse_year(name, path)
    base = clean_name(name)
    variants: list[tuple[str, int | None]] = [(base, year)]
    if " - " in base:
        left, right = [p.strip() for p in base.split(" - ", 1)]
        variants.extend([(left, year), (right, year)])
    if item_type == "Movie":
        variants.append((base, None))
        if " - " in base:
            variants.append((base.split(" - ", 1)[0].strip(), None))
    seen: set[tuple[str, int | None]] = set()
    out: list[tuple[str, int | None]] = []
    for search_name, search_year in variants:
        key = (search_name.casefold(), search_year)
        if search_name and key not in seen:
            seen.add(key)
            out.append((search_name, search_year))
    return out


def remote_search_named(name: str, year: int | None, item_type: str) -> list[dict]:
    user = uid()
    body = {"SearchInfo": {"Name": name, "ProviderIds": {}, "Year": year}}
    path = f"/emby/Items/RemoteSearch/{item_type}"
    try:
        results = post(path, {"UserId": user}, body) or []
        return results if isinstance(results, list) else results.get("SearchResults", [])
    except Exception:
        return []


def remote_search(item: dict, item_type: str) -> list[dict]:
    name = item.get("Name") or ""
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for search_name, search_year in search_variants(name, item.get("Path") or "", item_type):
        for result in remote_search_named(search_name, search_year, item_type):
            tmdb = (result.get("ProviderIds") or {}).get("Tmdb")
            key = tmdb or f"{result.get('Name')}|{result.get('ProductionYear')}"
            if key in seen_ids:
                continue
            seen_ids.add(key)
            merged.append(result)
        if merged:
            break
    return merged


def apply_search(item_id: str, result: dict) -> bool:
    try:
        post(
            f"/emby/Items/RemoteSearch/Apply/{item_id}",
            {"ReplaceAllImages": "true"},
            result,
        )
        post(
            f"/emby/Items/{item_id}/Refresh",
            {
                "Recursive": "true",
                "MetadataRefreshMode": "FullRefresh",
                "ImageRefreshMode": "Default",
            },
        )
        return True
    except urllib.error.HTTPError:
        return False


def verify_item(item_id: str) -> bool:
    user = uid()
    try:
        item = get(
            f"/emby/Users/{user}/Items/{item_id}",
            {"Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags"},
        )
        return has_metadata(item)
    except Exception:
        return False


def process_lib(lib_name: str, item_type: str, limit: int | None = None) -> tuple[int, int]:
    libs = {l["Name"]: l for l in get("/emby/Library/VirtualFolders")}
    lib = libs.get(lib_name)
    if not lib:
        return 0, 0
    items = [i for i in fetch_items(lib["ItemId"], item_type) if not has_metadata(i)]
    if limit:
        items = items[:limit]
    ok = 0
    fail = 0
    print(f"\n=== {lib_name}: {len(items)} da identificare ===")
    for i, item in enumerate(items, 1):
        name = item.get("Name") or "?"
        year = item.get("ProductionYear") or parse_year(name, item.get("Path") or "")
        results = remote_search(item, item_type)
        best = pick_best_result(results, name, year, item_type)
        if not best:
            fail += 1
            if i <= 15 or i % 50 == 0:
                print(f"  [{i}] nessun match: {name}")
            continue
        if apply_search(item["Id"], best):
            ok += 1
            if i <= 15 or i % 50 == 0:
                print(f"  [{i}] OK: {name} -> {best.get('Name')} ({best.get('ProductionYear')})")
        else:
            fail += 1
            if i <= 15:
                print(f"  [{i}] apply fallito: {name}")
        if i % 15 == 0:
            time.sleep(2)
    return ok, fail


def refresh_libraries() -> None:
    libs = {l["Name"]: l for l in get("/emby/Library/VirtualFolders")}
    for name in ("Serie Tv", "Film"):
        lib = libs.get(name)
        if not lib:
            continue
        post(
            f"/emby/Items/{lib['ItemId']}/Refresh",
            {
                "Recursive": "true",
                "MetadataRefreshMode": "FullRefresh",
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "false",
            },
        )
        print(f"Refresh libreria {name} avviato")


def main():
    import sys

    skip_refresh = "--skip-refresh" in sys.argv
    if not skip_refresh:
        refresh_libraries()
        print("Attendo 90s per il refresh iniziale...")
        time.sleep(90)

    s_ok, s_fail = process_lib("Serie Tv", "Series")
    m_ok, m_fail = process_lib("Film", "Movie")
    print(f"\nSerie identificate: {s_ok}, fallite: {s_fail}")
    print(f"Film identificati: {m_ok}, falliti: {m_fail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

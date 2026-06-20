#!/usr/bin/env python3
"""Last-resort metadata identification with extra title variants."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
HEADERS = {"X-Emby-Token": KEY, "Content-Type": "application/json"}
REPORT = Path(__file__).with_name("missing_metadata_report.json")
YEAR_RE = re.compile(r"\((\d{4})\)\s*$")

EXTRA = {
    "Hell's Kitchen USA": ["Hell's Kitchen", "Hells Kitchen"],
    "Willy Coyote e Road Runner": ["Wile E. Coyote", "Looney Tunes", "Coyote and Road Runner"],
    "Cucina al mercato con Ruben": ["Cucina al mercato", "Ruben"],
    "The Covenant - Il patto": ["The Covenant"],
    "Duck the Halls A Mickey Mouse Christmas Special": ["Duck the Halls", "Mickey Mouse Christmas Special"],
    "Delitto In Alsazia": ["Meurtres en Alsace", "Murders in Alsace"],
    "Delitto nel Jura": ["Meurtres dans le Jura", "Murders in the Jura"],
    "Il filo della libertà": ["Il filo della liberta", "Filo della liberta"],
    "Agitando le acqua": ["Stirring the Water", "Churning the Water"],
}


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


def has_metadata(item: dict) -> bool:
    providers = item.get("ProviderIds") or {}
    has_id = any(providers.get(k) for k in ("Tmdb", "Tvdb", "Imdb"))
    has_overview = bool((item.get("Overview") or "").strip())
    has_image = bool(item.get("PrimaryImageTag") or item.get("ImageTags"))
    return has_id and (has_overview or has_image)


def fetch_item(item_id: str) -> dict:
    return get(f"/emby/Users/{uid()}/Items/{item_id}", {"Fields": "Name,Path,ProductionYear,ProviderIds,Overview,PrimaryImageTag,ImageTags"})


def search(item_type: str, name: str, year: int | None) -> list[dict]:
    path = f"/emby/Items/RemoteSearch/{item_type}"
    body = {"SearchInfo": {"Name": name, "ProviderIds": {}, "Year": year}}
    results = post(path, {"UserId": uid()}, body) or []
    return results if isinstance(results, list) else []


def apply(item_id: str, result: dict) -> None:
    post(f"/emby/Items/RemoteSearch/Apply/{item_id}", {"ReplaceAllImages": "true"}, result)
    post(
        f"/emby/Items/{item_id}/Refresh",
        {"Recursive": "true", "MetadataRefreshMode": "FullRefresh", "ImageRefreshMode": "Default"},
    )


def variants(name: str, path: str) -> list[tuple[str, int | None]]:
    year = parse_year(name, path)
    base = YEAR_RE.sub("", name).strip()
    names = [base]
    if base in EXTRA:
        names.extend(EXTRA[base])
    if " - " in base:
        names.extend([p.strip() for p in base.split(" - ")])
    out: list[tuple[str, int | None]] = []
    seen: set[str] = set()
    for n in names:
        if not n or n.casefold() in seen:
            continue
        seen.add(n.casefold())
        out.append((n, year))
        out.append((n, None))
    return out


def main():
    data = json.loads(REPORT.read_text(encoding="utf-8"))
    ok = 0
    fail = 0
    for lib_name, item_type in [("Serie Tv", "Series"), ("Film", "Movie")]:
        ids = data.get(lib_name, {}).get("missing_ids", [])
        print(f"\n=== {lib_name}: {len(ids)} ===")
        for item_id in ids:
            item = fetch_item(item_id)
            if has_metadata(item):
                continue
            name = item.get("Name") or "?"
            path = item.get("Path") or ""
            applied = False
            for search_name, year in variants(name, path):
                results = search(item_type, search_name, year)
                if not results:
                    continue
                result = results[0]
                try:
                    apply(item_id, result)
                    item2 = fetch_item(item_id)
                    if has_metadata(item2):
                        print(f"  OK {name} -> {result.get('Name')} ({result.get('ProductionYear')})")
                        ok += 1
                        applied = True
                        break
                except Exception as exc:
                    print(f"  ERR {name}: {exc}")
            if not applied:
                print(f"  FAIL {name}")
                fail += 1
            time.sleep(0.4)
    print(f"\nOK={ok} FAIL={fail}")


if __name__ == "__main__":
    main()

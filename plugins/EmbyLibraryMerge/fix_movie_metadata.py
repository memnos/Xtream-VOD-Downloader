#!/usr/bin/env python3
"""Retry movie metadata via refresh + remote search with cleaned titles."""
import json
import re
import time
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
        return r.read().decode()


def uid():
    return get("/emby/Users")[0]["Id"]


def clean_name(name: str, path: str) -> tuple[str, int | None]:
    year = None
    m = YEAR_RE.search(path) or YEAR_RE.search(name)
    if m:
        year = int(m.group(1))
    base = YEAR_RE.sub("", name).strip()
    base = re.sub(r"\s*-\s*", " ", base).strip()
    return base, year


def has_metadata(item):
    p = item.get("ProviderIds") or {}
    return any(p.get(k) for k in ("Tmdb", "Tvdb", "Imdb")) and (
        bool((item.get("Overview") or "").strip()) or bool(item.get("PrimaryImageTag") or item.get("ImageTags"))
    )


def fetch_movies(parent_id):
    user = uid()
    items = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "200",
                "Fields": "ProviderIds,Overview,PrimaryImageTag,ImageTags,Path,Name,ProductionYear",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        if start + len(chunk) >= batch.get("TotalRecordCount", 0) or not chunk:
            break
        start += len(chunk)
    return items


def search_movie(name, year):
    body = {"SearchInfo": {"Name": name, "Year": year, "ProviderIds": {}}}
    url = BASE + "/emby/Items/RemoteSearch/Movie?" + urllib.parse.urlencode({"UserId": uid()})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


def apply(item_id, result):
    post(
        f"/emby/Items/RemoteSearch/Apply/{item_id}",
        {"ReplaceAllImages": "true"},
        result,
    )
    post(
        f"/emby/Items/{item_id}/Refresh",
        {
            "Recursive": "false",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "Default",
        },
    )


def main():
    lib = next(l for l in get("/emby/Library/VirtualFolders") if l["Name"] == "Film")
    missing = [m for m in fetch_movies(lib["ItemId"]) if not has_metadata(m)]
    print(f"Film senza metadati: {len(missing)}")
    ok = 0
    for item in missing:
        name, year = clean_name(item.get("Name") or "", item.get("Path") or "")
        try:
            results = search_movie(name, year)
            if not results:
                results = search_movie(name.split(" - ")[0], year)
            if not results:
                print(f"  FAIL {item.get('Name')}")
                continue
            apply(item["Id"], results[0])
            ok += 1
            print(f"  OK {item.get('Name')} -> {results[0].get('Name')}")
        except Exception as exc:
            print(f"  ERR {item.get('Name')}: {exc}")
        time.sleep(0.3)
    print(f"Identificati: {ok}/{len(missing)}")


if __name__ == "__main__":
    main()

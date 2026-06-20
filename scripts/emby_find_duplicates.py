#!/usr/bin/env python3
"""Find duplicate movies/series in Emby libraries by provider ID."""
import json
import urllib.parse
import urllib.request
from collections import defaultdict

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
USER = "Fabio"

TARGET_LIBS = {"VOD FILM", "VOD SERIES", "Film", "Serie Tv"}


def request(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def get(path, params=None):
    return request("GET", path, params=params)


def post(path, params=None, body=None):
    return request("POST", path, params=params, body=body)


def provider_key(provider_ids):
    if not provider_ids:
        return None
    for key in ("Tmdb", "Imdb", "Tvdb", "TmdbCollection"):
        val = provider_ids.get(key)
        if val:
            return f"{key}:{val}"
    return None


def fetch_all_items(user_id, parent_id, item_type):
    items = []
    start = 0
    limit = 200
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": str(limit),
                "Fields": "Path,ProviderIds,MediaSources,ProductionYear,IndexNumber,ParentIndexNumber,SeriesName,SeriesId",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    return items


def main():
    folders = get("/emby/Library/VirtualFolders")
    users = get("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == USER)

    for lib in folders:
        name = lib.get("Name")
        if name not in TARGET_LIBS:
            continue
        ctype = lib.get("CollectionType")
        if ctype not in ("movies", "tvshows"):
            continue

        item_type = "Movie" if ctype == "movies" else "Series"
        parent_id = lib.get("ItemId")
        print(f"\n{'='*60}\nLIBRARY: {name} ({item_type})\nLocations: {lib.get('Locations')}")
        items = fetch_all_items(user_id, parent_id, item_type)
        print(f"Total {item_type}: {len(items)}")

        groups = defaultdict(list)
        no_id = []
        for item in items:
            pk = provider_key(item.get("ProviderIds") or {})
            if pk:
                groups[pk].append(item)
            else:
                no_id.append(item)

        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        print(f"Duplicate groups by provider ID: {len(dupes)}")
        for pk, group in sorted(dupes.items(), key=lambda x: -len(x[1]))[:15]:
            print(f"\n  [{pk}] x{len(group)}")
            for item in group:
                print(f"    - {item.get('Name')} | {item.get('Id')} | {item.get('Path', 'N/A')}")

        if no_id:
            print(f"\n  Items without provider ID: {len(no_id)} (sample 5)")
            for item in no_id[:5]:
                print(f"    - {item.get('Name')} | {item.get('Path', 'N/A')}")


if __name__ == "__main__":
    main()

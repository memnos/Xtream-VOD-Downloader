#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


users = get("/emby/Users")
user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")
folders = get("/emby/Library/VirtualFolders")

for lib_name in ["VOD FILM", "Film"]:
    lib = next(f for f in folders if f.get("Name") == lib_name)
    raw_opts = lib.get("LibraryOptions") or {}
    opts = raw_opts[0] if isinstance(raw_opts, list) else raw_opts
    print(f"\n=== {lib_name} library options ===")
    for key in (
        "EnableAutomaticSeriesGrouping",
        "EnableMultiVersionByFiles",
        "EnableMultiVersionByMetadata",
        "MergeTopLevelFolders",
    ):
        print(f"  {key}: {opts.get(key)}")

for title in ["Avatar", "Harry Potter e la pietra filosofale", "The Boys", "Andor"]:
    print(f"\n=== Search: {title} ===")
    for lib_name in ["VOD FILM", "VOD SERIES", "Film", "Serie Tv"]:
        lib = next((f for f in folders if f.get("Name") == lib_name), None)
        if not lib:
            continue
        ctype = lib.get("CollectionType")
        item_type = "Movie" if ctype == "movies" else "Series"
        if item_type == "Movie" and "Potter" not in title and title in ("The Boys", "Andor"):
            continue
        if item_type == "Series" and title in ("Avatar", "Harry Potter e la pietra filosofale"):
            continue
        res = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": lib["ItemId"],
                "SearchTerm": title,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "Fields": "Path,ProviderIds",
            },
        )
        items = res.get("Items", [])
        if items:
            print(f"  {lib_name}: {len(items)} hits")
            for item in items[:5]:
                tmdb = (item.get("ProviderIds") or {}).get("Tmdb", "?")
                print(f"    {item['Name']} | id={item['Id']} | tmdb={tmdb}")
                print(f"      {item.get('Path', 'N/A')}")

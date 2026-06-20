#!/usr/bin/env python3
"""Inspect Emby libraries and find duplicate movies/series."""
import json
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"


def api(path, params=None):
    url = f"{BASE}{path}"
    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{qs}"
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    folders = api("/emby/Library/VirtualFolders")
    print("=== LIBRARIES ===")
    for f in folders:
        print(f"\nName: {f.get('Name')}")
        print(f"  CollectionType: {f.get('CollectionType')}")
        print(f"  ItemId: {f.get('ItemId')}")
        for loc in f.get("Locations", []):
            print(f"  Location: {loc}")
        opts = f.get("LibraryOptions") or {}
        interesting = {
            k: opts.get(k)
            for k in (
                "EnableAutomaticSeriesGrouping",
                "EnableFolderGrouping",
                "EnableGroupedFolders",
                "AutomaticallyGroupSeries",
                "AutomaticallyMergeSeries",
                "MergeTopLevelFolders",
                "EnableMultiVersionItems",
                "AllowEmbeddedSubtitles",
            )
            if k in opts
        }
        if interesting:
            print(f"  Options: {json.dumps(interesting, indent=4)}")

    users = api("/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")
    print(f"\nUser ID: {user_id}")

    # Sample duplicates: search for a movie that exists in both paths
    for name in ["Harry Potter e la pietra filosofale (2001)", "Bohemian Rhapsody (2018)"]:
        items = api(
            f"/emby/Users/{user_id}/Items",
            {
                "SearchTerm": name.split(" (")[0][:20],
                "IncludeItemTypes": "Movie",
                "Recursive": "true",
                "Fields": "Path,ProviderIds,MediaSources",
            },
        )
        print(f"\n=== Search '{name}' -> {items.get('TotalRecordCount', 0)} results ===")
        for item in items.get("Items", [])[:5]:
            print(f"  - {item.get('Name')} | Id={item.get('Id')} | Path={item.get('Path', 'N/A')}")


if __name__ == "__main__":
    main()

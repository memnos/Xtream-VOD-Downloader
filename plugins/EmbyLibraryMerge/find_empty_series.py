#!/usr/bin/env python3
"""Find series with no seasons/episodes visible in Emby."""
import json
import pathlib
import urllib.parse
import urllib.request

cfg = json.loads(pathlib.Path("/mnt/c/Users/Fabio/Documents/Download_from_m3u/plugins/EmbyLibraryMerge/host-config.json").read_text())
BASE = cfg["emby_url"].rstrip("/")
KEY = cfg["emby_api_key"]


def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())


users = get("/emby/Users")
user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")
folders = get("/emby/Library/VirtualFolders")

empty = []
for lib in folders:
    if lib.get("CollectionType") != "tvshows":
        continue
    name = lib.get("Name")
    items = []
    start = 0
    while True:
        batch = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": lib["ItemId"],
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": "100",
                "Fields": "Path,ChildCount,ProviderIds",
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break

    for s in items:
        cc = int(s.get("ChildCount") or 0)
        eps = get(
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": s["Id"],
                "IncludeItemTypes": "Episode",
                "Recursive": "true",
                "Limit": "1",
            },
        )
        ep_count = eps.get("TotalRecordCount", 0)
        if ep_count == 0 or cc == 0:
            empty.append((name, s.get("Name"), s.get("Id"), s.get("Path"), cc, ep_count))

print(f"Series with 0 episodes or 0 children: {len(empty)}")
for row in empty[:40]:
    print(f"  [{row[0]}] {row[1]} | id={row[2]} | children={row[4]} eps={row[5]}")
    print(f"    {row[3]}")

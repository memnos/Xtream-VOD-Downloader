#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request
from collections import defaultdict

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"

def get(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode())

users = get("/emby/Users")
user_id = next(u["Id"] for u in users if u.get("Name") == "Fabio")

# Get The Boys series
series = get(
    f"/emby/Users/{user_id}/Items",
    {"SearchTerm": "The Boys", "IncludeItemTypes": "Series", "Recursive": "true", "Limit": "5"},
)
boys = next(s for s in series["Items"] if s.get("Name") == "The Boys")
print(f"Series: {boys['Name']} ({boys['Id']})")

seasons = get(
    f"/emby/Users/{user_id}/Items",
    {"ParentId": boys["Id"], "IncludeItemTypes": "Season", "Fields": "Path,IndexNumber"},
)
print(f"Seasons in API: {len(seasons.get('Items', []))}")
for s in seasons.get("Items", []):
    eps = get(
        f"/emby/Users/{user_id}/Items",
        {"ParentId": s["Id"], "IncludeItemTypes": "Episode", "Fields": "Path"},
    )
    print(f"  S{s.get('IndexNumber')} {s.get('Name')}: {eps.get('TotalRecordCount', 0)} eps | {s.get('Path')}")

# all episodes under series recursive
all_eps = get(
    f"/emby/Users/{user_id}/Items",
    {
        "ParentId": boys["Id"],
        "IncludeItemTypes": "Episode",
        "Recursive": "true",
        "Fields": "Path,ParentIndexNumber,IndexNumber",
    },
)
print(f"\nTotal episodes under series: {all_eps.get('TotalRecordCount', 0)}")
by_season = defaultdict(list)
for ep in all_eps.get("Items", []):
    by_season[ep.get("ParentIndexNumber")].append(ep.get("Path"))
for sn in sorted(by_season):
    print(f"  Season {sn}: {len(by_season[sn])} episodes")

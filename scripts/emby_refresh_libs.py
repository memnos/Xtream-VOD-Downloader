#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"

def post(path, params=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Emby-Token": API_KEY}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()

folders = json.loads(
    urllib.request.urlopen(
        urllib.request.Request(f"{BASE}/emby/Library/VirtualFolders", headers={"X-Emby-Token": API_KEY})
    ).read()
)
for lib in folders:
    if lib.get("Name") in ("VOD FILM", "VOD SERIES", "Serie Tv"):
        post(f"/emby/Items/{lib['ItemId']}/Refresh", {"Recursive": "true"})
        print(f"Refreshed {lib['Name']}")

post("/emby/Library/Refresh")
print("Triggered global library refresh")

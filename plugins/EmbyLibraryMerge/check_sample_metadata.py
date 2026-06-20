#!/usr/bin/env python3
import json
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
uid = json.loads(urllib.request.urlopen(
    urllib.request.Request(BASE + "/emby/Users", headers={"X-Emby-Token": KEY})
).read())[0]["Id"]

folders = json.loads(urllib.request.urlopen(
    urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
).read())
lib = next(f for f in folders if f["Name"] == "Serie Tv")

for term in ["Big Bang", "9-1-1", "Outlander"]:
    data = json.loads(urllib.request.urlopen(
        urllib.request.Request(
            BASE + f"/emby/Users/{uid}/Items?ParentId={lib['ItemId']}&IncludeItemTypes=Series&Recursive=true&SearchTerm={term}&Fields=ProviderIds,Overview,PrimaryImageTag,ImageTags,Path",
            headers={"X-Emby-Token": KEY},
        )
    ).read())
    for i in data.get("Items", []):
        print(term, "->", i.get("Name"))
        print("  path:", i.get("Path"))
        print("  tmdb:", (i.get("ProviderIds") or {}).get("Tmdb"))
        print("  overview:", (i.get("Overview") or "")[:80] or "(vuota)")
        print("  PrimaryImageTag:", i.get("PrimaryImageTag"))
        print("  ImageTags:", i.get("ImageTags"))

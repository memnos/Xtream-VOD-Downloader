#!/usr/bin/env python3
import json
import urllib.request

KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
req = urllib.request.Request(BASE + "/emby/Library/VirtualFolders", headers={"X-Emby-Token": KEY})
for lib in json.loads(urllib.request.urlopen(req, timeout=30).read()):
    if lib.get("Name") not in ("Serie Tv", "VOD SERIES"):
        continue
    print("=" * 50)
    print(lib["Name"], lib.get("ItemId"))
    print("Locations:", lib.get("Locations"))
    opts = lib.get("LibraryOptions") or {}
    keys = [
        "EnableLocalMetadata",
        "EnableInternetProviders",
        "SaveLocalMetadata",
        "SaveMetadataHidden",
        "MetadataSavers",
        "DisabledLocalMetadataReaders",
        "DisabledMetadataFetchers",
        "LocalMetadataReaderOrder",
        "MetadataFetcherOrder",
        "PreferredMetadataLanguage",
        "MetadataCountryCode",
        "RequirePerfectMatch",
        "EnableAutomaticSeriesGrouping",
        "AutomaticallyMergeSeries",
    ]
    for k in keys:
        if k in opts:
            print(f"  {k}: {opts[k]}")

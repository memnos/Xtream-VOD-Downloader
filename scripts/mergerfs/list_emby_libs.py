#!/usr/bin/env python3
import json
import urllib.parse
import urllib.request

API = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
headers = {"X-Emby-Token": API, "Content-Type": "application/json"}


def get(path):
    req = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def post(path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


libs = get("/emby/Library/VirtualFolders")
for lib in libs:
    locs = lib.get("Locations") or []
    print(f"{lib.get('Name')} | {lib.get('CollectionType')} | id={lib.get('ItemId')} | {locs}")

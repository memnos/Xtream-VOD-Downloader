#!/usr/bin/env python3
import json
import urllib.request
from pathlib import Path

cfg = json.loads(Path(__file__).with_name("host-config.json").read_text(encoding="utf-8"))
url = cfg["emby_url"]
key = cfg["emby_api_key"]
sid = 5899873

# get user id
users = json.loads(urllib.request.urlopen(f"{url}/emby/Users?api_key={key}").read())
uid = users[0]["Id"]

for endpoint, label in [
    (f"/emby/Shows/{sid}/Seasons?UserId={uid}&api_key={key}", "Seasons"),
    (f"/emby/Shows/{sid}/Episodes?UserId={uid}&api_key={key}", "Episodes"),
]:
    data = json.loads(urllib.request.urlopen(url + endpoint).read())
    print(f"{label}: TotalRecordCount={data.get('TotalRecordCount', len(data.get('Items', [])))}")
    for item in data.get("Items", [])[:12]:
        print(f"  {item.get('Name')} S{item.get('ParentIndexNumber')} idx={item.get('IndexNumber')}")

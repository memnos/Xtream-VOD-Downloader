#!/usr/bin/env python3
"""
Unite duplicate Emby series (same TMDB) keeping ALL episode sources.

Uses Emby MergeVersions API:
- Series with same TMDB -> merge into one entry (all seasons/episodes kept)
- Episodes with same series + SxxExx -> merge strm + local mkv as multi-version

Does NOT delete strm entries.
"""
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
USER = "Fabio"
TV_LIBS = {"VOD SERIES", "Serie Tv"}
CONFIG = Path(__file__).resolve().parents[1] / "plugins" / "EmbyLibraryMerge" / "host-config.json"


def api(method: str, path: str, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def provider_key(provider_ids: dict | None) -> str | None:
    if not provider_ids:
        return None
    for key in ("Tmdb", "Tvdb", "Imdb"):
        val = provider_ids.get(key)
        if val:
            return f"{key}:{val}"
    return None


def fetch_all_items(user_id: str, parent_id: str, item_type: str, fields: str) -> list[dict]:
    items: list[dict] = []
    start = 0
    limit = 200
    while True:
        batch = api(
            "GET",
            f"/emby/Users/{user_id}/Items",
            {
                "ParentId": parent_id,
                "IncludeItemTypes": item_type,
                "Recursive": "true",
                "StartIndex": str(start),
                "Limit": str(limit),
                "Fields": fields,
            },
        )
        chunk = batch.get("Items", [])
        items.extend(chunk)
        total = batch.get("TotalRecordCount", 0)
        start += len(chunk)
        if start >= total or not chunk:
            break
    return items


def path_score(path: str) -> int:
    p = (path or "").lower()
    if p.startswith("/data/tv/") and not p.startswith("/data/tv-2/"):
        return 300
    if p.startswith("/data/tv-2/"):
        return 200
    if "/strm/" in p:
        return 100
    return 50


def merge_versions(ids: list[str], dry_run: bool, label: str) -> bool:
    if len(ids) < 2:
        return False
    print(f"  MERGE {label}: {len(ids)} items -> {ids[0]}")
    for iid in ids[1:]:
        print(f"    + {iid}")
    if dry_run:
        return True
    try:
        api("POST", "/emby/Videos/MergeVersions", params={"Ids": ",".join(ids)})
        return True
    except Exception as exc:
        print(f"    FAILED: {exc}")
        return False


def merge_series_groups(user_id: str, lib_id: str, lib_name: str, dry_run: bool) -> int:
    series = fetch_all_items(user_id, lib_id, "Series", "Path,ProviderIds,ChildCount")
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in series:
        pk = provider_key(item.get("ProviderIds"))
        if pk:
            groups[pk].append(item)

    merged = 0
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\n=== {lib_name}: {len(dupes)} duplicate series groups ===")
    for pk, group in sorted(dupes.items()):
        ordered = sorted(
            group,
            key=lambda s: (path_score(s.get("Path", "")), int(s.get("ChildCount") or 0)),
            reverse=True,
        )
        ids = [s["Id"] for s in ordered]
        print(f"[{pk}]")
        for s in ordered:
            print(f"  {s['Id']} {s['Name']} @ {s.get('Path')}")
        if merge_versions(ids, dry_run, f"series {pk}"):
            merged += 1
    return merged


def merge_episode_groups(user_id: str, lib_id: str, lib_name: str, dry_run: bool) -> int:
    episodes = fetch_all_items(
        user_id,
        lib_id,
        "Episode",
        "Path,ProviderIds,ParentIndexNumber,IndexNumber,SeriesId,MediaSources",
    )
    groups: dict[str, list[dict]] = defaultdict(list)
    for ep in episodes:
        sid = ep.get("SeriesId")
        season = ep.get("ParentIndexNumber")
        number = ep.get("IndexNumber")
        if sid is None or season is None or number is None:
            continue
        key = f"{sid}|{int(season)}|{int(number)}"
        groups[key].append(ep)

    merged = 0
    dupes = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"\n=== {lib_name}: {len(dupes)} duplicate episode groups ===")
    for key, group in sorted(dupes.items()):
        ordered = sorted(
            group,
            key=lambda e: (
                200 if (e.get("Path") or "").lower().endswith((".mkv", ".mp4")) else 100,
                len(e.get("MediaSources") or []),
            ),
            reverse=True,
        )
        ids = [e["Id"] for e in ordered]
        sample = ordered[0]
        label = (
            f"S{int(sample.get('ParentIndexNumber', 0)):02d}"
            f"E{int(sample.get('IndexNumber', 0)):02d}"
        )
        paths = [e.get("Path", "") for e in ordered]
        unique_paths = {p.lower() for p in paths if p}
        if len(unique_paths) < 2:
            print(f"  SKIP {label}: same path or missing path ({len(group)} entries)")
            continue
        print(f"  {label}:")
        for p in paths:
            print(f"    {p}")
        if merge_versions(ids, dry_run, f"episode {label}"):
            merged += 1
    return merged


def refresh_libraries(lib_ids: list[str], dry_run: bool) -> None:
    print("\n=== Refresh libraries ===")
    for lib_id in lib_ids:
        if dry_run:
            print(f"  Would refresh {lib_id}")
            continue
        api(
            "POST",
            f"/emby/Items/{lib_id}/Refresh",
            params={
                "Recursive": "true",
                "MetadataRefreshMode": "Default",
                "ImageRefreshMode": "Default",
            },
        )
        print(f"  Refreshed {lib_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--series-only", action="store_true")
    parser.add_argument("--episodes-only", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply
    if dry_run:
        print("DRY RUN — pass --apply to execute\n")

    users = api("GET", "/emby/Users")
    user_id = next(u["Id"] for u in users if u.get("Name") == USER)
    folders = api("GET", "/emby/Library/VirtualFolders")

    series_merged = 0
    episodes_merged = 0
    refreshed: list[str] = []

    for lib in folders:
        name = lib.get("Name")
        if name not in TV_LIBS or lib.get("CollectionType") != "tvshows":
            continue
        lib_id = lib.get("ItemId") or lib.get("Id")
        refreshed.append(lib_id)
        if not args.episodes_only:
            series_merged += merge_series_groups(user_id, lib_id, name, dry_run)
        if not args.series_only:
            episodes_merged += merge_episode_groups(user_id, lib_id, name, dry_run)

    refresh_libraries(refreshed, dry_run)
    print(
        f"\nSummary: series merges={series_merged}, episode version merges={episodes_merged}, "
        f"libraries refreshed={len(refreshed)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

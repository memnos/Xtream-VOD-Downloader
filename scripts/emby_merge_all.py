#!/usr/bin/env python3
"""Merge duplicate Emby movies via API and series via DB (with container stop)."""
import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

API_KEY = "dded6e2e923b4aeeafefd00e5f25c7a0"
BASE = "http://127.0.0.1:8096"
DB_PATH = "/var/lib/emby_config/data/library.db"
CONTAINER = "embyserver"
MOVIE_LIB_IDS = {"114872"}  # VOD FILM
SERIES_LIB_NAMES = {"VOD SERIES", "Serie Tv"}


def api(method, path, params=None, body=None):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": API_KEY, "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def run(cmd):
    return subprocess.run(cmd, shell=True, check=True, text=True, capture_output=True)


def tmdb_from_provider_ids(raw):
    if not raw:
        return None
    for part in raw.split("|"):
        if part.lower().startswith("tmdb="):
            return part.split("=", 1)[1]
    return None


def series_score(path, episode_count):
    path = (path or "").lower()
    if path.startswith("/data/tv/") and not path.startswith("/data/tv-2/"):
        tier = 300
    elif path.startswith("/data/tv-2/"):
        tier = 200
    elif "/strm/" in path:
        tier = 100
    else:
        tier = 0
    return tier * 10000 + episode_count


def find_movie_duplicates(conn):
    rows = conn.execute(
        """
        SELECT Id, Name, Path, ProviderIds, ParentId, PresentationUniqueKey
        FROM MediaItems
        WHERE type = 5
        """
    ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        tmdb = tmdb_from_provider_ids(row[3])
        if tmdb:
            groups[tmdb].append(row)
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_series_duplicates(conn):
    rows = conn.execute(
        """
        SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey
        FROM MediaItems
        WHERE type = 6
        """
    ).fetchall()
    groups = defaultdict(list)
    for row in rows:
        tmdb = tmdb_from_provider_ids(row[3])
        if tmdb:
            groups[tmdb].append(row)
    dupes = {}
    for k, v in groups.items():
        if len(v) > 1:
            scored = []
            for row in v:
                ep_count = conn.execute(
                    "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=8",
                    (row[0],),
                ).fetchone()[0]
                scored.append((series_score(row[2], ep_count), ep_count, row))
            scored.sort(reverse=True)
            dupes[k] = scored
    return dupes


def movie_score(path):
    path = (path or "").lower()
    if path.startswith("/data/movies/"):
        return 200
    if "/strm/" in path:
        return 100
    return 0


def merge_movies(groups, dry_run):
    merged = 0
    for tmdb, items in sorted(groups.items()):
        items = sorted(items, key=lambda r: movie_score(r[2]), reverse=True)
        ids = [str(r[0]) for r in items]
        keep = items[0]
        print(f"  MOVIE Tmdb={tmdb}: merge {len(items)} -> keep {keep[1]} ({keep[0]})")
        for r in items[1:]:
            print(f"    + {r[2]}")
        if not dry_run:
            api("POST", "/emby/Videos/MergeVersions", params={"Ids": ",".join(ids)})
        merged += 1
    return merged


def merge_series_in_db(conn, groups, dry_run):
    merged_groups = 0
    removed_roots = 0
    for tmdb, scored in sorted(groups.items()):
        primary = scored[0][2]
        primary_id = primary[0]
        primary_key = primary[4]
        dups = [row for _, _, row in scored[1:]]
        if not dups:
            continue
        print(f"  SERIES Tmdb={tmdb}: keep {primary[1]} ({primary_id}) @ {primary[2]}")
        for dup in dups:
            dup_id = dup[0]
            print(f"    merge {dup[1]} ({dup_id}) @ {dup[2]}")
            if dry_run:
                continue
            conn.execute(
                "UPDATE MediaItems SET ParentId=? WHERE ParentId=?",
                (primary_id, dup_id),
            )
            conn.execute(
                "UPDATE MediaItems SET SeriesId=? WHERE SeriesId=?",
                (primary_id, dup_id),
            )
            conn.execute(
                "UPDATE MediaItems SET SeriesName=? WHERE SeriesId=?",
                (primary[1], primary_id),
            )
            if primary_key:
                conn.execute(
                    "UPDATE MediaItems SET PresentationUniqueKey=? WHERE Id=?",
                    (primary_key, primary_id),
                )
            conn.execute("DELETE FROM AncestorIds2 WHERE ItemId=? OR AncestorId=?", (dup_id, dup_id))
            conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=6", (dup_id,))
            removed_roots += 1
        merged_groups += 1
    if not dry_run:
        conn.commit()
    return merged_groups, removed_roots


def refresh_libraries():
    folders = api("GET", "/emby/Library/VirtualFolders")
    for lib in folders:
        if lib.get("Name") in SERIES_LIB_NAMES or lib.get("ItemId") in MOVIE_LIB_IDS:
            lib_id = lib["ItemId"]
            api(
                "POST",
                f"/emby/Items/{lib_id}/Refresh",
                params={"Recursive": "true", "MetadataRefreshMode": "DefaultRefresh"},
            )
            print(f"  Refreshed {lib.get('Name')}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    if dry_run:
        print("DRY RUN - use --apply to execute\n")

    conn = sqlite3.connect(DB_PATH)
    movie_groups = find_movie_duplicates(conn)
    series_groups = find_series_duplicates(conn)
    print(f"Movie duplicate groups (all libraries): {len(movie_groups)}")
    print(f"Series duplicate groups (all libraries): {len(series_groups)}")

    print("\n=== MERGE MOVIES (API) ===")
    movie_merged = merge_movies(movie_groups, dry_run)

    print("\n=== MERGE SERIES (DB) ===")
    if dry_run:
        merge_series_in_db(conn, series_groups, True)
        print(f"Would merge {len(series_groups)} series groups")
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{DB_PATH}.bak-{ts}"
        print(f"  Backing up DB to {backup}")
        run(f"docker stop {CONTAINER}")
        shutil.copy2(DB_PATH, backup)
        shutil.copy2(f"{DB_PATH}-wal", f"{backup}-wal") if Path(f"{DB_PATH}-wal").exists() else None
        conn.close()
        conn = sqlite3.connect(DB_PATH)
        groups, removed = merge_series_in_db(conn, series_groups, False)
        conn.close()
        print(f"  Merged {groups} series groups, removed {removed} duplicate roots")
        run(f"docker start {CONTAINER}")
        print("  Waiting for Emby to start...")
        time.sleep(20)

    conn = sqlite3.connect(DB_PATH)
    print("\n=== AFTER (DB counts) ===")
    print(f"  Movie duplicate groups remaining: {len(find_movie_duplicates(conn))}")
    print(f"  Series duplicate groups remaining: {len(find_series_duplicates(conn))}")
    conn.close()

    if not dry_run:
        print("\n=== REFRESH LIBRARIES ===")
        time.sleep(5)
        refresh_libraries()


if __name__ == "__main__":
    main()

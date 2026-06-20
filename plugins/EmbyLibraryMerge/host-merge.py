#!/usr/bin/env python3
"""
Fallback CLI per unire duplicati Emby (MKV + STRM, serie multi-cartella).
Usalo se il plugin DLL non è installato o preferisci lanciare da WSL.

Config: plugins/EmbyLibraryMerge/host-config.json (copiato da host-config.example.json)
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = SCRIPT_DIR / "host-config.json"


def load_config(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def api(cfg: dict, method: str, path: str, params=None, body=None):
    base = cfg["emby_url"].rstrip("/")
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params, safe=",")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"X-Emby-Token": cfg["emby_api_key"], "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def tmdb(raw: str | None) -> str | None:
    if not raw:
        return None
    for part in raw.split("|"):
        if part.lower().startswith("tmdb="):
            return part.split("=", 1)[1]
    return None


def movie_score(path: str | None, prefer_local: bool) -> int:
    path = (path or "").lower()
    if prefer_local and path.startswith("/data/movies/"):
        return 200
    if "/strm/" in path:
        return 100
    return 50


def series_score(path: str | None) -> int:
    path = (path or "").lower()
    if path.startswith("/data/tv/") and not path.startswith("/data/tv-2/"):
        return 300
    if path.startswith("/data/tv-2/"):
        return 200
    if "/strm/" in path:
        return 100
    return 50


def norm_name(name: str | None) -> str:
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", (name or "").strip()).lower()
    return re.sub(r"\s+", " ", name)


def find_movie_groups(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE type = 5"
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        key = tmdb(row[3])
        if key:
            groups[key].append(row)
    return {k: v for k, v in groups.items() if len(v) > 1}


def find_series_groups(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey FROM MediaItems WHERE type = 6"
    ).fetchall()
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        key = tmdb(row[3])
        if key:
            groups[key].append(row)
    result = {}
    for key, items in groups.items():
        if len(items) < 2:
            continue
        scored = []
        for item in items:
            ep = conn.execute(
                "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=8", (item[0],)
            ).fetchone()[0]
            scored.append((series_score(item[2]) * 10000 + ep, item))
        scored.sort(reverse=True)
        result[key] = [x[1] for x in scored]
    return result


def merge_movies(cfg: dict, groups: dict, dry_run: bool) -> int:
    count = 0
    for key, items in groups.items():
        items = sorted(items, key=lambda r: movie_score(r[2], cfg.get("prefer_local_movies", True)), reverse=True)
        ids = ",".join(str(r[0]) for r in items)
        print(f"  MOVIE TMDB={key}: {len(items)} -> keep {items[0][1]}")
        if not dry_run:
            api(cfg, "POST", "/emby/Videos/MergeVersions", params={"Ids": ids})
        count += 1
    return count


def merge_series_db(conn: sqlite3.Connection, groups: dict, dry_run: bool) -> tuple[int, int]:
    groups_merged = 0
    removed = 0
    for key, items in groups.items():
        primary = items[0]
        primary_id = primary[0]
        primary_key = primary[4]
        print(f"  SERIES TMDB={key}: keep {primary[1]} @ {primary[2]}")
        for dup in items[1:]:
            print(f"    + merge {dup[1]} @ {dup[2]}")
            if dry_run:
                continue
            dup_id = dup[0]
            conn.execute("UPDATE MediaItems SET ParentId=? WHERE ParentId=?", (primary_id, dup_id))
            conn.execute("UPDATE MediaItems SET SeriesId=? WHERE SeriesId=?", (primary_id, dup_id))
            conn.execute("UPDATE MediaItems SET SeriesName=? WHERE SeriesId=?", (primary[1], primary_id))
            if primary_key:
                conn.execute(
                    "UPDATE MediaItems SET PresentationUniqueKey=? WHERE Id=?",
                    (primary_key, primary_id),
                )
            conn.execute("DELETE FROM AncestorIds2 WHERE ItemId=? OR AncestorId=?", (dup_id, dup_id))
            conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=6", (dup_id,))
            removed += 1
        groups_merged += 1
    if not dry_run:
        conn.commit()
    return groups_merged, removed


def merge_orphans(conn: sqlite3.Connection, dry_run: bool) -> int:
    rows = conn.execute(
        "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE type=6"
    ).fetchall()
    with_tmdb = [r for r in rows if tmdb(r[3])]
    without = [r for r in rows if not tmdb(r[3])]
    by_name = defaultdict(list)
    for row in with_tmdb:
        by_name[norm_name(row[1])].append(row)
    removed = 0
    for orphan in without:
        cands = by_name.get(norm_name(orphan[1]))
        if not cands:
            continue
        primary = sorted(cands, key=lambda r: series_score(r[2]), reverse=True)[0]
        print(f"  ORPHAN {orphan[1]} -> {primary[1]}")
        if dry_run:
            continue
        conn.execute("UPDATE MediaItems SET ParentId=? WHERE ParentId=?", (primary[0], orphan[0]))
        conn.execute("UPDATE MediaItems SET SeriesId=? WHERE SeriesId=?", (primary[0], orphan[0]))
        conn.execute("DELETE FROM AncestorIds2 WHERE ItemId=? OR AncestorId=?", (orphan[0], orphan[0]))
        conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=6", (orphan[0],))
        removed += 1
    if not dry_run:
        conn.commit()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Unisce duplicati Emby (MKV + STRM)")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--movies-only", action="store_true")
    parser.add_argument("--series-only", action="store_true")
    args = parser.parse_args()

    if not args.config.exists():
        print(f"Config mancante: {args.config}", file=sys.stderr)
        print("Copia host-config.example.json -> host-config.json", file=sys.stderr)
        return 1

    dry_run = not args.apply
    cfg = load_config(args.config)
    db_path = cfg["library_db"]
    container = cfg.get("docker_container", "embyserver")

    conn = sqlite3.connect(db_path)
    movie_groups = {} if args.series_only else find_movie_groups(conn)
    series_groups = {} if args.movies_only else find_series_groups(conn)
    print(f"Movie groups: {len(movie_groups)} | Series groups: {len(series_groups)}")

    if dry_run:
        print("\nDRY RUN — usa --apply per eseguire\n")

    if movie_groups:
        print("\n=== MOVIES ===")
        merge_movies(cfg, movie_groups, dry_run)

    if series_groups or (not args.movies_only and cfg.get("merge_orphans", True)):
        print("\n=== SERIES ===")
        if not dry_run and series_groups:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup = f"{db_path}.bak-{ts}"
            print(f"  Backup DB -> {backup}")
            subprocess.run(f"docker stop {container}", shell=True, check=True)
            shutil.copy2(db_path, backup)
            conn.close()
            conn = sqlite3.connect(db_path)
            merge_series_db(conn, series_groups, False)
            if cfg.get("merge_orphans", True):
                orphans = merge_orphans(conn, False)
                print(f"  Orphans removed: {orphans}")
            conn.close()
            subprocess.run(f"docker start {container}", shell=True, check=True)
            time.sleep(15)
        else:
            merge_series_db(conn, series_groups, dry_run)
            if cfg.get("merge_orphans", True):
                merge_orphans(conn, dry_run)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

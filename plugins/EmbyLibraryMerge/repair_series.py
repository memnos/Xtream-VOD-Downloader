#!/usr/bin/env python3
"""
Repair Emby TV series duplicates without losing seasons/episodes.

Strategy:
1. Group series (type=6) by TMDB id
2. Keep one primary series per group
3. Repoint all seasons/episodes to primary (SeriesId + season ParentId)
4. Merge duplicate seasons (same series + season number) into one season
5. Rebuild AncestorIds2 for affected items
6. Remove empty duplicate series roots (metadata only)
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

TYPE_SERIES = 6
TYPE_SEASON = 7
TYPE_EPISODE = 8


def tmdb(raw: str | None) -> str | None:
    if not raw:
        return None
    for part in raw.split("|"):
        if part.lower().startswith("tmdb="):
            return part.split("=", 1)[1]
    return None


def series_score(path: str | None) -> int:
    path = (path or "").lower()
    if path.startswith("/data/tv/") and not path.startswith("/data/tv-2/"):
        return 300
    if path.startswith("/data/tv-2/"):
        return 200
    if "/strm/" in path:
        return 100
    return 50


def load_config(path: Path) -> dict:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def fetch_series(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey, ParentIndexNumber "
        "FROM MediaItems WHERE type=?",
        (TYPE_SERIES,),
    ).fetchall()


def fetch_children(conn: sqlite3.Connection, parent_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT Id, type, Name, Path, ParentId, SeriesId, ParentIndexNumber, PresentationUniqueKey "
        "FROM MediaItems WHERE ParentId=?",
        (parent_id,),
    ).fetchall()


def fetch_by_series(conn: sqlite3.Connection, series_id: int) -> list[tuple]:
    return conn.execute(
        "SELECT Id, type, Name, Path, ParentId, SeriesId, ParentIndexNumber "
        "FROM MediaItems WHERE SeriesId=? AND type IN (?, ?)",
        (series_id, TYPE_SEASON, TYPE_EPISODE),
    ).fetchall()


def delete_ancestors(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM AncestorIds2 WHERE ItemId=?", (item_id,))


def rebuild_ancestors(conn: sqlite3.Connection, item_id: int) -> None:
    delete_ancestors(conn, item_id)
    chain: list[int] = []
    current = item_id
    seen = set()
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        row = conn.execute("SELECT ParentId FROM MediaItems WHERE Id=?", (current,)).fetchone()
        if not row or not row[0]:
            break
        current = row[0]
    for distance, ancestor_id in enumerate(reversed(chain)):
        conn.execute(
            "INSERT OR IGNORE INTO AncestorIds2 (ItemId, AncestorId, Distance) VALUES (?,?,?)",
            (item_id, ancestor_id, distance),
        )


def rebuild_ancestors_subtree(conn: sqlite3.Connection, root_id: int) -> None:
    stack = [root_id]
    while stack:
        iid = stack.pop()
        rebuild_ancestors(conn, iid)
        children = conn.execute("SELECT Id FROM MediaItems WHERE ParentId=?", (iid,)).fetchall()
        stack.extend(c[0] for c in children)


def episode_count(conn: sqlite3.Connection, series_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=?",
        (series_id, TYPE_EPISODE),
    ).fetchone()
    return int(row[0]) if row else 0


def pick_primary(conn: sqlite3.Connection, items: list[tuple]) -> list[tuple]:
    """Pick metadata primary only; strm episodes are moved, not deleted. Run unite_series_versions.py after."""
    return sorted(
        items,
        key=lambda r: (series_score(r[2]), episode_count(conn, r[0]), len(r[3] or "")),
        reverse=True,
    )


def merge_series_group(
    conn: sqlite3.Connection, items: list[tuple], dry_run: bool
) -> dict[str, int]:
    stats = {"seasons_merged": 0, "episodes_moved": 0, "series_removed": 0}
    ordered = sorted(
        items,
        key=lambda r: (series_score(r[2]), episode_count(conn, r[0]), len(r[3] or "")),
        reverse=True,
    )
    # For duplicate series: prefer strm root when it already contains linked local mkv (single tree).
    strm_items = [r for r in ordered if "/strm/" in (r[2] or "").lower()]
    local_items = [r for r in ordered if "/strm/" not in (r[2] or "").lower()]
    if strm_items and local_items:
        strm_id = strm_items[0][0]
        mkv_in_strm = conn.execute(
            "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=? AND lower(Path) LIKE '%.mkv'",
            (strm_id, TYPE_EPISODE),
        ).fetchone()[0]
        if mkv_in_strm > 0:
            ordered = strm_items + local_items
    primary = ordered[0]
    primary_id = primary[0]
    primary_name = primary[1]
    primary_key = primary[4]

    dup_ids = [r[0] for r in ordered[1:]]
    if not dup_ids:
        return stats

    print(f"  keep {primary_name} ({primary_id}) @ {primary[2]}")
    for dup in ordered[1:]:
        print(f"    absorb {dup[1]} ({dup[0]}) @ {dup[2]}")

    if dry_run:
        return stats

    # Move all seasons/episodes from duplicate series trees onto primary
    seasons_by_number: dict[int | None, int] = {}
    for row in conn.execute(
        "SELECT Id, ParentIndexNumber FROM MediaItems WHERE ParentId=? AND type=?",
        (primary_id, TYPE_SEASON),
    ):
        seasons_by_number[row[1]] = row[0]

    for dup_id in dup_ids:
        for child in fetch_children(conn, dup_id):
            cid, ctype = child[0], child[1]
            if ctype == TYPE_SEASON:
                sn = child[6]
                target_season = seasons_by_number.get(sn)
                if target_season is None:
                    conn.execute(
                        "UPDATE MediaItems SET ParentId=?, SeriesId=?, SeriesName=? WHERE Id=?",
                        (primary_id, primary_id, primary_name, cid),
                    )
                    seasons_by_number[sn] = cid
                else:
                    # merge duplicate season: move episodes to existing season
                    conn.execute(
                        "UPDATE MediaItems SET ParentId=?, SeriesId=?, SeriesName=? "
                        "WHERE ParentId=? AND type=?",
                        (target_season, primary_id, primary_name, cid, TYPE_EPISODE),
                    )
                    delete_ancestors(conn, cid)
                    conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=?", (cid, TYPE_SEASON))
                    stats["seasons_merged"] += 1
            elif ctype == TYPE_EPISODE:
                conn.execute(
                    "UPDATE MediaItems SET SeriesId=?, SeriesName=? WHERE Id=?",
                    (primary_id, primary_name, cid),
                )
                stats["episodes_moved"] += 1
            else:
                conn.execute(
                    "UPDATE MediaItems SET ParentId=?, SeriesId=? WHERE Id=?",
                    (primary_id, primary_id, cid),
                )

        # episodes/seasons referencing dup as SeriesId
        for row in fetch_by_series(conn, dup_id):
            rid, rtype = row[0], row[1]
            if rtype == TYPE_SEASON:
                conn.execute(
                    "UPDATE MediaItems SET ParentId=?, SeriesId=?, SeriesName=? WHERE Id=?",
                    (primary_id, primary_id, primary_name, rid),
                )
            else:
                conn.execute(
                    "UPDATE MediaItems SET SeriesId=?, SeriesName=? WHERE Id=?",
                    (primary_id, primary_name, rid),
                )

        delete_ancestors(conn, dup_id)
        conn.execute("DELETE FROM AncestorIds2 WHERE AncestorId=?", (dup_id,))
        conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=?", (dup_id, TYPE_SERIES))
        stats["series_removed"] += 1

    if primary_key:
        conn.execute(
            "UPDATE MediaItems SET PresentationUniqueKey=? WHERE Id=?",
            (primary_key, primary_id),
        )

    rebuild_ancestors_subtree(conn, primary_id)
    return stats


def repair_orphans(conn: sqlite3.Connection, dry_run: bool) -> int:
    rows = fetch_series(conn)
    with_tmdb = [r for r in rows if tmdb(r[3])]
    without = [r for r in rows if not tmdb(r[3])]
    by_name: dict[str, tuple] = {}
    for r in with_tmdb:
        key = re.sub(r"\s*\(\d{4}\)\s*$", "", r[1]).strip().lower()
        if key not in by_name or series_score(r[2]) > series_score(by_name[key][2]):
            by_name[key] = r
    removed = 0
    for orphan in without:
        key = re.sub(r"\s*\(\d{4}\)\s*$", "", orphan[1]).strip().lower()
        primary = by_name.get(key)
        if not primary or primary[0] == orphan[0]:
            continue
        print(f"  orphan {orphan[1]} ({orphan[0]}) -> {primary[1]} ({primary[0]})")
        if dry_run:
            removed += 1
            continue
        merge_series_group(conn, [primary, orphan], dry_run=False)
        removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("host-config.json"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    cfg = load_config(args.config)
    db_path = cfg["library_db"]
    container = cfg.get("docker_container", "embyserver")

    conn = sqlite3.connect(db_path)
    series = fetch_series(conn)
    groups: dict[str, list] = defaultdict(list)
    for row in series:
        key = tmdb(row[3])
        if key:
            groups[key].append(row)
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Duplicate TMDB series groups: {len(dup_groups)}")

    if dry_run:
        print("DRY RUN — use --apply to execute\n")

    if not dry_run:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = f"{db_path}.bak-repair-{ts}"
        print(f"Stopping {container}, backup -> {backup}")
        subprocess.run(f"docker stop {container}", shell=True, check=True)
        shutil.copy2(db_path, backup)
        conn.close()
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA busy_timeout=60000")

    total = {"seasons_merged": 0, "episodes_moved": 0, "series_removed": 0}
    for key, items in sorted(dup_groups.items()):
        print(f"\nTMDB={key}")
        st = merge_series_group(conn, items, dry_run)
        for k in total:
            total[k] += st[k]

    print("\n=== Orphans ===")
    orphans = repair_orphans(conn, dry_run)
    print(f"Orphan groups handled: {orphans}")

    if not dry_run:
        conn.commit()
        conn.close()
        subprocess.run(f"docker start {container}", shell=True, check=True)
        print(f"\nDone. Removed {total['series_removed']} duplicate series roots.")
        print("Attendi 30s e verifica in Emby PRIMA di lanciare un refresh completo.")
        time.sleep(5)
    else:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

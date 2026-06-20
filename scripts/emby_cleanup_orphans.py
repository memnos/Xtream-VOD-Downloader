#!/usr/bin/env python3
"""Remove orphan duplicate series recreated by library scan (same folder name, missing TMDB)."""
import re
import shutil
import sqlite3
import subprocess
import time
from collections import defaultdict
from datetime import datetime

DB_PATH = "/var/lib/emby_config/data/library.db"
CONTAINER = "embyserver"


def norm_name(name):
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", (name or "").strip()).lower()
    return re.sub(r"\s+", " ", name)


def tmdb_from_provider_ids(raw):
    if not raw:
        return None
    for part in raw.split("|"):
        if part.lower().startswith("tmdb="):
            return part.split("=", 1)[1]
    return None


def merge_orphans(conn):
    rows = conn.execute(
        "SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey FROM MediaItems WHERE type=6"
    ).fetchall()
    with_tmdb = []
    without_tmdb = []
    for row in rows:
        if tmdb_from_provider_ids(row[3]):
            with_tmdb.append(row)
        else:
            without_tmdb.append(row)

    by_norm = defaultdict(list)
    for row in with_tmdb:
        by_norm[norm_name(row[1])].append(row)

    removed = 0
    for orphan in without_tmdb:
        key = norm_name(orphan[1])
        candidates = by_norm.get(key)
        if not candidates:
            continue
        primary = sorted(
            candidates,
            key=lambda r: (0 if r[2].startswith("/data/tv/") and not r[2].startswith("/data/tv-2/") else 1, -len(r[3] or "")),
        )[0]
        primary_id = primary[0]
        orphan_id = orphan[0]
        print(f"  ORPHAN {orphan[1]} ({orphan_id}) -> {primary[1]} ({primary_id})")
        conn.execute("UPDATE MediaItems SET ParentId=? WHERE ParentId=?", (primary_id, orphan_id))
        conn.execute("UPDATE MediaItems SET SeriesId=? WHERE SeriesId=?", (primary_id, orphan_id))
        conn.execute("DELETE FROM AncestorIds2 WHERE ItemId=? OR AncestorId=?", (orphan_id, orphan_id))
        conn.execute("DELETE FROM MediaItems WHERE Id=? AND type=6", (orphan_id,))
        removed += 1
    conn.commit()
    return removed


def main():
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{DB_PATH}.bak-orphan-{ts}"
    subprocess.run(f"docker stop {CONTAINER}", shell=True, check=True)
    shutil.copy2(DB_PATH, backup)
    print(f"Backup: {backup}")
    conn = sqlite3.connect(DB_PATH)
    removed = merge_orphans(conn)
    conn.close()
    subprocess.run(f"docker start {CONTAINER}", shell=True, check=True)
    print(f"Removed {removed} orphan duplicate series")
    time.sleep(15)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Restore/rebuild PresentationUniqueKey for seasons and episodes."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

TYPE_SERIES = 6
TYPE_SEASON = 7
TYPE_EPISODE = 8
BACKUP = "/var/lib/emby_config/data/library.db.bak-repair-20260613-225648"


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild_key(series_key: str, season: int | None, episode: int | None) -> str:
    if season is None:
        return series_key
    sk = f"{series_key}-{season:03d}"
    if episode is None:
        return sk
    return f"{sk} - {episode:04d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("host-config.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    cfg = load_config(args.config)
    db_path = cfg["library_db"]
    container = cfg.get("docker_container", "embyserver")

    conn = sqlite3.connect(db_path)
    conn.execute(f"ATTACH DATABASE '{BACKUP}' AS bak")

    # restore from backup when key differs
    restore_rows = conn.execute(
        """
        SELECT c.Id, c.type, b.PresentationUniqueKey
        FROM MediaItems c
        JOIN bak.MediaItems b ON b.Id = c.Id
        WHERE c.type IN (?, ?)
          AND b.PresentationUniqueKey IS NOT NULL
          AND c.PresentationUniqueKey != b.PresentationUniqueKey
        """,
        (TYPE_SEASON, TYPE_EPISODE),
    ).fetchall()
    print(f"Restore from backup: {len(restore_rows)} items")

    # rebuild keys for items still matching series key (absorbed duplicates / missing in backup match)
    rebuild_rows = conn.execute(
        """
        SELECT c.Id, c.type, s.PresentationUniqueKey, c.ParentIndexNumber, c.IndexNumber
        FROM MediaItems c
        JOIN MediaItems s ON s.Id = c.SeriesId
        WHERE c.type IN (?, ?)
          AND c.PresentationUniqueKey = s.PresentationUniqueKey
        """,
        (TYPE_SEASON, TYPE_EPISODE),
    ).fetchall()
    print(f"Rebuild (collapsed to series key): {len(rebuild_rows)} items")

    outlander = [
        r for r in restore_rows + [(x[0], x[1], rebuild_key(x[2], x[3], x[4] if x[1] == TYPE_EPISODE else None)) for x in rebuild_rows]
        if conn.execute("SELECT Path FROM MediaItems WHERE Id=?", (r[0],)).fetchone()[0].find("Outlander (2014)") >= 0
    ]
    print(f"Outlander affected: {len(outlander)}")

    if dry_run:
        conn.close()
        print("DRY RUN — use --apply")
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{db_path}.bak-keysfix-{ts}"
    print(f"Stopping {container}, backup -> {backup}")
    subprocess.run(f"docker stop {container}", shell=True, check=True)
    shutil.copy2(db_path, backup)
    conn.close()

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute(f"ATTACH DATABASE '{BACKUP}' AS bak")

    conn.execute(
        """
        UPDATE MediaItems
        SET PresentationUniqueKey = (
            SELECT b.PresentationUniqueKey FROM bak.MediaItems b WHERE b.Id = MediaItems.Id
        )
        WHERE type IN (?, ?)
          AND Id IN (
            SELECT c.Id FROM MediaItems c
            JOIN bak.MediaItems b ON b.Id = c.Id
            WHERE c.type IN (?, ?)
              AND b.PresentationUniqueKey IS NOT NULL
              AND c.PresentationUniqueKey != b.PresentationUniqueKey
          )
        """,
        (TYPE_SEASON, TYPE_EPISODE, TYPE_SEASON, TYPE_EPISODE),
    )

    for item_id, itype, series_key, season, episode in rebuild_rows:
        if itype == TYPE_SEASON:
            new_key = rebuild_key(series_key, season, None)
        else:
            new_key = rebuild_key(series_key, season, episode)
        conn.execute(
            "UPDATE MediaItems SET PresentationUniqueKey=? WHERE Id=?",
            (new_key, item_id),
        )
        conn.execute(
            "UPDATE MediaItems SET SeriesPresentationUniqueKey=? WHERE Id=?",
            (series_key, item_id),
        )

    conn.commit()
    conn.close()
    subprocess.run(f"docker start {container}", shell=True, check=True)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

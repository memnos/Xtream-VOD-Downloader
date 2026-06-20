#!/usr/bin/env python3
import sqlite3
from pathlib import Path

for label, db in [
    ("pre-repair", "/var/lib/emby_config/data/library.db.bak-repair-20260613-225648"),
    ("pre-merge", "/var/lib/emby_config/data/library.db.bak-20260613-223015"),
    ("current", "/var/lib/emby_config/data/library.db"),
]:
    print(f"\n=== {label} ===")
    c = sqlite3.connect(db)
    rows = c.execute(
        "SELECT Id, type, Name, ParentIndexNumber, PresentationUniqueKey "
        "FROM MediaItems WHERE Path LIKE ? AND type IN (6,7,8) "
        "ORDER BY type, ParentIndexNumber, Id LIMIT 20",
        ("%Outlander (2014)%",),
    ).fetchall()
    for r in rows:
        print(r)
    c.close()

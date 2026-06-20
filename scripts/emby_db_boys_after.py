#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
rows = c.execute(
    """
    SELECT Id, Name, Path, ParentId, SeriesId
    FROM MediaItems
    WHERE type=7 AND (Path LIKE '%/The Boys%' OR SeriesId=5852501 OR ParentId=5852501)
    ORDER BY ParentIndexNumber, Path
    """
).fetchall()
print(f"Seasons for The Boys: {len(rows)}")
for r in rows:
    print(r)

eps = c.execute(
    "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=5852501 AND type=8"
).fetchone()[0]
print(f"Episodes with SeriesId=5852501: {eps}")

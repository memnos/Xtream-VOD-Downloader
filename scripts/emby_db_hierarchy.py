#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
for sid in (5852501, 5900071, 5957574):
    print(f"\n=== Series {sid} ===")
    seasons = c.execute(
        "SELECT Id, type, Name, Path, ParentId, SeriesId FROM MediaItems WHERE ParentId=? ORDER BY type, Name",
        (sid,),
    ).fetchall()
    print(f"direct children: {len(seasons)}")
    for s in seasons[:5]:
        print(" ", s)
    eps = c.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=?",
        (sid,),
    ).fetchone()[0]
    print(f"items with SeriesId={sid}: {eps}")

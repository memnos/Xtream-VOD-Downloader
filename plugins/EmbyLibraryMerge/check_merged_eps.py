#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("/var/lib/emby_config/data/library.db")
for name in ["The Boys", "Silo", "Outlander", "Vikings"]:
    print(f"\n=== {name} ===")
    eps = c.execute(
        "SELECT COUNT(*), MIN(Path), MAX(Path) FROM MediaItems WHERE type=8 AND Path LIKE ?",
        (f"%{name}%",),
    ).fetchone()
    print(f"  episodes by path like %name%: {eps[0]}")
    series = c.execute(
        "SELECT Id, Path FROM MediaItems WHERE type=6 AND Name LIKE ?",
        (f"%{name.split()[0]}%",),
    ).fetchall()
    for sid, path in series:
        n = c.execute("SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=8", (sid,)).fetchone()[0]
        print(f"  series {sid} @ {path}: {n} eps")

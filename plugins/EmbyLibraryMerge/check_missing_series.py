#!/usr/bin/env python3
import sqlite3
for name in ["Downton Abbey", "The Last Kingdom", "The Big Bang Theory (2007)", "Outlander"]:
    print(f"\n=== {name} ===")
    c = sqlite3.connect("/var/lib/emby_config/data/library.db")
    for r in c.execute(
        "SELECT Id, type, Name, Path FROM MediaItems WHERE Path LIKE ? ORDER BY type",
        (f"%{name}%",),
    ):
        if "strm" not in r[3].lower() or r[2] == 6:
            print(r)
    c.close()

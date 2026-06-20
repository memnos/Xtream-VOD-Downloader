#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("AncestorIds2 schema:", [r[1] for r in c.execute("PRAGMA table_info(AncestorIds2)")])
rows = c.execute("SELECT * FROM AncestorIds2 WHERE ItemId=? OR ParentItemId=? LIMIT 10", (5900071, 5900071)).fetchall()
print("ancestors for 5900071:", rows)
rows = c.execute("SELECT * FROM AncestorIds2 WHERE ItemId=? LIMIT 5", (5915037,)).fetchall()
print("ancestors for season 5915037:", rows)

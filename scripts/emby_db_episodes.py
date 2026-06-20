#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
ep = c.execute(
    "SELECT Id, type, Name, Path, ParentId, SeriesId FROM MediaItems WHERE Path LIKE '%The Boys%Season%' AND type=8 LIMIT 5"
).fetchall()
print("episodes type 8:", ep)
ep = c.execute(
    "SELECT Id, type, Name, Path, ParentId, SeriesId FROM MediaItems WHERE Path LIKE '%strm/series/The Boys%' AND type NOT IN (6,7) LIMIT 8"
).fetchall()
print("other strm boys items:", ep)

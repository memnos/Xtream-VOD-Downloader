#!/usr/bin/env python3
import sqlite3
for q in [
    "SELECT Id,Name,Path FROM MediaItems WHERE type=6 AND Name LIKE '%Downton%'",
    "SELECT Id,Name,Path FROM MediaItems WHERE type=6 AND Name LIKE '%Outlander%'",
    "SELECT Id,Name,Path FROM MediaItems WHERE type=6 AND Path LIKE '/data/tv/%' LIMIT 15",
]:
    print("\n", q)
    c = sqlite3.connect("/var/lib/emby_config/data/library.db")
    for r in c.execute(q):
        print(" ", r)
    c.close()

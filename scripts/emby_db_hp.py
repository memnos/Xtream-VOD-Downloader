#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("/var/lib/emby_config/data/library.db")
rows = c.execute("SELECT Id,Name,Path,type FROM MediaItems WHERE Path LIKE '%Harry Potter e la pietra%'").fetchall()
print(rows)

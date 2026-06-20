#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("count:", c.execute("SELECT COUNT(*) FROM MediaItems").fetchone())
print("sample types:", c.execute("SELECT type, COUNT(*) FROM MediaItems GROUP BY type ORDER BY 2 DESC LIMIT 15").fetchall())
rows = c.execute("SELECT Id, type, Name, Path FROM MediaItems WHERE Path LIKE '%/The Boys%' LIMIT 10").fetchall()
print("path The Boys:", rows)

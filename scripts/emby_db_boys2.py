#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("types:", c.execute("SELECT DISTINCT type FROM MediaItems WHERE IsSeries=1 LIMIT 5").fetchall())
rows = c.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE IsSeries=1 AND Name LIKE '%The Boys%'"
).fetchall()
print("The Boys:", rows)
rows = c.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE IsSeries=1 AND ProviderIds LIKE '%76479%'"
).fetchall()
print("Tmdb 76479:", rows)

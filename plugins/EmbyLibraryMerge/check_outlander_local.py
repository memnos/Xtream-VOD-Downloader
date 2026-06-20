#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("Local Outlander items:")
for r in c.execute(
    "SELECT Id, type, Name, Path FROM MediaItems "
    "WHERE Path LIKE '%Outlander%' AND Path NOT LIKE '%strm%'"
):
    print(r)
print("\nDuplicate TMDB Outlander series:")
for r in c.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE Name LIKE '%Outlander%' AND type=6"
):
    print(r)

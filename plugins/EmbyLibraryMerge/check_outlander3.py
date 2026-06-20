#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("/var/lib/emby_config/data/library.db")
print("Outlander local:")
for r in c.execute(
    "SELECT Id, type, Name, Path FROM MediaItems "
    "WHERE Path LIKE '%Outlander%' AND Path NOT LIKE '%strm%' AND Path NOT LIKE '%Blood%' "
    "AND type IN (6,7,8) ORDER BY type, Path"
):
    print(r)

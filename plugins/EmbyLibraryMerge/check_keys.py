#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path
from collections import Counter

cfg = json.loads(Path(__file__).with_name("host-config.json").read_text(encoding="utf-8"))
conn = sqlite3.connect(cfg["library_db"])
SID = 5899873

print("=== Outlander seasons keys ===")
keys = []
for row in conn.execute(
    "SELECT Id, Name, ParentIndexNumber, PresentationUniqueKey, SeriesPresentationUniqueKey "
    "FROM MediaItems WHERE ParentId=? AND type=7 ORDER BY ParentIndexNumber",
    (SID,),
):
    print(row)
    keys.append(row[3])

print("\nUnique PresentationUniqueKey count:", len(set(keys)), "of", len(keys))

print("\n=== Duplicate season keys library-wide ===")
dup = conn.execute(
    "SELECT PresentationUniqueKey, COUNT(*), GROUP_CONCAT(Id) "
    "FROM MediaItems WHERE type=7 AND PresentationUniqueKey IS NOT NULL "
    "GROUP BY PresentationUniqueKey HAVING COUNT(*)>1 LIMIT 5"
).fetchall()
print(f"Groups with duplicate season keys: {len(dup)} (showing 5)")
for d in dup:
    print(d)

print("\n=== Series PresentationPresentationUniqueKey ===")
print(conn.execute(
    "SELECT Id, PresentationUniqueKey, SeriesPresentationUniqueKey FROM MediaItems WHERE Id=?",
    (SID,),
).fetchone())

conn.close()

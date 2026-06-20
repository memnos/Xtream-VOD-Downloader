#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

cfg = json.loads(Path(__file__).with_name("host-config.json").read_text(encoding="utf-8"))
conn = sqlite3.connect(cfg["library_db"])
SID = 5899873

print("=== Season ParentIndexNumber ===")
for row in conn.execute(
    "SELECT Id, Name, Path, ParentIndexNumber, ParentId, SeriesId FROM MediaItems "
    "WHERE ParentId=? AND type=7 ORDER BY Path",
    (SID,),
):
    print(row)

print("\n=== Ancestors for series ===")
for row in conn.execute(
    "SELECT ItemId, AncestorId, Distance FROM AncestorIds2 WHERE ItemId=? ORDER BY Distance",
    (SID,),
):
    print(row)

print("\n=== Ancestors for first season ===")
season_id = conn.execute(
    "SELECT Id FROM MediaItems WHERE ParentId=? AND type=7 LIMIT 1", (SID,)
).fetchone()[0]
for row in conn.execute(
    "SELECT ItemId, AncestorId, Distance FROM AncestorIds2 WHERE ItemId=? ORDER BY Distance",
    (season_id,),
):
    print(row)

print("\n=== Ancestors for first episode ===")
ep_id = conn.execute(
    "SELECT Id FROM MediaItems WHERE SeriesId=? AND type=8 LIMIT 1", (SID,)
).fetchone()[0]
for row in conn.execute(
    "SELECT ItemId, AncestorId, Distance FROM AncestorIds2 WHERE ItemId=? ORDER BY Distance",
    (ep_id,),
):
    print(row)

print("\n=== Episodes with bad parent/series ===")
bad = conn.execute(
    "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=8 AND (ParentId IS NULL OR ParentId=0)",
    (SID,),
).fetchone()[0]
print(f"episodes without season parent: {bad}")

print("\n=== Local Outlander items still in DB ===")
for row in conn.execute(
    "SELECT Id, type, Name, Path, SeriesId, ParentId FROM MediaItems "
    "WHERE Path LIKE '%/Outlander%' AND Path NOT LIKE '%Blood%' ORDER BY type, Path"
):
    print(row)

print("\n=== Duplicate season numbers (PresentationUniqueKey) ===")
for row in conn.execute(
    "SELECT ParentIndexNumber, COUNT(*), GROUP_CONCAT(Id) FROM MediaItems "
    "WHERE ParentId=? AND type=7 GROUP BY ParentIndexNumber",
    (SID,),
):
    print(row)

conn.close()

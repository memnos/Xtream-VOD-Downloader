#!/usr/bin/env python3
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = Path(__file__).with_name("host-config.json")
import json

cfg = json.loads(DB.read_text(encoding="utf-8"))
conn = sqlite3.connect(cfg["library_db"])

TYPE_SERIES = 6
TYPE_SEASON = 7
TYPE_EPISODE = 8


def tmdb(raw):
    if not raw:
        return None
    for part in raw.split("|"):
        if part.lower().startswith("tmdb="):
            return part.split("=", 1)[1]
    return None


empty = []
for sid, name, path in conn.execute(
    "SELECT Id, Name, Path FROM MediaItems WHERE type=?", (TYPE_SERIES,)
):
    eps = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=?",
        (sid, TYPE_EPISODE),
    ).fetchone()[0]
    if eps == 0:
        empty.append((sid, name, path))

print(f"Series with 0 episodes: {len(empty)}")
for row in empty[:15]:
    print(f"  {row[1]} ({row[0]}) @ {row[2]}")
if len(empty) > 15:
    print(f"  ... and {len(empty) - 15} more")

groups = defaultdict(list)
for row in conn.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems WHERE type=?", (TYPE_SERIES,)
):
    key = tmdb(row[3])
    if key:
        groups[key].append(row)

dups = {k: v for k, v in groups.items() if len(v) > 1}
print(f"\nDuplicate TMDB series groups: {len(dups)}")

print("\n=== The Boys ===")
for sid, name, path in conn.execute(
    "SELECT Id, Name, Path FROM MediaItems WHERE Name=? AND type=?",
    ("The Boys", TYPE_SERIES),
):
    eps = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=?",
        (sid, TYPE_EPISODE),
    ).fetchone()[0]
    seas = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE ParentId=? AND type=?",
        (sid, TYPE_SEASON),
    ).fetchone()[0]
    print(f"  id={sid} seasons={seas} episodes={eps} path={path}")

conn.close()

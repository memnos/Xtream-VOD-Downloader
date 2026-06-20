#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

cfg = json.loads(Path(__file__).with_name("host-config.json").read_text(encoding="utf-8"))
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


print("=== All Outlander series entries ===")
series = conn.execute(
    "SELECT Id, Name, Path, ProviderIds FROM MediaItems "
    "WHERE type=? AND (Name LIKE '%Outlander%' OR Path LIKE '%Outlander%')",
    (TYPE_SERIES,),
).fetchall()
for s in series:
    sid = s[0]
    eps = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? AND type=?",
        (sid, TYPE_EPISODE),
    ).fetchone()[0]
    seas = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE ParentId=? AND type=?",
        (sid, TYPE_SEASON),
    ).fetchone()[0]
    print(f"\nSERIES id={sid} name={s[1]}")
    print(f"  path={s[2]}")
    print(f"  tmdb={tmdb(s[3])}  seasons={seas}  episodes={eps}")
    for row in conn.execute(
        "SELECT Id, Name, Path, ParentIndexNumber FROM MediaItems "
        "WHERE ParentId=? AND type=? ORDER BY ParentIndexNumber",
        (sid, TYPE_SEASON),
    ):
        sn = row[3]
        ep_count = conn.execute(
            "SELECT COUNT(*) FROM MediaItems WHERE ParentId=? AND type=?",
            (row[0], TYPE_EPISODE),
        ).fetchone()[0]
        print(f"    S{sn} id={row[0]} eps={ep_count} path={row[2]}")

print("\n=== Episodes by path (all Outlander) ===")
eps = conn.execute(
    "SELECT Id, Name, Path, SeriesId, ParentId, ParentIndexNumber, IndexNumber "
    "FROM MediaItems WHERE type=? AND Path LIKE '%Outlander%' ORDER BY Path",
    (TYPE_EPISODE,),
).fetchall()
print(f"Total episode records: {len(eps)}")
by_series = {}
for e in eps:
    by_series.setdefault(e[3], []).append(e)
for sid, items in sorted(by_series.items()):
    name = conn.execute("SELECT Name FROM MediaItems WHERE Id=?", (sid,)).fetchone()
    print(f"  SeriesId={sid} ({name[0] if name else '?'}): {len(items)} episodes")

print("\n=== Orphan / mismatched episodes ===")
for e in eps[:5]:
    print(f"  ep id={e[0]} S{e[5]}E{e[6]} series={e[3]} parent={e[4]} path={e[2]}")
if len(eps) > 5:
    print(f"  ... {len(eps)-5} more")

conn.close()

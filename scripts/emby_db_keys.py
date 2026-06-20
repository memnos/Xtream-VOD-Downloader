#!/usr/bin/env python3
import sqlite3

c = sqlite3.connect("/var/lib/emby_config/data/library.db")
ids = (5852501, 5900071, 5957574)
rows = c.execute(
    f"""
    SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey, SeriesPresentationUniqueKey, SeriesId, ParentId
    FROM MediaItems WHERE Id IN ({','.join('?'*len(ids))})
    """,
    ids,
).fetchall()
for r in rows:
    print(r)

# children count per series root
for sid in ids:
    cnt = c.execute("SELECT COUNT(*) FROM MediaItems WHERE SeriesId=? OR ParentId=?", (sid, sid)).fetchone()
    print(f"children of {sid}: {cnt}")

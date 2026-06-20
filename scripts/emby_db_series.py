#!/usr/bin/env python3
import sqlite3

db = "/var/lib/emby_config/data/library.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

# Inspect MediaItems for duplicate series paths
cur.execute("PRAGMA table_info(MediaItems)")
print("MediaItems columns:", [r[1] for r in cur.fetchall()])

cur.execute(
    """
    SELECT mi.Id, mi.Path, mi.Type
    FROM MediaItems mi
    WHERE mi.Type = 'Series'
      AND (mi.Path LIKE '%/The Boys%' OR mi.Path LIKE '%/Andor%')
    LIMIT 20
    """
)
for row in cur.fetchall():
    print(row)

cur.execute("PRAGMA table_info(fts_search9)")
cols = [r[1] for r in cur.fetchall()]
print("\nfts_search9 columns:", cols)

# find The Boys entries
name_col = next((c for c in cols if c.lower() in ("name", "cleanname", "text")), cols[0])
cur.execute(
    f"SELECT rowid, {name_col} FROM fts_search9 WHERE {name_col} LIKE '%Boys%' LIMIT 10"
)
print("\nfts The Boys:")
for row in cur.fetchall():
    print(row)

conn.close()

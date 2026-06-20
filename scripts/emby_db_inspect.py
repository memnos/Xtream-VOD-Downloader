#!/usr/bin/env python3
import sqlite3

db = "/var/lib/emby_config/data/library.db"
conn = sqlite3.connect(db)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in cur.fetchall()]
print("All tables:")
for t in tables:
    print(" ", t)

for t in ["TypedBaseItems", "BaseItems"]:
    if t in tables:
        cur.execute(f"PRAGMA table_info({t})")
        cols = [r[1] for r in cur.fetchall()]
        print(f"\n{t} columns sample:", cols[:30])
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"  count: {cur.fetchone()[0]}")

conn.close()

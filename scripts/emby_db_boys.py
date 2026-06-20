#!/usr/bin/env python3
import sqlite3

db = "/var/lib/emby_config/data/library.db"
conn = sqlite3.connect(db)
cur = conn.cursor()

cur.execute(
    """
    SELECT Id, Name, Path, ProviderIds, SeriesPresentationUniqueKey, PresentationUniqueKey
    FROM MediaItems
    WHERE Type = 'Series' AND Name = 'The Boys'
    """
)
print("The Boys series rows:")
for row in cur.fetchall():
    print(row)

cur.execute(
    """
    SELECT ProviderIds, COUNT(*) as c, GROUP_CONCAT(Name || ' @ ' || Path, ' | ')
    FROM MediaItems
    WHERE Type = 'Series' AND ProviderIds LIKE '%76479%'
    GROUP BY ProviderIds
    """
)
print("\nTmdb 76479 groups:")
for row in cur.fetchall():
    print(row)

conn.close()

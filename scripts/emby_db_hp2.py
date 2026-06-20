#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("/var/lib/emby_config/data/library.db")
for mid in (5806068, 5866788):
    row = c.execute(
        "SELECT Id, Name, Path, ProviderIds, PresentationUniqueKey, ParentId FROM MediaItems WHERE Id=?",
        (mid,),
    ).fetchone()
    print(row)

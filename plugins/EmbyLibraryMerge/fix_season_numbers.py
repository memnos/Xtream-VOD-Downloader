#!/usr/bin/env python3
"""Fix missing ParentIndexNumber on season items (parsed from path/name)."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

TYPE_SEASON = 7


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def season_number(name: str | None, path: str | None) -> int | None:
    for text in (path or "", name or ""):
        m = re.search(r"(?:Season|Stagione)\s*0*(\d+)", text, re.I)
        if m:
            return int(m.group(1))
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("host-config.json"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    cfg = load_config(args.config)
    db_path = cfg["library_db"]
    container = cfg.get("docker_container", "embyserver")

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT Id, Name, Path, ParentIndexNumber, ParentId FROM MediaItems WHERE type=?",
        (TYPE_SEASON,),
    ).fetchall()

    fixes: list[tuple[int, int, str, str]] = []
    for sid, name, path, pin, parent_id in rows:
        if pin is not None:
            continue
        sn = season_number(name, path)
        if sn is None:
            continue
        fixes.append((sid, sn, name or "", path or ""))

    print(f"Seasons missing ParentIndexNumber: {sum(1 for r in rows if r[3] is None)}")
    print(f"Fixable from path/name: {len(fixes)}")

    # show Outlander sample
    for sid, sn, name, path in fixes:
        if "Outlander" in path and "Blood" not in path:
            print(f"  id={sid} -> S{sn}  {path}")

    if dry_run:
        print("\nDRY RUN — use --apply to execute")
        conn.close()
        return 0

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{db_path}.bak-seasonfix-{ts}"
    print(f"\nStopping {container}, backup -> {backup}")
    subprocess.run(f"docker stop {container}", shell=True, check=True)
    shutil.copy2(db_path, backup)
    conn.close()
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout=60000")

    for sid, sn, _, _ in fixes:
        conn.execute("UPDATE MediaItems SET ParentIndexNumber=? WHERE Id=?", (sn, sid))

    conn.commit()
    conn.close()
    subprocess.run(f"docker start {container}", shell=True, check=True)
    print(f"Updated {len(fixes)} seasons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

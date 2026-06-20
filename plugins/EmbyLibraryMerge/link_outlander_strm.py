#!/usr/bin/env python3
"""DEPRECATO: non usare. Usare mergerfs (scripts/mergerfs/) invece dei symlink negli strm.

Create symlinks to local MKV files inside Outlander STRM season folders."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).with_name("host-config.json")
STRM_ROOT = "/data/strm/series/Outlander (2014)"
LOCAL_ROOT = "/data/tv/Outlander (2014)"


def cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def docker_sh(script: str) -> subprocess.CompletedProcess:
    container = cfg().get("docker_container", "embyserver")
    return subprocess.run(
        ["docker", "exec", container, "sh", "-c", script],
        capture_output=True,
        text=True,
    )


def season_dir(season: int) -> str:
    return f"{STRM_ROOT}/Season {season:02d}"


def ep_from_name(name: str) -> tuple[int, int] | None:
    m = re.search(r"S(\d+)E(\d+)", name, re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def main() -> int:
    # collect local mkvs (prefer LOCAL_ROOT, skip duplicates under /data/tv/Outlander)
    out = docker_sh(f"find '{LOCAL_ROOT}' -name '*.mkv' 2>/dev/null")
    local_files = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not local_files:
        print("Nessun MKV locale trovato.", file=sys.stderr)
        return 1

    print(f"MKV locali: {len(local_files)}")
    created = 0
    skipped = 0

    for src in local_files:
        base = src.rsplit("/", 1)[-1]
        parsed = ep_from_name(base)
        if not parsed:
            print(f"  SKIP (no SxxExx): {src}")
            skipped += 1
            continue
        season, _ep = parsed
        dst_dir = season_dir(season)
        link = f"{dst_dir}/{base}"

        exists = docker_sh(f"test -e '{link}' && echo yes || echo no").stdout.strip()
        if exists == "yes":
            print(f"  EXISTS {link}")
            skipped += 1
            continue

        docker_sh(f"mkdir -p '{dst_dir}'")
        r = docker_sh(f"ln -sf '{src}' '{link}'")
        if r.returncode != 0:
            print(f"  ERR {link}: {r.stderr}", file=sys.stderr)
            skipped += 1
        else:
            print(f"  LINK {link} -> {src}")
            created += 1

    print(f"\nCreati {created} symlink, saltati {skipped}")

    if created == 0:
        return 0

    # notify Emby
    c = cfg()
    base_url = c["emby_url"].rstrip("/")
    key = c["emby_api_key"]
    hdr = {"X-Emby-Token": key, "Content-Type": "application/json"}

    links = docker_sh(f"find '{STRM_ROOT}' -name '*.mkv' 2>/dev/null").stdout.splitlines()
    updates = [{"Path": p.strip(), "UpdateType": "Created"} for p in links if p.strip()]
    urllib.request.urlopen(
        urllib.request.Request(
            base_url + "/emby/Library/Media/Updated",
            data=json.dumps({"Updates": updates}).encode(),
            headers=hdr,
            method="POST",
        ),
        timeout=120,
    )

    conn = sqlite3.connect(c["library_db"])
    sid = conn.execute(
        "SELECT Id FROM MediaItems WHERE type=6 AND Path=?",
        (STRM_ROOT,),
    ).fetchone()
    conn.close()
    if not sid:
        print("Serie Outlander STRM non trovata in DB.", file=sys.stderr)
        return 1

    series_id = sid[0]
    urllib.request.urlopen(
        urllib.request.Request(
            base_url + f"/emby/Items/{series_id}/Refresh?"
            + urllib.parse.urlencode({"Recursive": "true", "MetadataRefreshMode": "FullRefresh"}),
            headers={"X-Emby-Token": key},
            method="POST",
        ),
        timeout=120,
    )
    print(f"Refresh avviato su serie Outlander (id={series_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

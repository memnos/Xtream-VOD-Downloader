#!/usr/bin/env python3
"""DEPRECATO: non usare. Usare mergerfs (scripts/mergerfs/) invece dei symlink negli strm.

Symlink local [LOCAL].mkv files into Big Bang Theory STRM season folders."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent / "host-config.json"
STRM_ROOT = "/data/strm/series/The Big Bang Theory (2007)"
LOCAL_ROOT = "/data/tv-2/The Big Bang Theory"


def cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def docker_sh(container: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", container, "sh", "-c", script],
        capture_output=True,
        text=True,
    )


def season_dirs(container: str, season: int) -> list[str]:
    names = [
        f"{STRM_ROOT}/Season {season:02d}",
        f"{STRM_ROOT}/Season {season}",
        f"{STRM_ROOT}/Season{season:02d}",
        f"{STRM_ROOT}/Season{season}",
    ]
    existing = []
    for name in names:
        if docker_sh(container, f"test -d '{name}' && echo yes").stdout.strip() == "yes":
            existing.append(name)
    return existing or [f"{STRM_ROOT}/Season {season:02d}"]


def ep_from_name(name: str) -> tuple[int, int] | None:
    m = re.search(r"S(\d+)E(\d+)", name, re.I)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def main() -> int:
    c = cfg()
    container = c.get("docker_container", "embyserver")
    out = docker_sh(container, f"find '{LOCAL_ROOT}' -name '*.mkv' 2>/dev/null")
    local_files = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not local_files:
        print("No local MKV found.", file=sys.stderr)
        return 1

    print(f"Local MKV: {len(local_files)}")
    created = skipped = 0
    for src in local_files:
        base = src.rsplit("/", 1)[-1]
        parsed = ep_from_name(base)
        if not parsed:
            print(f"  SKIP (no SxxExx): {src}")
            skipped += 1
            continue
        season, _ep = parsed
        linked = False
        for dst_dir in season_dirs(container, season):
            docker_sh(container, f"mkdir -p '{dst_dir}'")
            link = f"{dst_dir}/{base}"
            exists = docker_sh(container, f"test -e '{link}' && echo yes").stdout.strip()
            if exists == "yes":
                skipped += 1
                linked = True
                break
            r = docker_sh(container, f"ln -sf '{src}' '{link}'")
            if r.returncode == 0:
                print(f"  LINK {link}")
                created += 1
                linked = True
                break
        if not linked:
            print(f"  ERR no season dir for S{season:02d}: {src}", file=sys.stderr)
            skipped += 1

    print(f"\nCreated {created}, skipped {skipped}")
    if created == 0:
        return 0

    base_url = c["emby_url"].rstrip("/")
    key = c["emby_api_key"]
    hdr = {"X-Emby-Token": key, "Content-Type": "application/json"}
    links = docker_sh(container, f"find '{STRM_ROOT}' -name '*.mkv' 2>/dev/null").stdout.splitlines()
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

    import sqlite3

    conn = sqlite3.connect(c["library_db"])
    row = conn.execute(
        "SELECT Id FROM MediaItems WHERE type=6 AND Path=?",
        (STRM_ROOT,),
    ).fetchone()
    conn.close()
    if row:
        series_id = row[0]
        urllib.request.urlopen(
            urllib.request.Request(
                base_url + f"/emby/Items/{series_id}/Refresh?"
                + urllib.parse.urlencode({"Recursive": "true", "MetadataRefreshMode": "Default"}),
                headers={"X-Emby-Token": key},
                method="POST",
            ),
            timeout=120,
        )
        print(f"Refresh started on STRM series (id={series_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

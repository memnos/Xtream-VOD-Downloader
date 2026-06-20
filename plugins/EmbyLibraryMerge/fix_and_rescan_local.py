#!/usr/bin/env python3
"""Fix missing tvshow.nfo and rescan local series not fully indexed."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).with_name("host-config.json")
NFO_SOURCES = {
    "/data/tv/Outlander (2014)": "/data/tv/Outlander/tvshow.nfo",
}


def cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def api(method: str, path: str, params: dict | None = None, body: dict | None = None):
    c = cfg()
    url = c["emby_url"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"X-Emby-Token": c["emby_api_key"], "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read()) if resp.read() else None


def docker_exec(cmd: str) -> str:
    container = cfg().get("docker_container", "embyserver")
    r = subprocess.run(f"docker exec {container} sh -c {json.dumps(cmd)}", shell=True, capture_output=True, text=True)
    return r.stdout.strip()


def local_series_files() -> dict[str, list[str]]:
    container = cfg().get("docker_container", "embyserver")
    out = subprocess.run(
        f"docker exec {container} find /data/tv /data/tv-2 "
        r"-type f \( -iname '*.mkv' -o -iname '*.mp4' \) 2>/dev/null",
        shell=True, capture_output=True, text=True,
    ).stdout
    series: dict[str, list[str]] = {}
    for line in out.splitlines():
        p = line.strip()
        if not p:
            continue
        for base in ("/data/tv", "/data/tv-2"):
            if p.startswith(base + "/"):
                root = base + "/" + p[len(base) + 1 :].split("/")[0]
                series.setdefault(root, []).append(p)
                break
    return series


def db_episode_count(db: str, root: str) -> int:
    conn = sqlite3.connect(db)
    n = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE ?",
        (root + "%",),
    ).fetchone()[0]
    conn.close()
    return n


def wait_scan(timeout: int = 3600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = urllib.request.urlopen(
            urllib.request.Request(
                cfg()["emby_url"].rstrip("/") + "/emby/ScheduledTasks",
                headers={"X-Emby-Token": cfg()["emby_api_key"]},
            ),
            timeout=60,
        )
        active = [
            t for t in json.loads(tasks.read())
            if t.get("State") == "Running"
            and "scan" in (t.get("Name") or "").lower()
        ]
        if not active:
            return
        print(f"  Scan: {active[0].get('CurrentProgressPercentage', '?')}%")
        time.sleep(15)


def main() -> int:
    c = cfg()
    series = local_series_files()
    needs_rescan: list[str] = []

    print("=== Fix tvshow.nfo mancanti ===")
    for dest, src in NFO_SOURCES.items():
        has = docker_exec(f"test -f '{dest}/tvshow.nfo' && echo yes || echo no")
        if has == "yes":
            print(f"  OK {dest}")
            continue
        has_src = docker_exec(f"test -f '{src}' && echo yes || echo no")
        if has_src != "yes":
            print(f"  SKIP {dest}: sorgente {src} assente")
            continue
        docker_exec(f"cp '{src}' '{dest}/tvshow.nfo'")
        print(f"  Copiato {src} -> {dest}/tvshow.nfo")
        needs_rescan.append(dest)

    print("\n=== Serie con file locali non indicizzati ===")
    for root, files in sorted(series.items()):
        db = db_episode_count(c["library_db"], root)
        if db >= len(files):
            continue
        print(f"  {root.split('/')[-1]}: {db}/{len(files)} -> rescan")
        needs_rescan.append(root)

    needs_rescan = sorted(set(needs_rescan))
    if not needs_rescan:
        print("Nessun rescan necessario.")
        return 0

    all_files: list[str] = []
    for root in needs_rescan:
        all_files.extend(series.get(root, []))
        # include folder itself for series creation
        all_files.append(root)

    print(f"\n=== Notifica {len(all_files)} path ===")
    for i in range(0, len(all_files), 50):
        batch = all_files[i : i + 50]
        urllib.request.urlopen(
            urllib.request.Request(
                c["emby_url"].rstrip("/") + "/emby/Library/Media/Updated",
                data=json.dumps({"Updates": [{"Path": p, "UpdateType": "Created"} for p in batch]}).encode(),
                headers={"X-Emby-Token": c["emby_api_key"], "Content-Type": "application/json"},
                method="POST",
            ),
            timeout=120,
        )

    tasks = json.loads(
        urllib.request.urlopen(
            urllib.request.Request(
                c["emby_url"].rstrip("/") + "/emby/ScheduledTasks",
                headers={"X-Emby-Token": c["emby_api_key"]},
            )
        ).read()
    )
    task_id = next(t["Id"] for t in tasks if (t.get("Name") or "").lower() == "scan media library")
    print("=== Scan media library ===")
    urllib.request.urlopen(
        urllib.request.Request(
            c["emby_url"].rstrip("/") + f"/emby/ScheduledTasks/Running/{task_id}",
            headers={"X-Emby-Token": c["emby_api_key"]},
            method="POST",
        ),
        timeout=60,
    )
    wait_scan()

    print("\n=== Dopo rescan ===")
    for root in needs_rescan:
        db = db_episode_count(c["library_db"], root)
        disk = len(series.get(root, []))
        print(f"  {root.split('/')[-1]}: {db}/{disk}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

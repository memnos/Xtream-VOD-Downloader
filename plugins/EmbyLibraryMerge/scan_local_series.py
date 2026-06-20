#!/usr/bin/env python3
"""Scan Emby for all TV series with local video files under /data/tv and /data/tv-2."""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

LOCAL_ROOTS = ("/data/tv", "/data/tv-2")
CONFIG = Path(__file__).with_name("host-config.json")
BATCH = 50


def load_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def api(cfg: dict, method: str, path: str, params: dict | None = None, body: dict | None = None):
    url = cfg["emby_url"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-Emby-Token": cfg["emby_api_key"],
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def find_local_videos(container: str) -> tuple[dict[str, set[str]], list[str]]:
    cmd = (
        f"docker exec {container} find {' '.join(LOCAL_ROOTS)} "
        r"-type f \( -iname '*.mkv' -o -iname '*.mp4' -o -iname '*.avi' "
        r"-o -iname '*.m4v' -o -iname '*.ts' \) 2>/dev/null"
    )
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    files = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    series: dict[str, set[str]] = {}
    for fpath in files:
        for base in LOCAL_ROOTS:
            if fpath.startswith(base + "/"):
                root = base + "/" + fpath[len(base) + 1 :].split("/")[0]
                series.setdefault(root, set()).add(fpath)
                break
    return series, files


def scan_task_id(cfg: dict) -> str | None:
    for task in api(cfg, "GET", "/emby/ScheduledTasks") or []:
        if (task.get("Name") or "").lower() == "scan media library":
            return task.get("Id")
    return None


def wait_for_scan(cfg: dict, timeout: int = 7200) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        tasks = api(cfg, "GET", "/emby/ScheduledTasks") or []
        active = [
            t
            for t in tasks
            if t.get("State") == "Running"
            and any(k in (t.get("Name") or "").lower() for k in ("scan", "library", "refresh"))
        ]
        if not active:
            return True
        pct = active[0].get("CurrentProgressPercentage")
        print(f"  {active[0].get('Name')}: {pct if pct is not None else '?'}%")
        time.sleep(15)
    return False


def notify_files(cfg: dict, files: list[str]) -> None:
    for i in range(0, len(files), BATCH):
        batch = files[i : i + BATCH]
        updates = [{"Path": p, "UpdateType": "Created"} for p in batch]
        api(cfg, "POST", "/emby/Library/Media/Updated", body={"Updates": updates})
    print(f"Notificati {len(files)} file a Emby (Library/Media/Updated).")


def count_local_episodes(db_path: str) -> tuple[int, int]:
    conn = sqlite3.connect(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND (Path LIKE '/data/tv/%' OR Path LIKE '/data/tv-2/%')"
    ).fetchone()[0]
    outlander = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE '/data/tv/Outlander%'"
    ).fetchone()[0]
    conn.close()
    return total, outlander


def main() -> int:
    cfg = load_config()
    container = cfg.get("docker_container", "embyserver")

    print("=== Serie con file locali ===")
    series, files = find_local_videos(container)
    print(f"Serie: {len(series)} | File video: {len(files)}")
    before_total, before_out = count_local_episodes(cfg["library_db"])
    print(f"Episodi locali in DB (prima): {before_total} (Outlander: {before_out})")

    print("\n=== Notifica nuovi file ===")
    notify_files(cfg, files)

    task_id = scan_task_id(cfg)
    if task_id:
        print(f"\n=== Avvio 'Scan media library' ({task_id}) ===")
        api(cfg, "POST", f"/emby/ScheduledTasks/Running/{task_id}")
        if not wait_for_scan(cfg):
            print("Scan non completato entro timeout.", file=sys.stderr)
    else:
        print("\n=== Fallback: POST /Library/Refresh ===")
        api(cfg, "POST", "/emby/Library/Refresh")
        wait_for_scan(cfg)

    folders = api(cfg, "GET", "/emby/Library/VirtualFolders") or []
    for lib_name in ("Serie Tv", "VOD SERIES"):
        lib = next((f for f in folders if f.get("Name") == lib_name), None)
        if not lib:
            continue
        print(f"\n=== Refresh libreria '{lib_name}' (id={lib['ItemId']}) ===")
        api(
            cfg,
            "POST",
            f"/emby/Items/{lib['ItemId']}/Refresh",
            {"Recursive": "true", "MetadataRefreshMode": "Default"},
        )
    wait_for_scan(cfg, timeout=3600)

    after_total, after_out = count_local_episodes(cfg["library_db"])
    print(f"\n=== Risultato ===")
    print(f"Episodi locali in DB (dopo): {after_total} (+{after_total - before_total})")
    print(f"Outlander locali: {after_out} (+{after_out - before_out})")

    conn = sqlite3.connect(cfg["library_db"])
    print("\nSerie locali (top 10 per file su disco):")
    for root in sorted(series.keys(), key=lambda r: len(series[r]), reverse=True)[:10]:
        name = root.split("/")[-1]
        db_eps = conn.execute(
            "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE ?",
            (root + "%",),
        ).fetchone()[0]
        print(f"  {name}: {db_eps}/{len(series[root])} episodi indicizzati @ {root}")
    conn.close()

    if after_total > before_total:
        print("\nSuggerimento: se compaiono duplicati TMDB, esegui repair_series.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

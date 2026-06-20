#!/usr/bin/env python3
"""Rename downloaded MKV/MP4 files to include [LOCAL] marker (skip if already present)."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

MARKER = " [LOCAL]"
VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi"}

DEFAULT_SERIES = [
    "/data/tv-2/The Big Bang Theory",
    "/data/tv/The Big Bang Theory",
    "/data/tv/Outlander (2014)",
    "/data/tv/Outlander",
]

CONFIG = Path(__file__).resolve().parents[1] / "plugins" / "EmbyLibraryMerge" / "host-config.json"


def load_cfg() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def docker_exec(container: str, script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", "exec", container, "sh", "-c", script],
        capture_output=True,
        text=True,
    )


def list_videos(container: str, roots: list[str]) -> list[str]:
    paths: list[str] = []
    for root in roots:
        out = docker_exec(container, f"find '{root}' -type f 2>/dev/null")
        for line in out.stdout.splitlines():
            path = line.strip()
            if not path:
                continue
            ext = Path(path).suffix.lower()
            if ext in VIDEO_EXT and not path.lower().endswith(".strm"):
                paths.append(path)
    return sorted(set(paths))


def target_name(path: str) -> str | None:
    p = Path(path)
    stem = p.stem
    if MARKER.strip(" []") in stem or stem.endswith(MARKER):
        return None
    return str(p.with_name(f"{stem}{MARKER}{p.suffix}"))


def notify_emby(paths: list[str], cfg: dict) -> None:
    if not paths:
        return
    base = cfg["emby_url"].rstrip("/")
    key = cfg["emby_api_key"]
    headers = {"X-Emby-Token": key, "Content-Type": "application/json"}
    updates = [{"Path": p, "UpdateType": "Updated"} for p in paths]
    req = urllib.request.Request(
        base + "/emby/Library/Media/Updated",
        data=json.dumps({"Updates": updates}).encode(),
        headers=headers,
        method="POST",
    )
    urllib.request.urlopen(req, timeout=120)


def main() -> int:
    parser = argparse.ArgumentParser(description="Add [LOCAL] to downloaded episode filenames")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--series", action="append", help="Series root path inside Emby container")
    args = parser.parse_args()
    dry_run = not args.apply
    if dry_run:
        print("DRY RUN — pass --apply to rename files\n")

    cfg = load_cfg()
    container = cfg.get("docker_container", "embyserver")
    roots = args.series or DEFAULT_SERIES
    files = list_videos(container, roots)
    print(f"Video files found: {len(files)}")

    renamed: list[tuple[str, str]] = []
    skipped = 0
    for src in files:
        dst = target_name(src)
        if not dst:
            skipped += 1
            continue
        print(f"  {src}")
        print(f"    -> {dst}")
        if not dry_run:
            r = docker_exec(container, f"mv '{src}' '{dst}'")
            if r.returncode != 0:
                print(f"    ERR: {r.stderr}", file=sys.stderr)
                continue
        renamed.append((src, dst))

    print(f"\nRenamed: {len(renamed)}, skipped (already marked): {skipped}")
    if renamed and not dry_run:
        notify_emby([dst for _src, dst in renamed], cfg)
        print("Emby notified of path updates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

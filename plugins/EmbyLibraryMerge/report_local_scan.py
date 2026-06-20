#!/usr/bin/env python3
import json
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).with_name("host-config.json")
cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
container = cfg.get("docker_container", "embyserver")

cmd = (
    f"docker exec {container} find /data/tv /data/tv-2 "
    r"-type f \( -iname '*.mkv' -o -iname '*.mp4' \) 2>/dev/null"
)
files = subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.splitlines()
series = {}
for f in files:
    f = f.strip()
    if not f:
        continue
    for base in ("/data/tv", "/data/tv-2"):
        if f.startswith(base + "/"):
            root = base + "/" + f[len(base) + 1 :].split("/")[0]
            series.setdefault(root, 0)
            series[root] += 1
            break

conn = sqlite3.connect(cfg["library_db"])
print(f"{'Serie':<45} {'Disco':>6} {'DB':>6}")
partial = []
missing = []
for root in sorted(series):
    name = root.split("/")[-1][:44]
    disk = series[root]
    db = conn.execute(
        "SELECT COUNT(*) FROM MediaItems WHERE type=8 AND Path LIKE ?",
        (root + "%",),
    ).fetchone()[0]
    print(f"{name:<45} {disk:>6} {db:>6}")
    if db == 0:
        missing.append(root)
    elif db < disk:
        partial.append((root, disk, db))
conn.close()
print(f"\nMancanti (0 in DB): {len(missing)}")
print(f"Parziali: {len(partial)}")

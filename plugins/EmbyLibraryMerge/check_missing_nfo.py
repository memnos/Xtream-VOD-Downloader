#!/usr/bin/env python3
import subprocess

MISSING = [
    "/data/tv/Outlander (2014)",
    "/data/tv/The Falcon and The Winter Soldier",
    "/data/tv/The Witcher",
    "/data/tv/Downton Abbey",
    "/data/tv/The Big Bang Theory (2007)",
    "/data/tv/The Last Kingdom",
]

container = "embyserver"
for path in MISSING:
    print(f"\n=== {path} ===")
    nfo = subprocess.run(
        f'docker exec {container} test -f "{path}/tvshow.nfo" && echo yes || echo no',
        shell=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  tvshow.nfo: {nfo}")
    mkv = subprocess.run(
        f'docker exec {container} find "{path}" -name "*.mkv" 2>/dev/null | wc -l',
        shell=True, capture_output=True, text=True,
    ).stdout.strip()
    print(f"  mkv count: {mkv}")
    ls = subprocess.run(
        f'docker exec {container} ls "{path}" 2>/dev/null',
        shell=True, capture_output=True, text=True,
    ).stdout.strip().split("\n")[:8]
    print(f"  contents: {ls}")

# find alternate nfo sources
print("\n=== Alternate Outlander nfo ===")
subprocess.run("docker exec embyserver test -f '/data/tv/Outlander/tvshow.nfo' && echo found", shell=True)

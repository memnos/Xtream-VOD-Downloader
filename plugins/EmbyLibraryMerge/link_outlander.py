#!/usr/bin/env python3
import subprocess

container = "embyserver"

def run(cmd):
    subprocess.run(f"docker exec {container} sh -c {repr(cmd)}", shell=True, check=True)

for src, dst in [
    ("/data/tv/Outlander (2014)/Season 08", "/data/tv/Outlander/Season 08"),
    ("/data/tv/Outlander (2014)/Season 09", "/data/tv/Outlander/Season 09"),
]:
    run(f"mkdir -p '{dst}'")
    out = subprocess.run(
        f"docker exec {container} sh -c \"ls '{src}'/*.mkv 2>/dev/null\"",
        shell=True, capture_output=True, text=True,
    )
    for f in out.stdout.splitlines():
        f = f.strip()
        if not f:
            continue
        base = f.split("/")[-1]
        run(f"test -e '{dst}/{base}' || ln -sf '{f}' '{dst}/{base}'")

n = subprocess.run(
    f"docker exec {container} find /data/tv/Outlander -name '*.mkv' | wc -l",
    shell=True, capture_output=True, text=True,
).stdout.strip()
print(f"Outlander mkv in /data/tv/Outlander: {n}")

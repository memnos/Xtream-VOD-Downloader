#!/usr/bin/env python3
import os
import re
import subprocess

def list_dirs(container_path):
    out = subprocess.check_output(
        ["docker", "exec", "embyserver", "ls", container_path],
        text=True,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def norm(name):
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name).strip().lower()
    name = re.sub(r"\s+", " ", name)
    return name


pairs = [
    ("/data/movies", "/data/strm/movies", "movies"),
    ("/data/tv", "/data/strm/series", "tv vs strm"),
    ("/data/tv-2", "/data/strm/series", "tv-2 vs strm"),
]
for a, b, label in pairs:
    da = {norm(x): x for x in list_dirs(a)}
    db = {norm(x): x for x in list_dirs(b)}
    overlap = sorted(set(da) & set(db))
    print(f"\n{label}: {len(overlap)} overlapping normalized names (sample 15)")
    for key in overlap[:15]:
        print(f"  {da[key]}  <->  {db[key]}")

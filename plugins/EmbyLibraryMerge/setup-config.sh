#!/usr/bin/env bash
# Crea host-config.json dalle credenziali xtream-downloader (se presenti)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/host-config.json"
AUTO="/home/fabio/xtream-downloader/.data/auto_download.json"

if [[ -f "$OUT" ]]; then
  echo "Esiste già $OUT"
  exit 0
fi

if [[ ! -f "$AUTO" ]]; then
  cp "$ROOT/host-config.example.json" "$OUT"
  echo "Creato $OUT — modifica emby_api_key manualmente"
  exit 0
fi

python3 - <<'PY' "$AUTO" "$OUT"
import json, sys
auto_path, out_path = sys.argv[1], sys.argv[2]
with open(auto_path, encoding="utf-8") as f:
    auto = json.load(f)
cfg = {
    "emby_url": auto.get("emby_url", "http://127.0.0.1:8096"),
    "emby_api_key": auto.get("emby_api_key", ""),
    "library_db": "/var/lib/emby_config/data/library.db",
    "docker_container": "embyserver",
    "prefer_local_movies": True,
    "merge_orphans": True,
}
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print(f"Creato {out_path} da {auto_path}")
PY

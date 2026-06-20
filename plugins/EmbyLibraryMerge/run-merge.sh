#!/usr/bin/env bash
# Unione completa film + serie (consigliato quando vedi duplicati TV)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$ROOT/host-config.json"

if [[ ! -f "$CONFIG" ]]; then
  echo "Crea $CONFIG da host-config.example.json" >&2
  exit 1
fi

python3 "$ROOT/host-merge.py" --config "$CONFIG" "$@"

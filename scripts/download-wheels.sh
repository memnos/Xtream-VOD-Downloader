#!/usr/bin/env bash
# Scarica i wheel sul host (fuori da Docker) per build offline.
# Esegui da WSL: bash scripts/download-wheels.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WHEELS="${ROOT}/wheels"

mkdir -p "${WHEELS}"
rm -f "${WHEELS}"/*.whl "${WHEELS}"/*.tar.gz 2>/dev/null || true

python3 -m pip install --upgrade pip
python3 -m pip download -r "${ROOT}/requirements.txt" -d "${WHEELS}"

count="$(find "${WHEELS}" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l)"
echo "OK: ${count} pacchetti in ${WHEELS}/"
echo "Ora esegui: docker compose build --no-cache"

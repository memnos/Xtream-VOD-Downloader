#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

docker run --rm --network host \
  -v "${ROOT}/wheels:/wheels" \
  -v "${ROOT}/requirements.txt:/req.txt:ro" \
  python:3.10-slim-bookworm \
  sh -c "pip install -q --upgrade pip && pip download -r /req.txt -d /wheels"

count="$(find "${ROOT}/wheels" -maxdepth 1 \( -name '*.whl' -o -name '*.tar.gz' \) | wc -l)"
echo "OK: ${count} pacchetti in ${ROOT}/wheels/"

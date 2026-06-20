#!/usr/bin/env bash
# Installazione completa mergerfs + Emby (eseguire su WSL con sudo disponibile).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EMBY_DIR=/home/fabio/emby
DOWNLOADER_ENV=/home/fabio/xtream-downloader/.env

echo "=== 1/5 Migrazione serie HDD2 -> HDD1 (se presenti) ==="
if [[ "${SKIP_MIGRATE:-0}" == "1" ]]; then
  echo "SKIP_MIGRATE=1: migrazione saltata"
elif pgrep -x rsync >/dev/null 2>&1; then
  echo "rsync già in esecuzione, continuo in parallelo"
else
  bash "${ROOT}/migrate-hdd2-to-hdd1.sh"
fi

echo ""
echo "=== 2/5 Installazione mergerfs + systemd ==="
bash "${ROOT}/setup-mergerfs.sh"

echo ""
echo "=== 3/5 Aggiornamento docker-compose Emby ==="
cp "${ROOT}/emby-docker-compose.yml" "${EMBY_DIR}/docker-compose.yml"

echo ""
echo "=== 4/5 Downloader: TV2 -> HDD1 ==="
if [[ -f "${DOWNLOADER_ENV}" ]]; then
  if grep -q '^TV2_PATH=' "${DOWNLOADER_ENV}"; then
    sed -i 's|^TV2_PATH=.*|TV2_PATH=/mnt/wsl/HDD1/Serie_Tv|' "${DOWNLOADER_ENV}"
  else
    echo 'TV2_PATH=/mnt/wsl/HDD1/Serie_Tv' >> "${DOWNLOADER_ENV}"
  fi
  grep TV2_PATH "${DOWNLOADER_ENV}" || true
fi

echo ""
echo "=== 5/5 Riavvio Emby ==="
cd "${EMBY_DIR}"
docker compose down
docker compose up -d

sleep 5
if docker ps --format '{{.Names}}' | grep -qx embyserver; then
  python3 "${ROOT}/update_emby_libraries.py" || echo "WARN: aggiorna librerie Emby manualmente"
  echo ""
  echo "Installazione completata."
  findmnt | grep -E 'union|mergerfs' || true
  systemctl is-enabled mergerfs-movies.service mergerfs-series.service
else
  echo "ERRORE: container embyserver non avviato"
  docker compose logs --tail 30
  exit 1
fi

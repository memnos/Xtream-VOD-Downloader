#!/usr/bin/env bash
# Ripristina library.db dal backup pre-merge (22:30 del 13/06)
set -euo pipefail

DB="/var/lib/emby_config/data/library.db"
BACKUP="/var/lib/emby_config/data/library.db.bak-20260613-223015"
CONTAINER="${EMBY_CONTAINER:-embyserver}"

if [[ ! -f "$BACKUP" ]]; then
  echo "Backup non trovato: $BACKUP" >&2
  exit 1
fi

echo "==> Stop $CONTAINER"
docker stop "$CONTAINER"

SAFETY="/var/lib/emby_config/data/library.db.bak-before-restore-$(date +%Y%m%d-%H%M%S)"
echo "==> Salvo stato attuale -> $SAFETY"
cp "$DB" "$SAFETY"
rm -f "${DB}-wal" "${DB}-shm" 2>/dev/null || true

echo "==> Ripristino da $BACKUP"
cp "$BACKUP" "$DB"

echo "==> Start $CONTAINER"
docker start "$CONTAINER"
echo "==> Ripristino completato. Attendi ~30s prima di verificare Emby."

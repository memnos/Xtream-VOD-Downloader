#!/bin/bash
# Installa la versione migliorata del healthcheck di sistema (serve sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
sudo install -m 0755 "$ROOT/mergerfs-docker-healthcheck.sh" /usr/local/bin/mergerfs-docker-healthcheck.sh
sudo tee /etc/systemd/system/mergerfs-docker-watchdog.service >/dev/null <<'UNIT'
[Unit]
Description=Auto-riparazione union mergerfs + riavvio container dipendenti
After=docker.service mergerfs-movies.service mergerfs-series.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/mergerfs-docker-healthcheck.sh
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now mergerfs-docker-watchdog.timer
sudo /usr/local/bin/mergerfs-docker-healthcheck.sh
echo "OK: watchdog di sistema aggiornato"

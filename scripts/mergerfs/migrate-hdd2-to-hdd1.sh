#!/usr/bin/env bash
# Sposta le serie da HDD2 a HDD1 (solo HDD1 in mergerfs).
set -euo pipefail

SRC=/mnt/wsl/HDD2/Serie_Tv
DST=/mnt/wsl/HDD1/Serie_Tv

[[ -d "$SRC" ]] || { echo "Niente da spostare: $SRC assente"; exit 0; }

mkdir -p "$DST"
echo "Copia serie HDD2 -> HDD1 (rsync)..."
rsync -avh --progress "$SRC/" "$DST/"
echo "Fatto. Verifica i file su HDD1 prima di cancellare HDD2."

#!/usr/bin/env bash
# Unisce file locali (HDD) e .strm (m3u-editor) con mergerfs per Emby.
# Esegui su WSL: bash scripts/mergerfs/setup-mergerfs.sh
set -euo pipefail

MOVIES_BRANCHES=(
  /mnt/wsl/HDD1/Movies
  /home/fabio/m3u-editor/movies
)

SERIES_BRANCHES=(
  /mnt/wsl/HDD1/Serie_Tv
  /home/fabio/m3u-editor/series
)

UNION_ROOT=/mnt/union
MOVIES_MOUNT="${UNION_ROOT}/Movies"
SERIES_MOUNT="${UNION_ROOT}/Serie_Tv"

MERGERFS_OPTS="allow_other,use_ino,category.create=ff,minfreespace=4G"

die() { echo "ERRORE: $*" >&2; exit 1; }

if ! command -v mergerfs >/dev/null 2>&1; then
  echo "Installazione mergerfs (serve root: wsl -u root bash setup-root.sh)..."
  if [[ "$(id -u)" -eq 0 ]]; then
    apt-get update -qq
    apt-get install -y mergerfs fuse3
  else
    die "Esegui: wsl -u root bash ${BASH_SOURCE[0]%/*}/setup-root.sh"
  fi
fi

if ! grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null; then
  echo "Abilito user_allow_other in /etc/fuse.conf (serve per Docker)..."
  echo 'user_allow_other' | sudo tee -a /etc/fuse.conf >/dev/null
fi

for branch in "${MOVIES_BRANCHES[@]}" "${SERIES_BRANCHES[@]}"; do
  [[ -d "$branch" ]] || die "cartella mancante: $branch"
done

sudo mkdir -p "$UNION_ROOT" "$MOVIES_MOUNT" "$SERIES_MOUNT"

mount_union() {
  local target="$1"
  shift
  local branches=("$@")
  local joined
  joined=$(IFS=:; echo "${branches[*]}")

  if mountpoint -q "$target" 2>/dev/null; then
    echo "Già montato: $target"
    return 0
  fi

  echo "mergerfs $joined -> $target"
  sudo mergerfs -o "${MERGERFS_OPTS},fsname=mergerfs_$(basename "$target")" \
    "$joined" "$target"
}

mount_union "$MOVIES_MOUNT" "${MOVIES_BRANCHES[@]}"
mount_union "$SERIES_MOUNT" "${SERIES_BRANCHES[@]}"

install_systemd() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  echo "Installazione servizi systemd..."
  sudo cp "${script_dir}/mergerfs-movies.service" /etc/systemd/system/
  sudo cp "${script_dir}/mergerfs-series.service" /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable mergerfs-movies.service mergerfs-series.service
  # Se già montati a mano, systemd li considera attivi al prossimo boot
  sudo systemctl start mergerfs-movies.service mergerfs-series.service 2>/dev/null || true
}

install_systemd

echo ""
echo "Mount attivi:"
findmnt -t fuse.mergerfs "$UNION_ROOT" || findmnt | grep -E 'union|mergerfs' || true
echo ""
echo "Servizi abilitati al boot:"
systemctl is-enabled mergerfs-movies.service mergerfs-series.service
echo ""
echo "Prossimo passo: aggiorna Emby e riavvia il container (vedi README)."

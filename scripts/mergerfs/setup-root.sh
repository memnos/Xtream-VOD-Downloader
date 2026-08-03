#!/usr/bin/env bash
set -euo pipefail

grep -q '^user_allow_other' /etc/fuse.conf 2>/dev/null || echo 'user_allow_other' >> /etc/fuse.conf

mkdir -p /mnt/union/Movies /mnt/union/Serie_Tv
OPTS='allow_other,use_ino,category.create=ff,minfreespace=4G'

if ! mountpoint -q /mnt/union/Movies; then
  mergerfs -o "${OPTS},fsname=mergerfs_Movies" \
    /mnt/wsl/HDD1/Movies:/home/fabio/m3u-editor/movies \
    /mnt/union/Movies
fi

if ! mountpoint -q /mnt/union/Serie_Tv; then
  mergerfs -o "${OPTS},fsname=mergerfs_Serie_Tv" \
    /mnt/wsl/HDD1/Serie_Tv:/home/fabio/m3u-editor/series \
    /mnt/union/Serie_Tv
fi

SCRIPT_DIR=/home/fabio/xtream-downloader/scripts/mergerfs
cp "${SCRIPT_DIR}/mergerfs-movies.service" /etc/systemd/system/
cp "${SCRIPT_DIR}/mergerfs-series.service" /etc/systemd/system/
cp "${SCRIPT_DIR}/mergerfs.target" /etc/systemd/system/
mkdir -p /etc/systemd/system/docker.service.d
cp "${SCRIPT_DIR}/docker-mergerfs.conf" /etc/systemd/system/docker.service.d/mergerfs.conf
systemctl daemon-reload
systemctl enable mergerfs.target mergerfs-movies.service mergerfs-series.service
systemctl restart mergerfs-movies.service mergerfs-series.service

echo "--- mount ---"
findmnt | grep -E 'union|mergerfs' || true
echo "--- systemd ---"
systemctl is-enabled mergerfs.target mergerfs-movies.service mergerfs-series.service
systemctl is-active mergerfs-movies.service mergerfs-series.service
echo "--- docker waits for mergerfs ---"
systemctl show docker.service -p After -p Requires --no-pager

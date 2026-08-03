#!/bin/bash
# Auto-riparazione mergerfs + Docker.
# Rileva union mount FUSE "stale" (Transport endpoint is not connected),
# servizi mergerfs falliti o Docker spento, e li ripristina da solo.
# Idempotente: se e' tutto a posto non fa nulla.
set -u

UNIONS=(/mnt/wsl/union/Movies /mnt/wsl/union/Serie_Tv)
SERVICES=(mergerfs-movies.service mergerfs-series.service)

stale=0
problem=0

for d in "${UNIONS[@]}"; do
  if mountpoint -q "$d"; then
    # Un mount stale fa fallire stat con ENOTCONN; timeout per non bloccarsi
    if ! timeout 5 stat "$d" >/dev/null 2>&1; then
      echo "union mount stale: $d -> umount lazy"
      umount -l "$d" 2>/dev/null || true
      stale=1
      problem=1
    fi
  else
    echo "union non montato: $d"
    problem=1
  fi
done

for s in "${SERVICES[@]}"; do
  if ! systemctl is-active --quiet "$s"; then
    echo "servizio non attivo: $s"
    problem=1
  fi
done

if ! systemctl is-active --quiet docker.service; then
  echo "docker.service non attivo"
  problem=1
fi

if [ "$problem" -eq 0 ]; then
  exit 0
fi

echo "riparazione mergerfs + docker in corso..."
systemctl reset-failed "${SERVICES[@]}" docker.socket docker.service 2>/dev/null || true
systemctl restart "${SERVICES[@]}"

if ! systemctl is-active --quiet docker.service; then
  systemctl start docker.service
elif [ "$stale" -eq 1 ]; then
  echo "union era stale: riavvio docker per rinfrescare i bind mount dei container"
  systemctl restart docker.service
fi

echo "riparazione completata"

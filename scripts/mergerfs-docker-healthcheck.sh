#!/bin/bash
# Auto-riparazione mergerfs + container che usano l'union.
# - Se l'union e' stale/non montata: rimonta mergerfs
# - Se il remount (o un kill 137) ha spento i container sull'union: li riavvia
# Idempotente: se e' tutto a posto non fa nulla.
set -u

LOG="${LOG:-/var/log/mergerfs-docker-healthcheck.log}"
UNIONS=(/mnt/wsl/union/Movies /mnt/wsl/union/Serie_Tv)
SERVICES=(mergerfs-movies.service mergerfs-series.service)

log() {
  local msg="$*"
  printf '%s %s\n' "$(date '+%F %T')" "$msg" | tee -a "$LOG" >/dev/null
  printf '%s\n' "$msg"
}

union_ok() {
  local d="$1"
  mountpoint -q "$d" || return 1
  timeout 5 stat "$d" >/dev/null 2>&1 || return 1
  timeout 5 ls "$d" >/dev/null 2>&1 || return 1
  return 0
}

discover_union_containers() {
  command -v docker >/dev/null 2>&1 || return 0
  local name src
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    src="$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$name" 2>/dev/null || true)"
    if printf '%s\n' "$src" | grep -qE '^/mnt/(wsl/)?union(/|$)'; then
      printf '%s\n' "$name"
    fi
  done < <(docker ps -a --format '{{.Names}}' 2>/dev/null)
}

ensure_container_running() {
  local name="$1" reason="$2"
  local status
  status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo missing)"
  if [ "$status" = "running" ]; then
    return 0
  fi
  if [ "$status" = "missing" ]; then
    log "container assente: $name"
    return 1
  fi
  log "riavvio container $name (status=$status, motivo: $reason)"
  if docker start "$name" >/dev/null 2>&1; then
    sleep 2
    status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo '?')"
    if [ "$status" = "running" ]; then
      log "container $name di nuovo running"
      return 0
    fi
  fi
  if docker restart "$name" >/dev/null 2>&1; then
    sleep 2
    status="$(docker inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo '?')"
    log "container $name dopo restart: $status"
    [ "$status" = "running" ]
    return $?
  fi
  log "ERRORE: impossibile avviare $name"
  return 1
}

stale=0
problem=0

for d in "${UNIONS[@]}"; do
  if mountpoint -q "$d"; then
    if ! union_ok "$d"; then
      log "union mount stale: $d -> umount lazy"
      umount -l "$d" 2>/dev/null || true
      stale=1
      problem=1
    fi
  else
    log "union non montato: $d"
    problem=1
  fi
done

for s in "${SERVICES[@]}"; do
  if ! systemctl is-active --quiet "$s"; then
    log "servizio non attivo: $s"
    problem=1
  fi
done

docker_was_down=0
if ! systemctl is-active --quiet docker.service; then
  log "docker.service non attivo"
  problem=1
  docker_was_down=1
fi

repaired=0
if [ "$problem" -eq 1 ]; then
  log "riparazione mergerfs in corso..."
  systemctl reset-failed "${SERVICES[@]}" 2>/dev/null || true
  systemctl restart "${SERVICES[@]}"
  repaired=1

  for d in "${UNIONS[@]}"; do
    if union_ok "$d"; then
      log "union OK: $d"
    else
      log "ERRORE: union ancora non OK: $d"
    fi
  done

  if [ "$docker_was_down" -eq 1 ]; then
    systemctl reset-failed docker.socket docker.service 2>/dev/null || true
    systemctl start docker.service
    for i in $(seq 1 30); do
      docker info >/dev/null 2>&1 && break
      sleep 1
    done
    log "docker.service ripartito"
  fi

  # NON riavviare tutto Docker: basta restart dei container sull'union
  mapfile -t UNION_CTRS < <(discover_union_containers)
  if [ "${#UNION_CTRS[@]}" -eq 0 ]; then
    log "nessun container con mount union trovato"
  else
    log "riavvio container su union: ${UNION_CTRS[*]}"
    for c in "${UNION_CTRS[@]}"; do
      if docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null | grep -qx running; then
        log "restart $c (refresh bind mount)"
        docker restart "$c" >/dev/null 2>&1 || ensure_container_running "$c" "post-remount"
      else
        ensure_container_running "$c" "post-remount"
      fi
    done
  fi

  log "riparazione completata"
fi

# Container union usciti con 137/OOM -> ripartenza se mount OK
if systemctl is-active --quiet docker.service && docker info >/dev/null 2>&1; then
  mapfile -t UNION_CTRS < <(discover_union_containers)
  for c in "${UNION_CTRS[@]}"; do
    status="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
    [ "$status" = "running" ] && continue
    [ "$status" = "missing" ] && continue
    exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$c" 2>/dev/null || echo 0)"
    oom="$(docker inspect -f '{{.State.OOMKilled}}' "$c" 2>/dev/null || echo false)"
    if [ "$exit_code" = "137" ] || [ "$oom" = "true" ] || [ "$repaired" -eq 1 ]; then
      all_ok=1
      for d in "${UNIONS[@]}"; do
        union_ok "$d" || all_ok=0
      done
      if [ "$all_ok" -eq 1 ]; then
        ensure_container_running "$c" "exit=${exit_code} oom=${oom}"
      else
        log "skip start $c: union non ancora OK"
      fi
    fi
  done
fi

exit 0

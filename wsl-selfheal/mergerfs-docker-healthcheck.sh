#!/bin/bash
# Auto-riparazione mergerfs + container che usano l'union.
# - Se l'union e' ENOTCONN/non montata: rimonta mergerfs
# - Timeout su HDD USB lento: ritenta, NON smonta se mergerfs e' vivo
# - Se il remount (o un kill 137) ha spento i container sull'union: li riavvia
# Idempotente: se e' tutto a posto non fa nulla.
set -u

LOG="${LOG:-/var/log/mergerfs-docker-healthcheck.log}"
UNIONS=(/mnt/wsl/union/Movies /mnt/wsl/union/Serie_Tv)
SERVICES=(mergerfs-movies.service mergerfs-series.service)
PROBE_TIMEOUT="${PROBE_TIMEOUT:-20}"
PROBE_RETRIES="${PROBE_RETRIES:-3}"
PROBE_RETRY_SLEEP="${PROBE_RETRY_SLEEP:-5}"

log() {
  local msg="$*"
  printf '%s %s\n' "$(date '+%F %T')" "$msg" | tee -a "$LOG" >/dev/null
  printf '%s\n' "$msg"
}

# 0=ok 1=not_mount 2=ENOTCONN 124=timeout other=error
probe_union() {
  local d="$1"
  mountpoint -q "$d" || return 1
  if [ -x /usr/bin/python3 ]; then
    timeout "$PROBE_TIMEOUT" /usr/bin/python3 -c '
import os, sys, errno
try:
    os.stat(sys.argv[1])
except OSError as e:
    sys.exit(2 if e.errno == errno.ENOTCONN else 1)
' "$d"
    return $?
  fi
  local err rc
  err="$(timeout "$PROBE_TIMEOUT" stat "$d" 2>&1 >/dev/null)"
  rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$rc" -eq 124 ] && return 124
  printf '%s' "$err" | grep -qiE 'not connected|ENOTCONN|NotConnected|code: 107' && return 2
  return 1
}

mergerfs_alive_for() {
  local mp="$1"
  pgrep -f "/usr/bin/mergerfs .*$mp" >/dev/null 2>&1
}

union_ok() {
  local rc
  probe_union "$1"
  rc=$?
  [ "$rc" -eq 0 ]
}

# Smonta solo su ENOTCONN o mergerfs morto. Un timeout con processo vivo non e' stale.
check_union() {
  local d="$1"
  local attempt rc last_rc=1

  if ! mountpoint -q "$d"; then
    log "union non montato: $d"
    problem=1
    return 0
  fi

  for attempt in $(seq 1 "$PROBE_RETRIES"); do
    probe_union "$d"
    rc=$?
    last_rc=$rc
    case "$rc" in
      0)
        return 0
        ;;
      2)
        if [ "$attempt" -lt 2 ]; then
          log "union ENOTCONN: $d (tentativo $attempt/$PROBE_RETRIES), riprovo"
          sleep 1
          continue
        fi
        log "union mount stale (ENOTCONN): $d -> umount lazy"
        umount -l "$d" 2>/dev/null || true
        stale=1
        problem=1
        return 0
        ;;
      124)
        if mergerfs_alive_for "$d"; then
          log "union lento (timeout ${PROBE_TIMEOUT}s): $d tentativo $attempt/$PROBE_RETRIES, mergerfs vivo, non smonto"
          sleep "$PROBE_RETRY_SLEEP"
          continue
        fi
        log "union timeout e mergerfs assente: $d -> umount lazy"
        umount -l "$d" 2>/dev/null || true
        stale=1
        problem=1
        return 0
        ;;
      1)
        log "union non montato: $d"
        problem=1
        return 0
        ;;
      *)
        if mergerfs_alive_for "$d"; then
          log "union errore probe rc=$rc: $d tentativo $attempt/$PROBE_RETRIES, mergerfs vivo"
          sleep "$PROBE_RETRY_SLEEP"
          continue
        fi
        log "union errore probe rc=$rc e mergerfs assente: $d -> umount lazy"
        umount -l "$d" 2>/dev/null || true
        stale=1
        problem=1
        return 0
        ;;
    esac
  done

  if [ "$last_rc" -eq 0 ]; then
    return 0
  fi
  if mergerfs_alive_for "$d"; then
    log "union ancora lento/erratico ma mergerfs vivo: $d -> skip umount"
    return 0
  fi
  log "union non recuperato: $d rc=$last_rc -> umount lazy"
  umount -l "$d" 2>/dev/null || true
  stale=1
  problem=1
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
  check_union "$d"
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

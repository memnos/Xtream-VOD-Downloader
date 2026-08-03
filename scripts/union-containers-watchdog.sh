#!/bin/bash
# Watchdog utente: se jellyfin/embyserver (mount union) sono spenti
# dopo kill 137/OOM, e l'union risponde, li riavvia.
# Non rimonta mergerfs (serve root): quello resta al timer di sistema.
set -u
LOG="${HOME}/.local/log/union-containers-watchdog.log"
mkdir -p "$(dirname "$LOG")"
UNIONS=(/mnt/wsl/union/Movies /mnt/wsl/union/Serie_Tv)

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }

union_ok() {
  local d="$1"
  mountpoint -q "$d" 2>/dev/null || return 1
  timeout 5 stat "$d" >/dev/null 2>&1 || return 1
  timeout 5 ls "$d" >/dev/null 2>&1 || return 1
  return 0
}

discover_union_containers() {
  local name src
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    src="$(docker inspect -f '{{range .Mounts}}{{println .Source}}{{end}}' "$name" 2>/dev/null || true)"
    if printf '%s\n' "$src" | grep -qE '^/mnt/(wsl/)?union(/|$)'; then
      printf '%s\n' "$name"
    fi
  done < <(docker ps -a --format '{{.Names}}' 2>/dev/null)
}

all_ok=1
for d in "${UNIONS[@]}"; do
  if ! union_ok "$d"; then
    log "union NON OK: $d (aspetto remount di sistema)"
    all_ok=0
  fi
done
[ "$all_ok" -eq 1 ] || exit 0

docker info >/dev/null 2>&1 || exit 0

mapfile -t CTRS < <(discover_union_containers)
for c in "${CTRS[@]}"; do
  status="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo missing)"
  [ "$status" = "running" ] && continue
  [ "$status" = "missing" ] && continue
  exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$c" 2>/dev/null || echo 0)"
  oom="$(docker inspect -f '{{.State.OOMKilled}}' "$c" 2>/dev/null || echo false)"
  # 137 = SIGKILL (remount/docker kill); 0 after unexpected stop also restart
  # if container has RestartPolicy unless-stopped but stayed down
  policy="$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$c" 2>/dev/null || echo '')"
  should=0
  if [ "$exit_code" = "137" ] || [ "$oom" = "true" ]; then
    should=1
  elif [ "$policy" = "unless-stopped" ] || [ "$policy" = "always" ]; then
    # spento ma policy dice che dovrebbe essere su (es. dopo restart docker)
    should=1
  fi
  if [ "$should" -eq 1 ]; then
    log "start $c (status=$status exit=$exit_code oom=$oom policy=$policy)"
    if docker start "$c" >/dev/null 2>&1; then
      sleep 2
      st="$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null)"
      log "$c -> $st"
    else
      log "ERRORE start $c"
    fi
  fi
done
exit 0

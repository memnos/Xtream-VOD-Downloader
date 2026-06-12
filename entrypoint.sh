#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Fix ownership on bind mounts when the container runs as root.
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/.data /download/movies /download/tv /download/tv-2
  chown -R "${PUID}:${PGID}" /app/.data /download/movies /download/tv /download/tv-2 2>/dev/null || true
  find /download -type d -exec chmod 777 {} + 2>/dev/null || true
  find /download -type f -exec chmod 664 {} + 2>/dev/null || true
fi

python /app/watcher_daemon.py &
exec streamlit run /app/app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

#!/bin/sh
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Fix ownership on bind-mount roots only (not a recursive library walk).
# Full chown/find over /download can take minutes on large HDD/WSL libraries and
# blocked Streamlit startup; new files are fixed by finalize_download_path().
if [ "$(id -u)" = "0" ]; then
  mkdir -p /app/.data /download/movies /download/tv
  chown "${PUID}:${PGID}" /app/.data /download/movies /download/tv 2>/dev/null || true
  chmod 777 /download/movies /download/tv 2>/dev/null || true
  chmod 775 /app/.data 2>/dev/null || true
fi

python /app/watcher_daemon.py &
python /app/stream_proxy.py &
exec streamlit run /app/app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

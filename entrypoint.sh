#!/bin/sh
set -e
python /app/watcher_daemon.py &
exec streamlit run /app/app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.fileWatcherType=none \
  --browser.gatherUsageStats=false

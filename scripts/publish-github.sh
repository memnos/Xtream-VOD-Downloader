#!/usr/bin/env bash
set -eu
GH="${GH_BIN:-gh}"
REPO_NAME="${1:-xtream-emby-downloader}"
"$GH" repo create "$REPO_NAME" \
  --public \
  --description "Streamlit + yt-dlp downloader for Xtream VOD with Emby auto-download watcher (EN/IT)" \
  --source=. \
  --remote=origin \
  --push
echo "Done: https://github.com/$("$GH" api user -q .login)/${REPO_NAME}"

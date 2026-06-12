#!/usr/bin/env bash
# Run from the project root after: gh auth login
set -euo pipefail

REPO_NAME="${1:-xtream-emby-downloader}"

gh repo create "$REPO_NAME" \
  --public \
  --description "Streamlit + yt-dlp downloader for Xtream VOD with Emby auto-download watcher (EN/IT)" \
  --source=. \
  --remote=origin \
  --push

echo "Done: https://github.com/$(gh api user -q .login)/${REPO_NAME}"

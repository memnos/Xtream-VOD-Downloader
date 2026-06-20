#!/usr/bin/env bash
ENV=/home/fabio/xtream-downloader/.env
set_kv() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "$ENV"
  else
    echo "${key}=${val}" >> "$ENV"
  fi
}
set_kv STRM_MOVIES_PATH /home/fabio/m3u-editor/movies
set_kv STRM_SERIES_PATH /home/fabio/m3u-editor/series
set_kv TV2_PATH /mnt/wsl/HDD1/Serie_Tv
grep -E 'MOVIES_PATH|TV_PATH|TV2_PATH|STRM' "$ENV"

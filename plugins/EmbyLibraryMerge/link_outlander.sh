#!/bin/bash
set -e
link_season() {
  src_dir="$1"
  dst_dir="$2"
  mkdir -p "$dst_dir"
  for f in "$src_dir"/*.mkv; do
    [ -f "$f" ] || continue
    base=$(basename "$f")
    if [ ! -e "$dst_dir/$base" ]; then
      ln -sf "$f" "$dst_dir/$base"
    fi
  done
}
link_season "/data/tv/Outlander (2014)/Season 08" "/data/tv/Outlander/Season 08"
link_season "/data/tv/Outlander (2014)/Season 09" "/data/tv/Outlander/Season 09"
find "/data/tv/Outlander" -name '*.mkv' | wc -l

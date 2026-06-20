#!/usr/bin/env bash
set -u
BASE=/mnt/wsl/HDD1/spazzatura

paths=(
  "m3u/Movies/dggh/6"
  "m3u/Movies/dggh/7"
  "m3u/Movies/dggh/5"
  "IPTVDIR/Movies/Pokémon - Kyurem e il solenne spadaccino"
  "IPTVDIR/Movies/Pokémon - Lascesa di Darkrai (2007)"
  "IPTVDIR/Movies/Pokémon - Lucario e il mistero di Mew (2005)"
)

for p in "${paths[@]}"; do
  src="$BASE/$p"
  dst="$BASE/${p}.broken"
  if [ -e "$src" ] 2>/dev/null; then
    mv "$src" "$dst" 2>/dev/null || true
  fi
done

rm -rf "$BASE/m3u" "$BASE/IPTVDIR" 2>/dev/null || true
rm -rf "$BASE" 2>/dev/null || true

if [ -d "$BASE" ]; then
  # ultima risorsa: nascondi tutto
  mv "$BASE" /mnt/wsl/HDD1/.spazzatura.broken 2>/dev/null || true
fi

if [ -d /mnt/wsl/HDD1/spazzatura ]; then
  echo "RESTA: /mnt/wsl/HDD1/spazzatura"
  find /mnt/wsl/HDD1/spazzatura 2>&1 | head -20
elif [ -d /mnt/wsl/HDD1/.spazzatura.broken ]; then
  echo "OK: spazzatura nascosta in .spazzatura.broken (dentry corrotti, serve e2fsck per pulizia fisica)"
else
  echo "OK: spazzatura rimossa"
fi

#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_ROOT="${EMBY_PLUGIN_DIR:-/var/lib/emby_config/plugins}"
PLUGIN_SUBDIR="$PLUGIN_ROOT/EmbyLibraryMerge"
CONTAINER="${EMBY_CONTAINER:-embyserver}"
DLL="$ROOT/bin/Release/net8.0/EmbyLibraryMerge.dll"

if [[ ! -f "$DLL" ]]; then
  echo "Plugin non compilato. Esegui prima: bash build.sh" >&2
  exit 1
fi

echo "==> Installing to $PLUGIN_ROOT/EmbyLibraryMerge.dll"
cp "$DLL" "$PLUGIN_ROOT/EmbyLibraryMerge.dll"

echo "==> Installing to $PLUGIN_SUBDIR/EmbyLibraryMerge.dll"
mkdir -p "$PLUGIN_SUBDIR"
cp "$DLL" "$PLUGIN_SUBDIR/"

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "==> Restarting $CONTAINER"
  docker restart "$CONTAINER"
  echo "==> Attendi ~30s e verifica Dashboard → Plugin → Library Merge"
else
  echo "==> Container $CONTAINER not running; copy completed."
fi

echo "==> Attività pianificate (categoria 'Library Merge'):"
echo "    - Unisci film duplicati (MKV + STRM)"
echo "    - Unisci serie duplicate (MKV + STRM)"
echo "    - Ripara metadati serie (stagioni + episodi)"

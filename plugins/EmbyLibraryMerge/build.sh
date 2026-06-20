#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
LIB_DIR="$ROOT/lib"
OUT_DIR="$ROOT/bin/Release/net8.0"
CONTAINER="${EMBY_CONTAINER:-embyserver}"

echo "==> Copying Emby reference DLLs from container $CONTAINER"
mkdir -p "$LIB_DIR"
for dll in MediaBrowser.Common MediaBrowser.Controller MediaBrowser.Model; do
  docker cp "$CONTAINER:/system/${dll}.dll" "$LIB_DIR/"
done

echo "==> Building plugin"
docker run --rm \
  -v "$ROOT:/src" \
  -w /src \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  dotnet build -c Release

echo "==> Built: $OUT_DIR/EmbyLibraryMerge.dll"
ls -la "$OUT_DIR/EmbyLibraryMerge.dll"

#!/bin/bash
set -euo pipefail

LIB_DB="/var/lib/emby_config/data/library.db"
AUTH_DB="/var/lib/emby_config/data/authentication.db"

echo "=== API Keys ==="
sqlite3 "$AUTH_DB" "SELECT AccessToken, Name FROM ApiKeys LIMIT 5;" 2>/dev/null || sqlite3 "$AUTH_DB" ".tables"

echo ""
echo "=== Virtual Folders / Libraries ==="
sqlite3 "$LIB_DB" "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%Folder%' OR name LIKE '%Library%' OR name LIKE '%Virtual%';"

echo ""
echo "=== All tables (sample) ==="
sqlite3 "$LIB_DB" "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;" | head -50

echo ""
echo "=== Folder structure in media paths ==="
echo "--- /data/movies ---"
docker exec embyserver find /data/movies -maxdepth 2 -type d 2>/dev/null | head -20
echo "--- /data/strm ---"
docker exec embyserver find /data/strm -maxdepth 3 -type d 2>/dev/null | head -30
echo "--- /data/tv ---"
docker exec embyserver find /data/tv -maxdepth 2 -type d 2>/dev/null | head -20
echo "--- /data/tv-2 ---"
docker exec embyserver find /data/tv-2 -maxdepth 2 -type d 2>/dev/null | head -20

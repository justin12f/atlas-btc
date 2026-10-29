#!/usr/bin/env bash
# Respaldo diario: sqlite3 .backup + gzip; 30 días locales. Copia fuera del VPS opcional (ATLAS_BACKUP_REMOTE).
set -euo pipefail
DATA="${ATLAS_DATA_DIR:-/var/lib/atlas}"
DB="$DATA/data/live/atlas.db"
OUT="$DATA/backups/atlas-$(date -u +%F).db"
[ -f "$DB" ] || { echo "no existe $DB"; exit 1; }
sqlite3 "$DB" ".backup '$OUT'"
sqlite3 "$OUT" "PRAGMA integrity_check;" | grep -qx ok
gzip -f "$OUT"
find "$DATA/backups" -name 'atlas-*.db.gz' -mtime +30 -delete
# Ejemplos: ATLAS_BACKUP_REMOTE="rclone:gdrive:atlas"  o  "rsync:usuario@laptop:/ruta/atlas-backups/"
case "${ATLAS_BACKUP_REMOTE:-}" in
  rclone:*) rclone copy "$OUT.gz" "${ATLAS_BACKUP_REMOTE#rclone:}" ;;
  rsync:*)  rsync -a "$OUT.gz" "${ATLAS_BACKUP_REMOTE#rsync:}" ;;
  "") ;;
esac
echo "respaldo $OUT.gz"

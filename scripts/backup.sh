#!/usr/bin/env bash
# Sauvegarde souveraine (A7) : base SQLite de l'espace utilisateur + dump de l'index Meili.
# Tout reste LOCAL (aucune exfiltration). À planifier en cron sur le VPS.
#
#   ./scripts/backup.sh [DOSSIER_DESTINATION]
#
# Prérequis : lire /opt/jurilux-api/.env (DB_PATH, MEILI_URL, MEILI_MASTER_KEY).
set -euo pipefail

DEST="${1:-/opt/jurilux-api/backups}"
ENV_FILE="${ENV_FILE:-/opt/jurilux-api/.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

DB_PATH="${DB_PATH:-/var/lib/jurilux/jurilux.db}"
MEILI_URL="${MEILI_URL:-http://127.0.0.1:7700}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$DEST/$STAMP"
mkdir -p "$OUT"

# 1) Base SQLite : sauvegarde cohérente (à chaud) via l'API de backup SQLite.
if [ -f "$DB_PATH" ]; then
  sqlite3 "$DB_PATH" ".backup '$OUT/jurilux.db'"
  echo "SQLite -> $OUT/jurilux.db"
fi

# 2) Meilisearch : déclenche un dump (l'archive reste dans le volume Meili `dumps/`).
if [ -n "${MEILI_MASTER_KEY:-}" ]; then
  curl -fsS -X POST "$MEILI_URL/dumps" \
    -H "Authorization: Bearer $MEILI_MASTER_KEY" | tee "$OUT/meili_dump.json"
  echo "Meili dump déclenché (voir le volume dumps/ de Meilisearch)."
fi

# 3) Rétention : ne garder que les 14 dernières sauvegardes.
ls -1dt "$DEST"/*/ 2>/dev/null | tail -n +15 | xargs -r rm -rf
echo "Sauvegarde terminée : $OUT"

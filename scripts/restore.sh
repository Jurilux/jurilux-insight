#!/usr/bin/env bash
# Restauration (A7) de la base SQLite à partir d'une sauvegarde produite par backup.sh.
#
#   ./scripts/restore.sh /opt/jurilux-api/backups/<STAMP>/jurilux.db
#
# ATTENTION : écrase la base courante. Arrêter l'API avant (docker compose stop api).
# L'index Meili se restaure séparément (importDump au démarrage de Meilisearch).
set -euo pipefail

SRC="${1:?usage: restore.sh <chemin/vers/jurilux.db>}"
ENV_FILE="${ENV_FILE:-/opt/jurilux-api/.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
DB_PATH="${DB_PATH:-/var/lib/jurilux/jurilux.db}"

[ -f "$SRC" ] || { echo "Source introuvable : $SRC" >&2; exit 1; }

# Vérifie l'intégrité de la sauvegarde avant d'écraser quoi que ce soit.
sqlite3 "$SRC" "PRAGMA integrity_check;" | grep -qx "ok" || {
  echo "Sauvegarde corrompue (integrity_check != ok) — abandon." >&2; exit 1; }

if [ -f "$DB_PATH" ]; then
  cp -f "$DB_PATH" "$DB_PATH.avant-restore-$(date -u +%Y%m%dT%H%M%SZ)"
fi
mkdir -p "$(dirname "$DB_PATH")"
cp -f "$SRC" "$DB_PATH"
echo "Base restaurée depuis $SRC -> $DB_PATH (relancer l'API)."

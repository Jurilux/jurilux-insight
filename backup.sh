#!/usr/bin/env bash
# Sauvegarde Jurilux : dump Meilisearch + archive /data (laws + pdfs).
# IMPORTANT : écrit dans /data/backups (hors /opt/jurilux-api), car le workflow de
# déploiement fait `rsync --delete` sur /opt et supprimerait tout backup qui y traîne.
# Ce script vit DANS le repo → déployé à /opt/jurilux-api/backup.sh et lancé par cron.
# Rétention locale 14 jours. TODO : push vers object storage OVH (creds manquants).
set -euo pipefail
BK=/data/backups
COMPOSE="docker compose -f /opt/jurilux-api/docker-compose.yml"
KEY=$(grep ^MEILI_MASTER_KEY /opt/jurilux-api/.env | cut -d= -f2)
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p "$BK"

# 1. Dump Meili via API, attendre le succès
uid=$(curl -s -X POST -H "Authorization: Bearer $KEY" http://127.0.0.1:7700/dumps | sed -E 's/.*"taskUid":([0-9]+).*/\1/')
for i in $(seq 1 200); do
  st=$(curl -s -H "Authorization: Bearer $KEY" http://127.0.0.1:7700/tasks/$uid | sed -E 's/.*"status":"([a-z]+)".*/\1/')
  [ "$st" = succeeded ] && break
  [ "$st" = failed ] && { echo "dump FAILED"; exit 1; }
  sleep 3
done

# 2. Extraire le dump du volume
dump=$($COMPOSE exec -T meilisearch sh -c "ls -1t /meili_data/dumps/*.dump | head -1" | tr -d '\r')
$COMPOSE cp "meilisearch:$dump" "$BK/meili-$STAMP.dump"

# 3. Archiver /data/laws seulement (petit). PAS /data/pdfs : ~5 Go, statiques et
#    re-téléchargeables via fetch_jurisprudence.py ; le dump Meili contient déjà tout
#    le texte indexé. Éviter d'accumuler des archives de 5 Go (saturation disque).
tar -C /data -czf "$BK/laws-$STAMP.tar.gz" laws

# 3b. Purge des dumps dans le volume (déjà copiés)
$COMPOSE exec -T meilisearch sh -c "find /meili_data/dumps -name '*.dump' -mtime +1 -delete" 2>/dev/null || true

# 4. Rétention 14 jours (dumps + archives lois ; purge aussi d'anciennes archives data-*)
find "$BK" -name 'meili-*.dump' -mtime +14 -delete
find "$BK" -name 'laws-*.tar.gz' -mtime +14 -delete
find "$BK" -name 'data-*.tar.gz' -delete

echo "OK backup $STAMP"
ls -lh "$BK" | tail -6

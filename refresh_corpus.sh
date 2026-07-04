#!/usr/bin/env bash
# Rafraîchit le corpus (V1.5 « corpus vivant »), à lancer par cron.
# - jurisprudence : re-télécharge les zips des années récentes (nouvelles décisions
#   ajoutées aux archives de l'année en cours) pour tous les datasets data.public.lu ;
# - lois : fetch_legilux ne re-télécharge que les consolidations au nouveau nom (idempotent) ;
# - ré-indexe, puis met à jour l'index `corpus_meta` (périmètre affiché par le front).
#
# Ré-indexer ~50k PDFs prend 1–3 h : prévu pour tourner en cron mensuel, la nuit.
# Usage : sudo /opt/jurilux-api/refresh_corpus.sh   (log : /var/log/jurilux-refresh.log)
set -euo pipefail
cd /opt/jurilux-api
COMPOSE="docker compose -f /opt/jurilux-api/docker-compose.yml"
KEY=$(grep ^MEILI_MASTER_KEY /opt/jurilux-api/.env | cut -d= -f2)
MEILI=http://127.0.0.1:7700
YEAR=$(date +%Y); MIN=$((YEAR-1))

echo "== $(date -Is) refresh corpus (jurisprudence >= $MIN, lois) =="

# 1. Jurisprudence — années récentes, tous les datasets de l'org (sauf rien).
SLUGS=$(python3 -c "
import json,urllib.request
UA={'User-Agent':'Mozilla/5.0'}
def get(u): return json.loads(urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=60).read())
u='https://data.public.lu/api/1/organizations/administration-judiciaire/datasets/?page_size=50'; out=[]
while u:
    d=get(u); out+=[x['slug'] for x in d.get('data',[])]; u=d.get('next_page')
print(' '.join(out))")
python3 ingest/fetch_jurisprudence.py /data/pdfs --min-year "$MIN" $SLUGS || echo "WARN fetch jurisprudence partiel"

# 2. Lois — consolidations nouvelles seulement (fetch_legilux saute l'existant).
python3 ingest/fetch_legilux.py ingest/legilux_codes.txt /data/laws || echo "WARN fetch lois partiel"

# 3. Ré-indexation (idempotent : mêmes chunk_id -> upsert).
$COMPOSE run --rm -v /data:/data api python -m ingest.index_pdfs /data/laws --source-type law
$COMPOSE run --rm -v /data:/data api python -m ingest.index_pdfs /data/pdfs

# 4. Périmètre du corpus (index corpus_meta, lu par /api/corpus).
DEC=$(find /data/pdfs -maxdepth 1 -name '*.pdf' | wc -l)
TXT=$(find /data/laws -maxdepth 1 -name '*.pdf' | wc -l)
UPD=$(date +%Y-%m-%d)
curl -s -X PUT "$MEILI/indexes/corpus_meta/documents" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d "[{\"id\":1,\"decisions\":$DEC,\"texts\":$TXT,\"updated\":\"$UPD\"}]" >/dev/null

echo "== $(date -Is) refresh terminé : $DEC décisions, $TXT textes, maj $UPD =="

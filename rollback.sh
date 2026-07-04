#!/usr/bin/env bash
# Rollback RAPIDE du backend : rebascule le conteneur `api` sur l'image précédente
# (jurilux-api-api:previous, sauvegardée par le workflow avant chaque rebuild), SANS
# rebuild. Utile si le dernier déploiement casse la prod.
#
# Pour revenir plus loin dans l'historique : GitHub Actions → « Deploy API » →
# Run workflow → ref = <tag> (ex. v1.0.0). Cela reconstruit exactement cette version.
#
# Usage sur le VPS : sudo /opt/jurilux-api/rollback.sh
set -euo pipefail
cd /opt/jurilux-api

if ! docker image inspect jurilux-api-api:previous >/dev/null 2>&1; then
  echo "Aucune image :previous disponible — impossible de rollback en local."
  echo "Utiliser GitHub Actions (Deploy API, ref=<tag>) pour redéployer une version."
  exit 1
fi

echo "Ref actuellement déployée : $(cat /opt/jurilux-api/.deployed_ref 2>/dev/null || echo '?')"
echo "Bascule sur l'image précédente (:previous)…"
# La mauvaise image (:latest) devient récupérable via un redéploiement Actions.
docker image tag jurilux-api-api:previous jurilux-api-api:latest
docker compose up -d --no-build api

sleep 5
if curl -sf http://127.0.0.1:8088/health >/dev/null; then
  echo "Rollback OK — image précédente active, /health répond."
else
  echo "ATTENTION : /health ne répond pas après rollback. Vérifier : docker compose logs api"
  exit 1
fi

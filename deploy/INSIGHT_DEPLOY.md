# Déploiement de `insight.jurilux.lu`

Mise en ligne du produit **Jurilux Insight** (dashboard analytics contentieux) sur le
sous-domaine `insight.jurilux.lu`, à côté de `jurilux.lu` (produit RAG), **sur le même VPS**.

## Architecture cible
```
Caddy (VPS OVH)
  ├─ jurilux.lu           ──> jurilux-api        (127.0.0.1:8088)  + front jurilux-web
  └─ insight.jurilux.lu   ──> jurilux-insight    (127.0.0.1:8089)  + front jurilux-insight-web
        ├─ /api/*, /health ──> 127.0.0.1:8089
        ├─ /docs/*         ──> /data/pdfs        (corpus PARTAGÉ)
        └─ le reste        ──> /var/www/juriscope/insight/current (SPA)
```
Le backend insight **réutilise le Meilisearch/Ollama de jurilux-api** (même corpus) : pas de
second index. Il a sa **propre base SQLite** (`insight_data`) et son **propre port** (8089).

## Prérequis (actions manuelles, une fois)
1. **DNS** : enregistrement A `insight.jurilux.lu` → IP du VPS (Caddy fera le TLS automatiquement).
2. **Secrets GitHub** sur les deux nouveaux dépôts (`Settings → Secrets → Actions`) :
   - `jurilux-insight` : `DEPLOY_HOST`, `DEPLOY_SSH_KEY` (mêmes valeurs que jurilux-api).
   - `jurilux-insight-web` : `DEPLOY_HOST`, `DEPLOY_SSH_KEY`.
3. **VPS** :
   - `/opt/jurilux-insight/.env` (copier depuis `/opt/jurilux-api/.env` — mêmes clés ; `MEILI_URL`
     par défaut = `http://host.docker.internal:7700`).
   - Stack `jurilux-api` up (le Meili partagé doit tourner).
   - Ajouter le bloc `deploy/Caddyfile.insight` au Caddyfile du VPS puis `sudo caddy reload`
     (ou `systemctl reload caddy`).

## Déploiement (automatique au push sur `main`)
- **Backend** : push `jurilux-insight` → `Deploy Insight API` (gate pytest rapide → rsync
  `/opt/jurilux-insight` → `docker compose up -d --build` → smoke test `:8089/health`).
- **Front** : push `jurilux-insight-web` → `Deploy Insight Front` (build Vite → rsync
  `/var/www/juriscope/insight/current` → smoke test `https://insight.jurilux.lu/`).

## Données du dashboard
Les endpoints `/api/insight/*` lisent la table `insight_appearances` de la base SQLite d'insight.
Tant que le **build insight** n'a pas tourné, les vues affichent un état vide (gracieux). Pour
peupler : exécuter `insight_build.py` contre le corpus (comme le cron `refresh_corpus.sh` de
jurilux-api). La base SQLite d'insight étant distincte, ce build doit être lancé côté insight.

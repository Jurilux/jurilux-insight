# Runbook VPS — backend jurilux-api (une seule fois)

Complète `RUNBOOK_VPS.md` de jurilux-web (front déjà en place). À exécuter sur le VPS OVH (`51.178.16.135`).

## 1. Docker

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
```

## 2. Dossiers

```bash
sudo mkdir -p /opt/jurilux-api /data/pdfs /data/laws
sudo chown -R deploy:deploy /opt/jurilux-api
sudo chown -R deploy:www-data /data/pdfs /data/laws
```

`/data/pdfs` = PDFs de jurisprudence servis par Caddy sur `/docs/*` (le bloc Caddy existe déjà).

## 3. Secrets (.env, jamais dans git)

```bash
sudo -u deploy tee /opt/jurilux-api/.env >/dev/null <<'ENV'
MEILI_MASTER_KEY=<openssl rand -hex 24>
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-haiku-4-5
PROMPT_VERSION=rebuild-2026-07-v1
ENV
sudo chmod 600 /opt/jurilux-api/.env
```

## 4. Sudo pour le déploiement

Le user `deploy` a déjà sudo NOPASSWD global (cf. état actuel). Sinon, à minima :

```bash
echo 'deploy ALL=(root) NOPASSWD: /usr/bin/docker' | sudo tee /etc/sudoers.d/jurilux-api-deploy
sudo chmod 440 /etc/sudoers.d/jurilux-api-deploy
```

## 5. Secrets GitHub (repo jurilux-api)

| Secret | Valeur |
|---|---|
| `DEPLOY_HOST` | `51.178.16.135` |
| `DEPLOY_SSH_KEY` | même clé privée que jurilux-web |

(`DEPLOY_USER`/`DEPLOY_PATH` inutiles : user `deploy` et `/opt/jurilux-api` sont dans le workflow.)

## 6. Premier déploiement

`git push` sur `main` (ou Actions → Deploy API → Run). Puis :

```bash
sudo docker compose -f /opt/jurilux-api/docker-compose.yml ps
curl -s 127.0.0.1:8088/health          # {"status":"ok",...} attendu
curl -s 127.0.0.1:7700/health          # {"status":"available"}
```

## 7. Corpus

```bash
cd /opt/jurilux-api
# démo (validation bout en bout, avant le vrai corpus) :
sudo docker compose exec api python -m ingest.seed_demo
# vrai corpus, une fois les PDFs dans /data/pdfs :
# monter /data dans le conteneur (décommenter le volume) ou lancer en venv sur l'hôte :
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -a; . ./.env; set +a
.venv/bin/python -m ingest.index_pdfs /data/pdfs
.venv/bin/python -m ingest.index_pdfs /data/laws --source-type law
```

## 8. Vérification via Caddy (avant bascule DNS)

```bash
curl -s http://127.0.0.1/health
curl -s -X POST http://127.0.0.1/api/ask -H 'Content-Type: application/json' \
  -d '{"q":"licenciement faute grave","topK":3,"temperature":0}' | head -c 300
```

Front http://51.178.16.135/ : le voyant doit passer à « Connecté » (health strict).

## 9. Versions & rollback

Chaque release est taggée en git (`vMAJEUR.MINEUR.PATCH`, ex. `v1.0.0`). Le CI :
- lance `pytest` (gate) **avant** tout déploiement (`push main` échoue si un test casse) ;
- garde l'image Docker courante sous `jurilux-api-api:previous` avant chaque rebuild ;
- écrit la version déployée dans `/opt/jurilux-api/.deployed_ref`.

**Rollback rapide** (dernier déploiement cassé, sans rebuild) :
```bash
ssh ubuntu@51.178.16.135 'sudo /opt/jurilux-api/rollback.sh'
```

**Rollback vers une version précise** (reconstruit exactement le tag) :
GitHub → repo `jurilux-api` → Actions → **Deploy API (VPS OVH)** → *Run workflow* →
champ `ref` = le tag voulu (ex. `v1.0.0`).

**Tagger une nouvelle version** (après merge sur `main`) :
```bash
git tag -a v1.1.0 -m "Description" && git push origin v1.1.0
```

## Dépannage

- `sudo docker compose logs -f api` / `logs -f meilisearch`
- Réindexation totale : supprimer l'index (`curl -X DELETE 'http://127.0.0.1:7700/indexes/chunks' -H "Authorization: Bearer $MEILI_MASTER_KEY"`) puis relancer l'ingestion.
- Le workflow `diagnose.yml` de jurilux-web dump l'état backend à distance.

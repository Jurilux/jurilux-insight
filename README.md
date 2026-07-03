# jurilux-api

Backend RAG de Jurilux : FastAPI (127.0.0.1:8088) + Meilisearch (127.0.0.1:7700) + API Claude.
Respecte le contrat du front `jurilux-web/src/api.ts` (`POST /api/ask`, `GET /health`).

## Architecture

```
Caddy ── /api/*, /health ──> FastAPI :8088 ──> Meilisearch :7700 (index "chunks")
      └─ /docs/*  ─────────> /data/pdfs (PDFs jurisprudence, statique)   └─> API Anthropic
```

- `/health` est **strict** : 503 si Meilisearch est down ou si la clé API manque
  → le voyant du front passe correctement à « Indisponible ».
- `/api/ask` : recherche Meilisearch (filtres `year_min`/`year_max`/`juridiction_key`),
  puis génération Claude en JSON structuré (answer, used_doc_ids, refused, feedback).
- Citations : `doc_id` de jurisprudence → le front sert `/docs/<doc_id>.pdf` ;
  textes de loi → `pdf_url` absolue Legilux.

## Dev local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # renseigner ANTHROPIC_API_KEY + MEILI_MASTER_KEY
docker compose up -d meilisearch
python -m ingest.seed_demo      # 3 chunks de démo
uvicorn app.main:app --port 8088 --reload
curl -s localhost:8088/health
curl -s -X POST localhost:8088/api/ask -H 'Content-Type: application/json' \
  -d '{"q":"licenciement faute grave","topK":5,"temperature":0}'
```

Tests : `pip install pytest httpx && pytest` (aucun service externe requis).

## Ingestion du corpus

Voir `ingest/` :

1. `seed_demo.py` — valider la chaîne de bout en bout.
2. `index_pdfs.py` — indexer un dossier de PDFs (jurisprudence dans `/data/pdfs`,
   lois avec `--source-type law`). Métadonnées déduites du nom de fichier,
   surchargées par `metadata.jsonl`.
3. `fetch_legilux.py` — retélécharger les textes Legilux depuis une liste d'URLs ELI.

Cible historique : ~311k chunks (jurisprudence CSJ/Cassation + Legilux).
Les sources d'origine sont perdues — reconstruction incrémentale.

## Déploiement

`git push` sur `main` → `.github/workflows/deploy-api.yml` → rsync vers
`/opt/jurilux-api` + `docker compose up -d --build`. Préparation du VPS
(une fois) : voir `RUNBOOK_API.md`.

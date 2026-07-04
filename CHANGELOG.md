# Changelog

Format : [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ·
Versionnage : [SemVer](https://semver.org/lang/fr/). Chaque version = un tag git
(rollback via Actions `Deploy API` avec `ref=<tag>`, voir `RUNBOOK_API.md` §9).

## [Non publié]

## [1.1.0] — 2026-07-04

Durcissement & préparation bêta (roadmap V1.1).

### Ajouté
- Rate-limit par IP sur `/api/ask` (`RATE_LIMIT_PER_MIN`, 20/min par défaut) — garde-fou
  coût LLM, refus gracieux préservant le contrat `AskResponse`.
- `GET /api/corpus` : périmètre du corpus (décisions/textes + chunks + année récente).
- `refresh_corpus.sh` : ingestion incrémentale (années récentes) + mise à jour du périmètre.
- `COMPLIANCE.md` : cadrage licéité / RGPD (gate n°1) pour juriste / CNPD.

### Corrigé
- `/api/corpus` : total de chunks exact (somme des facettes) et année récente plafonnée.

## [1.0.0] — 2026-07-04

Première version en production du backend RAG (FastAPI + Meilisearch + Claude).

### Ajouté
- API `/health` et `/api/ask` (contrat `AskRequest`/`AskResponse`), réponses sourcées.
- Déploiement continu sur le VPS OVH (`docker compose`), gate `pytest` avant déploiement.
- Ingestion lois Legilux (`fetch_legilux.py`, `legilux_codes.txt`) — 10 codes consolidés.
- Ingestion jurisprudence open-data data.public.lu (`fetch_jurisprudence.py`) — 96 datasets
  (Cassation, chambres CSJ, tribunaux d'arrondissement, justices de paix), ~49,5k décisions.
- Corpus indexé : ~1,24 M chunks (lois + jurisprudence).
- Sauvegardes automatisées (`backup.sh` + cron) : dump Meilisearch + archive `/data/laws`.
- Rollback : image `:previous` + `rollback.sh` + déploiement d'un tag via Actions.
- Dépendances épinglées (`requirements.txt`) pour des rebuilds reproductibles.

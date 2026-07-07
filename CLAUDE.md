# CLAUDE.md — jurilux-api

Backend RAG de Jurilux (assistant juridique, droit luxembourgeois). Code et
commentaires en **français** : garder cette langue.

> ⚠️ **Avant de coder, lire ce fichier ET vérifier `origin/main`** (`git fetch`) :
> `main` est la **source de vérité** et évolue (tags `vMAJEUR.MINEUR.PATCH`). Ne pas
> forker d'un vieux point : partir de `origin/main`.

## Architecture (état réel — respecter ces choix)

```
Caddy (VPS OVH, UE)
  ├─ /api/*, /health ──> FastAPI 127.0.0.1:8088 (app/main.py, uvicorn)
  │                        ├─> Meilisearch 127.0.0.1:7700  index "chunks"
  │                        ├─> Ollama (embeddings BGE-M3)   recherche hybride/sémantique
  │                        └─> API Anthropic (Claude)       génération RAG
  └─ /docs/*         ──> /data/pdfs (PDFs de jurisprudence, statique)
```

**Conventions structurelles NON NÉGOCIABLES (elles définissent le projet) :**
- **Routes déclarées inline dans `app/main.py`** (`@app.get/post/...`), pas d'`APIRouter`,
  pas de dossier `app/routers/`. Les modules `app/*.py` sont des **stores/helpers** plats
  (`auth`, `db`, `admin`, `alert`, `alert_runner`, `feedback`, `share`, `workspace`,
  `insight`, `insight_build`, `search`, `rag`, `metrics`).
- **Persistance = sqlite3 BRUT (stdlib)** via `app/db.py` (`get_conn()`, `SCHEMA`,
  `init_db()`). **Pas de SQLAlchemy, pas d'ORM.** Les « users » circulent en `dict`
  (`user["id"]`, `user["email"]`, `user["plan"]`, `user["is_admin"]`).
- **Auth = pbkdf2 (hashlib/stdlib) + tokens de session opaques** stockés hachés en
  table `sessions` (`app/auth.py`). **Pas de JWT, pas de bcrypt.** En-tête
  `Authorization: Bearer <token>`. Dans `main.py` : `_require_user` / `_require_admin`.
- **Dépendances minimales** (`requirements.txt` en `==`) : fastapi, uvicorn, pydantic,
  meilisearch, anthropic, pypdf, cryptography. Ne pas en ajouter sans nécessité réelle.
- Flux `/api/ask` : `insight.lawyer_lookup` (court-circuit si avocat nommé) →
  `search.search` → `rag.answer` → `AskResponse`. Toute panne = **refus gracieux**
  (`rag.refusal`), jamais de 500.

### Recherche (`app/search.py`)
- **Index `chunks`** : 1 doc = 1 chunk. PK `chunk_id`. Filterable `year`,
  `juridiction_key`, `source_type` (`"jurisprudence"|"law"|"projet_loi"`). Searchable
  `text`, `title`.
- **Recherche FÉDÉRÉE** : le corpus est ~92 % jurisprudence ; une recherche simple ne
  remonte jamais de lois. On interroge jurisprudence et lois séparément puis on
  **entrelace 1:1** pour garantir les textes de loi dans le contexte. Les projets de loi
  n'entrent que sur filtre `source_type` explicite.
- **Hybride sémantique** si `HYBRID_SEMANTIC_RATIO > 0` : embeddings (Ollama BGE-M3, ou
  OpenAI si `OPENAI_API_KEY` pour embedder la requête une fois). 0 = mots-clés seuls.
- `corpus_overview()` (→ `/api/corpus`), `index_stats()` (backoffice).

### SSO entreprise & air-gap
- **SSO OIDC** (`app/oidc.py`, `/api/auth/oidc/{enabled,login,callback}`) : optionnel, actif si
  `OIDC_ISSUER`/`OIDC_CLIENT_ID`/`OIDC_REDIRECT_URI` configurés. Flux Authorization Code
  (découverte `.well-known`, `urllib`), crée/lie le compte (`auth.ensure_user`) et émet un
  jeton de session pbkdf2. Vide = login mot de passe seul.
- **Air-gap / chiffrement au repos** : `deploy/AIRGAP.md` (router le LLM en `local` via Ollama,
  couper l'egress ; volume LUKS + backups chiffrés). Le fournisseur `local` du routeur rend
  la souveraineté effective *par construction*.

### Routeur de modèle (`app/llm.py`)
- **Souveraineté par construction** : le fournisseur LLM est choisi selon la **sensibilité**
  de la requête — `public` (corpus jurisprudence/Legilux) → `LLM_PROVIDER_PUBLIC` ;
  `confidentiel` (documents privés du Vault) → `LLM_PROVIDER_CONFIDENTIAL`. Le Vault route
  donc en « confidentiel ».
- Fournisseurs : `anthropic` (Claude, SDK), `mistral` (API UE, `urllib`), `local` (Ollama
  air-gap, `urllib`). **Par défaut tout sur `anthropic`** → comportement historique inchangé.
  `rag.answer` appelle `llm.generer(...)` ; le streaming reste Anthropic (repli non-streamé
  si le fournisseur public est Mistral/local). Panne = `RuntimeError` → refus gracieux.
- Visibilité backoffice : `GET /api/admin/llm` (`llm.info()`).

### RAG (`app/rag.py`)
- **Biais anti-refus (prompt v2)** : privilégier une réponse partielle (`status="partial"`)
  à un refus ; ne refuser que si hors droit LU ou aucun extrait pertinent. Ne pas relâcher.
- **Questions méta** (sur Jurilux/le corpus) répondues via un bloc « À PROPOS ».
- **Contexte conversationnel** : `history` (tours précédents) enrichit la requête de
  recherche (`_contextual_query`) et le prompt.
- **`suggested_question`** (rebond 1 clic, autre angle) + **`follow_ups`** (parcours guidé :
  série ordonnée de questions de suivi qui, enchaînées, mènent à une réponse complète) +
  **mode pédagogique** (plan étudiant).
- **Streaming** (`answer_stream` / `/api/ask/stream`) : réponse en markdown puis
  délimiteur `§§§META§§§` + JSON compact. `refusal()` pour les refus gracieux.

## Espace utilisateur (`app/db.py`, `app/auth.py`)
- **SQLite** (`DB_PATH`, volume Docker `app_data`). Tables : `users`, `sessions`,
  `history`, `feedback`, `shares`, `workspaces`, `workspace_members`, `dossiers`,
  `dossier_items`, `alerts`, `alert_hits`, `insight_appearances`, `vault_documents`.
- **Quota plan étudiant** freemium **mensuel** (`STUDENT_MONTHLY_QUOTA`, `quota_info`) ;
  plan `pro` = illimité. `/api/ask` refuse gracieusement au-delà du quota.
- **Admin** : `is_admin` en base OU e-mail dans `ADMIN_EMAILS` (amorce du 1er admin).

## Contrat d'API — NE JAMAIS CASSER
Le front `jurilux-web/src/api.ts` dépend des chemins et formes exactes. Ajouts
optionnels rétrocompatibles uniquement.
- Auth : `POST /api/auth/{register,login}` renvoient **`{token, user:{email}}`**
  (champ **`token`**, pas `access_token`) ; `/api/auth/{logout,change-password}`.
- `GET /api/me` → `{user:{email,plan,is_admin}, quota}` · `GET /api/history` → `{items}`.
- `AskRequest` : `q`, `topK`, `temperature`, `filters` (`year_min/max`,
  `juridiction_key`, `source_type`), `pedagogical`, `history` (`Turn{role,content}`).
- `AskResponse` : `answer`, `citations[]`, `refused`, `status`, `feedback`,
  `suggested_question` (autre angle), `follow_ups` (parcours guidé : liste ordonnée de
  questions de suivi, ajout optionnel rétrocompatible), `prompt_version`.
- `Citation.source_type` : `"jurisprudence"|"law"|"projet_loi"`.
- Verrouillé par `tests/test_api.py` (gate CI avant déploiement).

## Sous-systèmes (modules plats, routes dans `main.py`)
- **Feedback** (`feedback.py`, `/api/feedback`) · **Permaliens** (`share.py`,
  `/api/share` + GET public).
- **Cabinet** (`workspace.py`) : `/api/workspaces`, `/api/dossiers` — rôles
  owner/admin/member (`_require_ws_role`).
- **Veille** (`alert.py` + `alert_runner.py`) : `/api/alerts` (+ check/hits) ; le check
  automatique tourne aussi au **cron d'ingestion** (nouvelle jurisprudence sur mes sujets).
- **Backoffice** (`admin.py`, `/api/admin/*`, gate `is_admin`) : overview, users, plan/
  admin, questions, feedback, activité par jour, **probe** (inspecteur retrieval),
  **eval** (banc de 10 questions de référence, sans LLM).
- **Insight avocats** (`insight.py` + `insight_build.py`, `/api/insight/*`, **PUBLIC**).

### Insight avocats — spécificités
- Profiling **des AVOCATS uniquement** (« Maître X »), données **publiques** de
  jurisprudence. **JAMAIS de magistrats/greffiers** (zone RGPD/CNPD la plus sensible) —
  règle produit à conserver.
- Extraction **locale et déterministe** (regex/heuristiques, aucun appel LLM) :
  `insight_build.py` (re)construit la table `insight_appearances` **à chaque refresh du
  corpus** (cron). Côté (A/B), issue estimée gagné/perdu (indicatif), matière, réseau de
  confrères. Tant que le build n'a pas tourné, les endpoints renvoient des ensembles vides.
- `insight.lawyer_lookup(q)` **court-circuite le RAG** dans `/api/ask` : une recherche
  nominative (« décisions de Maître X ») renvoie directement le profil + décisions.

### Vault — documents privés (`app/vault.py`)
- L'utilisateur dépose ses propres documents (PDF/texte) et les interroge. Métadonnées
  en SQLite (`vault_documents`, isolées par `owner_id`) ; chunks dans l'index Meili
  **`vault_chunks`** (filtre `owner_id` = **isolation stricte**, créé paresseusement).
- `POST /api/vault/documents` (upload, corps brut + `?filename=`), `GET`/`DELETE`,
  `POST /api/vault/ask` (Q&A sourcé isolé ; `include_corpus:true` = **RAG hybride** privé +
  corpus public officiel en une requête, les citations sans `source_type` = « votre
  document »), `POST /api/vault/documents/{id}/analyze` : `task:citations` = **vérificateur
  de citations ancré au corpus** ; `task:extract` = **extraction structurée** via `insight`
  (les deux locales/déterministes, aucun LLM) ; `task:summary` = **résumé** fidèle (LLM) ;
  `task:counter` = **contre-argumentaire sourcé** ancré à la jurisprudence LU réelle,
  citations vérifiables (LLM + corpus). Les tâches LLM routent en « confidentiel ».
- **Analytics contentieux** (`insight.analytics` → `GET /api/insight/analytics`, public) :
  volumes + taux de succès estimé par matière/juridiction/année (avocats/parties, pas de
  magistrats ; montants = extension future d'`insight_build`).

### Socle entreprise / on-prem + rédaction
- **Journal d'audit** (`audit.py`, table `audit_log`) : trace locale qui/quoi/quand
  (login, mutations admin, upload/suppression Vault…). `GET /api/admin/audit`, purge via
  `POST /api/admin/purge` (rétention). Écriture best-effort (ne casse jamais l'action).
- **Clés d'API de service** (`apikeys.py`, table `api_keys`) : jeton haché (jamais en clair),
  en-tête `X-API-Key` (accepté sur `/api/ask`). `POST`/`GET`/`DELETE /api/keys`.
- **Export RGPD** (`rgpd.py`) : `GET /api/me/export` (portabilité) ; `rgpd.purge(days)`.
- **Bibliothèque de prompts** (`prompts.py`, table `prompts`) : perso ou partagée au cabinet
  (`workspace_id`). `POST`/`GET`/`DELETE /api/prompts`.
- **Rédaction assistée** (`rag.rediger`, `POST /api/draft`) : document sourcé sur le corpus.
- **Vault** : `POST /api/vault/review` (revue tabulaire, 1 doc = 1 ligne) ;
  `analyze task=timeline` (chronologie déterministe).
- **Cloisons déontologiques** (`workspace.py`, table `dossier_access`, col `dossiers.restricted`) :
  dossier restreint = invisible (404) hors owner/admin + autorisés nommément
  (`/api/dossiers/{id}/restrict`, `/access`, `/access/{uid}`).
- **Observabilité** : `GET /api/admin/health` (dépendances + volumétrie + routage LLM).
- **Paramétrage runtime** (`config_store.py`, table `app_config`) : `GET`/`PATCH /api/admin/config`
  — réglages NON secrets appliqués à `settings` sans redéploiement (liste blanche ; secrets restent en `.env`).
- **Sauvegarde/restauration** : `scripts/backup.sh` (SQLite `.backup` + dump Meili) / `restore.sh`.
- **Revue de contrats** (`playbooks.py`, table `playbooks`) : playbooks de règles (perso/cabinet,
  `/api/playbooks`) appliqués à un contrat Vault via `POST /api/vault/documents/{id}/review-contract`
  → verdict par règle (`ok`/`issue`/`missing`) ancré au texte (`rag.revue_contrat`, LLM confidentiel).

## Conventions
- **`doc_id` jurisprudence = nom du PDF sans `.pdf`** → le front sert `/docs/<doc_id>.pdf`
  (Caddy depuis `/data/pdfs`). Ne pas renommer après indexation. Les lois
  (`source_type="law"`) utilisent un `pdf_url` **absolu** Legilux.
- **`/health` strict** : **503** si Meilisearch down **ou** `ANTHROPIC_API_KEY` manquante
  (le front n'affiche « Connecté » que sur `res.ok`). Verrouillé par test.
- **Versions épinglées** (`requirements.txt` `==`, Meilisearch `v1.15`) → rebuilds
  reproductibles. **Rollback par tags** git `vX.Y.Z` (Actions *Deploy API* `ref=<tag>`,
  ou `rollback.sh` sur le VPS). Cf. `RUNBOOK_API.md` §9, `CHANGELOG.md`.
- **Config par env** (`app/config.py`, `.env` jamais commité) : `MEILI_*`, `ANTHROPIC_*`,
  `OPENAI_API_KEY`, `PROMPT_VERSION`, `HYBRID_SEMANTIC_RATIO`, `RATE_LIMIT_PER_MIN`,
  `DB_PATH`, `SESSION_DAYS`, `STUDENT_MONTHLY_QUOTA`, `ADMIN_EMAILS`. `.env.example` = modèle.
- **Refus > invention** : le prompt interdit d'inventer du droit (enjeu conformité,
  cf. `COMPLIANCE.md`).

## Déploiement (`.github/workflows/deploy-api.yml`)
- SSH via **alias `deploytarget`** + user `deploy` **codé en dur** (délibéré : contourne
  deux bugs de quoting résolus). Ne pas « simplifier » en `ssh user@host`. Secrets requis :
  `DEPLOY_HOST`, `DEPLOY_SSH_KEY`. Prérequis VPS : `/opt/jurilux-api/.env` présent.
- **Gate `pytest`** avant `deploy`. La PR ne déploie pas. Image courante retaggée
  `:previous` avant rebuild (rollback instantané).
- `docker-compose.yml` : services `meilisearch` (+ `ulimit nofile` élevé pour le reindex),
  `ollama` (embeddings sémantiques, souverain), `api`. `rsync` exclut `.env` et `data/`.

## Dev local & tests
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # ANTHROPIC_API_KEY + MEILI_MASTER_KEY
docker compose up -d meilisearch
python -m ingest.seed_demo
uvicorn app.main:app --port 8088 --reload
```
Tests : `pip install pytest httpx && pytest -q` — **aucun service externe requis**
(Meilisearch/Anthropic monkeypatchés dans `tests/`). Espace utilisateur : fixture
`temp_db` (monkeypatch `settings.db_path` → SQLite jetable). Toujours lancer `pytest`
avant de pousser : gate CI.

**Moteur de tests fonctionnels** (`functional/`, `python -m functional.run`) : deux niveaux —
(1) **parcours utilisateur** réalistes (objectif + séquence d'étapes enchaînées à état
partagé, multi-acteurs : `parcours.py`) ; (2) **matrice d'autorisation** endpoint × profil.
Catalogue **par domaine** dans `functional/scenarios/` (service/auth/ask/feedback_partage/
insight/cabinet/veille/vault/admin), ~470 assertions couvrant succès ET branches d'erreur
(401/403/404/422/413, refus gracieux) via des **stubs par scénario**. Exécutés **par profil**
(anonyme/étudiant/pro/admin + rôles d'espace + isolation Vault), avec **injection de données**
(corpus/insight/docs stubés). Passerelle CI : `tests/test_functional.py`. Cf.
`functional/README.md`.

## Ingestion du corpus
`refresh_corpus.sh` (cron mensuel, nuit) : re-fetch jurisprudence (`fetch_jurisprudence.py`)
+ Legilux consolidé et projets de loi (`fetch_legilux_full.py`), ré-indexation idempotente
(`index_pdfs.py`, `index_chd.py`), **rebuild de l'index insight avocats** (`insight_build.py`),
maj `corpus_meta`. Métadonnées déduites du nom de fichier, surchargeables par `metadata.jsonl`.

## Leçons apprises (à ne pas rejouer)
Erreur commise sur ce projet, gardée en mémoire ici pour ne pas se répéter :

- **Divergence par fork périmé.** Une session a forké d'un vieux point (`v1.1`) et
  **ré-implémenté tout le produit en parallèle** avec une autre architecture (SQLAlchemy/
  JWT/`app/routers/`) alors que `main` avait avancé (`v1.3.2`, sqlite3 brut / pbkdf2 /
  routes inline). Résultat : deux codebases incompatibles, un `CLAUDE.md` qui décrivait
  l'archi *fantasmée* et non le code réel. **La source de vérité, c'est `origin/main`**,
  pas une note ou un souvenir de session.
- **Réflexes pour l'éviter** :
  1. **Toujours `git fetch origin main` d'abord** et repartir de là (le bandeau en tête de
     ce fichier). Vérifier `git log origin/main` et les **tags** avant de coder.
  2. **Épouser l'architecture existante, ne pas la « moderniser »** : ici sqlite3 brut,
     pas d'ORM ; pbkdf2 + sessions, pas de JWT ; routes inline, pas de routers ; deps
     minimales. Un refactor d'archi se **décide**, il ne se glisse pas dans une feature.
  3. **Ce `CLAUDE.md` documente le code réel, pas une cible.** S'ils divergent, le code
     gagne — corriger le doc, pas inventer le code pour coller au doc.
  4. **Petits diffs vérifiables** sur base à jour > grande réécriture parallèle.

## Docs de référence
`README.md` · `RUNBOOK_API.md` (setup VPS + rollback) · `COMPLIANCE.md` (licéité/RGPD) ·
`CHANGELOG.md` (versions/tags).

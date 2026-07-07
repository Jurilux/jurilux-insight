# CLAUDE.md — jurilux-insight

**Intelligence contentieux B2B** pour le droit luxembourgeois. Produit orienté
**dashboard analytics** dont le cœur est le **profilage d'avocats** à partir de données
**publiques** de jurisprudence. Code et commentaires en **français** : garder cette langue.

> ⚠️ **Fork de `jurilux-api`** (produit RAG « assistant juridique »). Ici le centre de gravité
> est INVERSÉ : l'analytics contentieux devient la surface principale, le Q&A RAG passe en
> accessoire. Ne PAS re-fusionner naïvement avec `jurilux-api` : ce sont deux produits.
> `origin/main` de CE dépôt est la source de vérité.

## Ce qui change par rapport à jurilux-api (et ce qui NE change PAS)

**Hérité tel quel (ne pas « moderniser »)** — mêmes conventions structurelles non négociables :
- **Routes inline dans `app/main.py`** (`@app.get/post`), pas d'`APIRouter`, pas de `app/routers/`.
  Les modules `app/*.py` sont des **stores/helpers** plats.
- **Persistance = sqlite3 BRUT (stdlib)** via `app/db.py` (`get_conn()`, `SCHEMA`, `init_db()`).
  **Pas de SQLAlchemy, pas d'ORM.** Users en `dict`.
- **Auth = pbkdf2 (hashlib/stdlib) + tokens de session opaques** hachés en table `sessions`
  (`app/auth.py`). **Pas de JWT, pas de bcrypt.** En-tête `Authorization: Bearer <token>`.
- **Dépendances minimales** (`requirements.txt` en `==`). Ne pas ajouter sans nécessité réelle.

**Réorienté (le produit) :**
- Le **dashboard analytics** (`app/insight.py`) est la surface principale. L'accueil = tableaux de
  bord : volumétrie, taux de succès ESTIMÉ par matière / juridiction / année, **benchmark
  d'avocats**, **export CSV**.
- Le **RAG** (`app/rag.py`, `/api/ask`) est conservé mais **secondaire** : sourcer une décision
  citée dans une fiche, répondre à une question ponctuelle. Ne pas le supprimer (il alimente
  aussi `lawyer_lookup`), mais il n'est plus la porte d'entrée.

## Cœur produit — Insight / analytics contentieux (`app/insight.py`)

Extraction **locale et déterministe** (regex/heuristiques, **aucun appel LLM**) depuis la
jurisprudence publique. `insight_build.py` (re)construit la table `insight_appearances` à chaque
refresh du corpus (cron). Tant que le build n'a pas tourné, les endpoints renvoient des ensembles
vides.

**Règle produit NON NÉGOCIABLE (RGPD/CNPD) :** profilage des **AVOCATS uniquement** (« Maître X »)
et des parties. **JAMAIS de magistrats ni de greffiers.** Taux de succès toujours présentés comme
**estimés / indicatifs**, jamais comme certains (heuristique sur le dispositif).

### Endpoints analytics (`/api/insight/*`, PUBLIC)
- `GET /api/insight/overview` — **KPIs d'en-tête du dashboard** : volumétrie globale (avocats,
  décisions, estimables, taux global), période couverte, top matières & juridictions (`overview()`).
- `GET /api/insight/analytics?matter&juridiction` — volumes + taux de succès estimé par matière /
  juridiction / année (`analytics()`).
- `GET /api/insight/lawyers?q&limit&sort&matter` — liste/recherche d'avocats, tri `cases|recent|winrate`
  (`list_lawyers()`).
- `GET /api/insight/lawyers/{key}` — fiche profil : décisions, côté (A/B), gagné/perdu estimé,
  matières, **réseau de confrères** (`get_lawyer()` + `_cocounsel()`).
- `GET /api/insight/compare?keys=k1,k2,...` — **benchmark côte à côte** de 2 à 6 avocats (`compare()`).
- `GET /api/insight/export/lawyers.csv?q&limit&sort&matter` — **export CSV** (tableur cabinet/assureur)
  (`export_lawyers_csv()`).
- `GET /api/insight/matters`, `GET /api/insight/stats` — filtres & volumétrie.
- `insight.lawyer_lookup(q)` **court-circuite le RAG** dans `/api/ask` : une question nominative
  renvoie directement le profil + décisions.

## Contrat d'API — additions rétrocompatibles uniquement
Les formes existantes de `jurilux-api` restent valables (auth, `/api/me`, `/api/ask`, insight…).
Les nouveautés B2B (`overview`, `compare`, `export/lawyers.csv`) sont des **ajouts** : ne pas casser
les chemins/formes existants. Verrouillé par `tests/` (gate CI).

## Espace utilisateur, sous-systèmes hérités
Inchangés depuis `jurilux-api` : `users/sessions/history/feedback/shares/workspaces/dossiers/
alerts/insight_appearances/vault_documents`, quota plan étudiant, admin (`is_admin` ou
`ADMIN_EMAILS`), Vault (isolation `owner_id`), veille, cabinet, backoffice, audit, clés d'API,
RGPD, prompts, playbooks, routeur de modèle (`app/llm.py`, souveraineté par construction). Voir
l'historique git hérité. **Ne pas régresser** ces sous-systèmes en réorientant le produit.

## Dev local & tests
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
docker compose up -d meilisearch
python -m ingest.seed_demo
uvicorn app.main:app --port 8088 --reload
```
Tests : `pip install pytest httpx && pytest -q` — **aucun service externe requis**
(Meilisearch/Anthropic/Ollama monkeypatchés). Fixture `temp_db` (SQLite jetable). Moteur
fonctionnel : `functional/` (`python -m functional.run`). **Toujours `pytest` avant de pousser.**

## Leçons héritées (à ne pas rejouer)
- **Divergence par fork périmé.** Ce dépôt EST un fork assumé — mais le piège reste le même :
  ne pas ré-implémenter en parallèle avec une autre archi. On garde sqlite3 brut / pbkdf2 /
  routes inline / deps minimales. Un refactor d'archi se **décide**, il ne se glisse pas.
- **Ce `CLAUDE.md` documente le code réel, pas une cible.** S'ils divergent, le code gagne —
  corriger le doc.
- **Petits diffs vérifiables sur base à jour** > grande réécriture parallèle.

## Docs de référence
`README.md` · `RUNBOOK_API.md` · `COMPLIANCE.md` (licéité/RGPD — profilage avocats) · `CHANGELOG.md`.

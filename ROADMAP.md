# ROADMAP — Jurilux

Feuille de route produit, alignée sur `POSITIONING.md` (cible : cabinets d'avocats LU
petits/moyens ; flanc souverain ; différenciateurs vérifiabilité + insight + air-gap).
Deux chantiers : **A. socle « entreprise / on-prem »** (table stakes d'un déploiement en
cabinet) et **B. fonctionnalités concurrentes** encore absentes.

Légende effort : 🟢 petit · 🟡 moyen · 🔴 gros. « code » = faisable dans l'archi de `main`
(stdlib/sqlite/FastAPI) ; « ops » = déploiement/infra.

---

## A. Socle « entreprise / on-prem » (générique, attendu de tout logiciel déployé)

Ce qu'un cabinet exige pour héberger un logiciel juridique chez lui. Beaucoup **servent
directement le Pilier 2** (souveraineté par construction).

| # | Fonctionnalité | Pourquoi (on-prem / conformité) | Effort | État |
|---|---|---|---|---|
| A1 | **Journal d'audit** (qui/quoi/quand, local, exportable, immuable) | Secret pro, déontologie, RGPD ; « qui a consulté quoi ». Répond à la question ouverte de rétention (`COMPLIANCE.md §4). | 🟡 code | ✅ (`app/audit.py`) |
| A2 | **Routeur de modèle par sensibilité** (Claude / **Mistral UE** / local) | Comble le trou souverain (Alizé a déjà Mistral ; aligné AI4LUX). **Prérequis du discours souverain.** | 🟡 code | ✅ (`app/llm.py`) |
| A3 | **Rétention & purge configurables** (historique, logs, feedback, audit) | RGPD (minimisation) ; politique par cabinet. | 🟡 code | ✅ (`/api/admin/purge`) |
| A4 | **Export / portabilité des données** (par utilisateur) | RGPD (portabilité) ; réversibilité. | 🟡 code | ✅ (`/api/me/export`) |
| A5 | **SSO entreprise** (OIDC) | Annuaire du cabinet (Keycloak/Azure AD/Google). | 🟡 code | ✅ (`app/oidc.py`, `/api/auth/oidc/*`) |
| A6 | **Clés d'API / jetons de service** | Intégrations, scripts cabinet, automatisations. | 🟢 code | ✅ (`app/apikeys.py`, X-API-Key) |
| A7 | **Sauvegarde & restauration** (SQLite + dump Meili) | Continuité d'activité. | 🟡 ops | ✅ (`scripts/backup.sh`, `restore.sh`) |
| A8 | **Cloisons déontologiques** (ethical walls) | Conflits d'intérêts : dossier restreint = visible des seuls autorisés. | 🟡 code | ✅ (`/api/dossiers/{id}/restrict`) |
| A9 | **Mode hors-ligne / air-gap** (LLM local, zéro appel externe) | Différenciateur ultime vs Alizé (SaaS). | 🔴 code+ops | ✅ (routeur `local` + `deploy/AIRGAP.md`) |
| A10 | **Observabilité** (santé détaillée + volumétrie + routage LLM) | Exploitation par l'IT du cabinet. | 🟢 code | ✅ (`/api/admin/health`) |
| A11 | **Chiffrement au repos** (volume LUKS + backups chiffrés) | Secret pro. | 🟡 ops | ✅ recette (`deploy/AIRGAP.md`) |
| A12 | **Paramétrage runtime** (réglages non-secrets sans redéploiement) | Confort d'exploitation. | 🟡 code | ✅ (`/api/admin/config`) |

**Priorité socle (valeur × fit positionnement) : A1 (audit) + A2 (routeur modèle)** — les
deux rendent la promesse souveraine *vérifiable*, et sont 100 % faisables dans l'archi de
`main` sans service externe.

---

## B. Fonctionnalités concurrentes encore absentes

Reprises de la veille (Harvey, Lexis+ Protégé, Legora, CoCounsel, vLex, Spellbook, Alizé).
Priorisées par fit avec la cible (cabinets LU) et réutilisation de nos atouts (corpus
officiel + insight).

| # | Fonctionnalité | Qui la fait | Réutilise | Effort | État |
|---|---|---|---|---|---|
| B1 | **Résumé de document / décision** | Lexis, Harvey | RAG | 🟢 code | ✅ (Vault `task=summary`) |
| B2 | **Contre-argumentaire sourcé** sur la jurisprudence LU (Vault #5) | Lexis (Protégé) | RAG + corpus | 🟡 code | ✅ (Vault `task=counter`) |
| B3 | **Analytics contentieux** (taux par juridiction/matière ; montants = à venir) | Lex Machina, DataJust | **insight** | 🟡 code | ✅ (`/api/insight/analytics`) |
| B4 | **Revue tabulaire multi-documents** (1 doc = ligne, colonnes extraites) | Legora, Harvey | Vault | 🟡 code | ✅ (`/api/vault/review`) |
| B5 | **Chronologie / timeline** (dates + contexte, déterministe) | Lexis | Vault + regex | 🟡 code | ✅ (Vault `task=timeline`) |
| B6 | **Rédaction assistée / drafting sourcé** (conclusions, courriers) | Harvey, Legora, Lexis | RAG | 🔴 code | ✅ (`/api/draft`) |
| B7 | **Bibliothèque de prompts / skills** partagée au cabinet | Legora, CoCounsel | — | 🟢 code | ✅ (`app/prompts.py`) |
| B8 | **Comparatif multi-juridiction** (LU / BE / FR) | vLex | corpus (à étendre) | 🔴 corpus+code | ❌ |
| B9 | **Revue de contrats + playbooks** (verdict par règle ; redline = à venir) | Spellbook, Luminance | Vault | 🟡 code | ✅ (`/api/vault/.../review-contract`) |
| B10 | **Intégration Word / M365 / DMS** | Harvey, Spellbook | — | 🔴 intégration | ❌ |
| B11 | **Workflows / agents no-code** | Harvey, Legora | — | 🔴 code | ❌ |

**Priorité produit (quick wins à fort fit) : B1 (résumé), B2 (contre-argumentaire),
B3 (analytics contentieux)** — tous réutilisent le RAG/corpus/insight existants, renforcent
les Piliers 1 et 3, et sont testables sans service externe.

---

## Séquence recommandée (petits diffs vérifiables, sur base à jour)

1. **A2 — Routeur de modèle par sensibilité** (débloque le discours souverain + A9).
2. **A1 — Journal d'audit** (socle conformité, valeur immédiate en cabinet).
3. **B2 — Contre-argumentaire sourcé** (différenciateur Vault #5, renforce Pilier 1).
4. **B1 — Résumé** + **B3 — Analytics contentieux** (quick wins, Piliers 1 & 3).
5. Puis A3/A4 (rétention/export RGPD), A8 (ethical walls), B5 (timeline).

> Chaque item : conçu et livré dans l'archi de `main` (routes inline, sqlite brut, deps
> minimales), couvert par le moteur de tests fonctionnels (`functional/`) et `pytest`.

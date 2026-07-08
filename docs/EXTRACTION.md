# Extraction Insight — documentation technique

Le cœur de Jurilux Insight est un **pipeline d'extraction local et déterministe** (regex +
heuristiques, **aucun appel LLM**) qui transforme le texte des décisions de jurisprudence en
lignes structurées de la table `insight_appearances`. Ce document décrit chaque extracteur,
ses signaux, ses garde-fous et ses limites. Tout est dans `app/insight.py` ; le build est
`app/insight_build.py` (scan de l'index Meili `chunks`, agrégation par décision).

## Principes
- **Déterministe & auditable** : aucune boîte noire. Un même texte donne toujours le même résultat.
- **Couverture partielle assumée** : on n'invente jamais un signal. En l'absence de preuve
  textuelle, le champ reste `NULL` (montant, cabinet, délai, issue…).
- **Garde-fou RGPD/CNPD** : profilage des **avocats et parties uniquement**. Les constantes
  partagées `_BAD_TOKEN` (placeholders/non-personnes) et `_JUDICIAL_TITLE` (greffier, magistrat,
  huissier, procureur, substitut) garantissent qu'aucun titre judiciaire n'est capté comme avocat.
- **Indicatif, jamais certain** : taux de succès, montants, délais sont des **estimations**.

## Extracteurs

### Avocats — `_NAME_RE`, `parse_chunk`, `extract_lawyers`
- Reconnaît « **Maître** Prénom NOM » et l'abréviation « **Me** » / « Me. » (fréquente en LU).
- Gère 1–3 prénoms/initiales et une **particule** minuscule (`de`, `van`, `von`, `d'`…) devant le
  NOM en capitales.
- Filtré par `_PLACEHOLDER_RE` (chiffres, placeholders de pseudonymisation, titres judiciaires).
- Clé de regroupement : `name_key` (sans accents, majuscules, espaces normalisés).

### Côté (A/B) — `_side_before`, `_ROLE_A`/`_ROLE_B`
- A = demandeur / appelant / requérant / poursuivant / **partie civile**.
- B = défendeur / intimé / **prévenu** (pénal). Formes masculines et féminines.
- Attribué par le **marqueur de rôle le plus proche AVANT** la mention de l'avocat (fenêtre 320).

### Issue estimée — `_OUT_A`/`_OUT_B` (dans `parse_chunk`)
- Ne tranche que si le dispositif (`_DISPO_HINT`) est présent **et qu'un seul côté ressort**
  (sinon `None` — on ne sur-attribue pas).
- A : « fait droit », « déclare … fondé », « infirme », « réforme », « casse et annule »,
  « condamne … à payer/verser/indemniser ».
- B : « déboute », « rejette », « non fondé », « confirme le jugement », « déclare … irrecevable »,
  « dit n'y avoir lieu ».
- `won` = 1 si `side == outcome`, 0 sinon, `NULL` si l'un des deux manque.

### Sens du dispositif — `extract_sens`
- `cassation` | `rejet` (pourvoi/recours/**appel**) | `irrecevabilité` | `réformation`
  (`réforme`/**`infirme`**) | `confirmation`. Priorité aux verbes les plus spécifiques.

### Matières — `_MATTER_RE`, `matter_hits`, `matter_from_docid`
- 13 domaines (travail, bail, famille, successions, sociétés/commercial, responsabilité civile,
  assurances, immobilier/construction, pénal, fiscal/administratif, **propriété intellectuelle**,
  **consommation/crédit**, **circulation/roulage**) — comptage de mots-clés, domaine dominant.
- `matter_from_docid` : domaine sûr pour les chambres spécialisées (`JPLTRAVAIL`, `JPLBAIL`).

### Montants / quantum — `extract_amount`
- Nombres au format européen (`12.345,67`, `12 345,67`) avec le marqueur `€`/`EUR`/`euros`
  **avant OU après** (`12.345 €`, `EUR 12.345`, `€ 1.500`).
- On retient la **plus grande** somme plausible (le principal domine intérêts/frais).
- Garde-fous : `_AMOUNT_MIN` (100 €) / `_AMOUNT_MAX` (500 M€).

### Articles de loi — `extract_articles`, `_ARTICLE_RE`
- « article L.124-10 », « articles 1134 et 1135 », « art. 579 » — gère les **énumérations**,
  normalise (`L.124-10`), déduplique, plafonne à 25.

### Cabinets — `_firm_near`, `_FIRM_RE`
- Uniquement les cabinets **explicitement nommés** (« Étude X », « cabinet X »,
  « société d'avocats X ») ; **jamais inférés**. Capture **1 à 4 tokens** joints par espace /
  « & » / « et » (ex. « Bonn Steichen & Partners »). Le plus proche de l'avocat l'emporte.
- `_FIRM_STOP` retire les mots de phrase capitalisés happés en queue.

### Délais de procédure — `extract_delai`
- Durée en **jours** entre la date de décision (préfixe `AAAAMMJJ` du `doc_id`) et la date de
  départ plausible la plus **ancienne** située près d'un marqueur d'introduction
  (`assignation`, `requête introductive`, `signification`, `jugement entrepris`…).
- Garde-fous : `_DELAI_MIN` (30 j) / `_DELAI_MAX` (15 ans). `None` si non estimable.

## Agrégation — `analytics()` / `overview()`
- SQLite n'a pas de `MEDIAN` : montants et délais sont chargés une fois puis les **médianes**
  par dimension (matière / juridiction / année) et globale sont calculées en Python
  (`_median_by`). Chaque groupe porte `amount_median`/`amount_n` et `delai_median`/`delai_n`.

## Tests
- Unitaire : `tests/test_insight.py` — chaque extracteur est verrouillé sur des **extraits
  réalistes** de décisions luxembourgeoises (y compris cas ambigus → `None`, garde-fou RGPD → `[]`).
- Fonctionnel : `functional/scenarios/insight.py` (injection `INSIGHT_ROWS` via le banc) — forme
  et sémantique des endpoints, y compris montants et délais.

## Limites connues
- Homonymes d'avocats non désambiguïsés (regroupement par nom normalisé).
- Rattachement à un cabinet **partiel** (dépend d'une mention explicite).
- L'issue estimée est une heuristique : à présenter comme telle, jamais comme un verdict certain.

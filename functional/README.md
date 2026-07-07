# Moteur de tests fonctionnels

Teste Jurilux **par cas d'usage dérivés de la documentation** (le contrat d'API de
`CLAUDE.md`), **pour chaque profil**, en **injectant des données de test**, et **évalue le
succès par fonctionnalité**. Aligné sur l'archi de `main` : stdlib + FastAPI `TestClient`,
aucune dépendance nouvelle, **aucun service externe** (Meilisearch/Anthropic stubés).

Deux niveaux de test complémentaires :

- **Parcours utilisateur** (`parcours.py`) — des scénarios réalistes avec un **objectif
  clair** et une **séquence d'étapes enchaînées** : l'état se transmet d'une étape à l'autre
  (jeton, id d'espace/dossier/document, promotion de rôle, reconnexion…), plusieurs
  **acteurs** peuvent intervenir dans un même parcours. Évaluation étape par étape puis
  verdict du parcours entier.
- **Matrice d'autorisation** (`cas.py`) — un endpoint × chaque profil, pour verrouiller les
  permissions (401/403/404, refus gracieux) de façon exhaustive.

## Lancer

```bash
python -m functional.run                     # parcours + matrice, rapport texte + code 0/1
python -m functional.run --mode parcours     # uniquement les parcours utilisateur
python -m functional.run --mode matrice      # uniquement la matrice d'autorisation
python -m functional.run --format markdown   # ou --format json (CI/dashboards)
python -m functional.run --filtre Vault      # ne garder que ce qui contient « Vault »
```

Il tourne aussi comme **passerelle CI** : `tests/test_functional.py` exige les deux rapports
100 % verts (et un méta-test garantit que le moteur détecte bien un échec).

## Écrire un parcours

Dans `parcours.py`, un `Parcours` = objectif + profil de départ + liste d'`Etape`. Une étape
peut **capturer** une valeur pour les suivantes et agir sous un **acteur** donné :

```python
Parcours("mon-parcours", "Objectif clair et concret", "pro", [
    E("crée un espace", "POST", "/api/workspaces", ok(lambda j: bool(j["id"])),
      corps={"name": "X"}, capture=lambda j, c: c.__setitem__("wid", j["id"])),
    E("un collègue le voit", "GET", "/api/workspaces/{wid}/dossiers",
      ok(lambda j: "items" in j), acteur="headers_collegue", role="collègue"),
], preparer=_pre_pro_plus_collegue)   # monte les acteurs et les met dans le contexte
```

Le `corps` peut être `lambda ctx: {...}` pour réutiliser l'état capturé.

## Ce que ça couvre

Deux dimensions de **profils** :

- **compte** : `anonyme`, `étudiant`, `pro`, `admin` (plan + `is_admin`) ;
- **rôles / isolation** provisionnés à la volée : rôles d'espace (`propriétaire`,
  `admin_espace`, `membre`, `étranger`) et isolation Vault (`propriétaire` vs `intrus`).

Chaque **cas d'usage** déclare, par profil, l'**attente** :

- `ok(verif)` — 2xx + prédicat sur le corps JSON ;
- `refuse(code)` — refus d'autorisation avec code précis (401 / 403 / 404) ;
- `gracieux()` — 200 mais `refused=true` (quota épuisé, aucun extrait) — **jamais un 500**.

Le moteur exécute **cas × profils**, compare l'obtenu à l'attendu, et agrège un **verdict
par fonctionnalité** (une fonctionnalité est *réussie* si toutes ses assertions passent).

Couverture actuelle : **~470 assertions** (≈360 matrice + ≈110 parcours), succès **et**
branches d'erreur/validation (401/403/404/422/413, refus gracieux), sur toutes les
fonctionnalités.

## Architecture

| Fichier | Rôle |
|---|---|
| `banc.py` | Base SQLite jetable, stubs des services externes, **données injectées** (corpus, insight, docs), provisionnement des profils. Tout est restauré à la sortie. |
| `engine.py` | Modèle (`CasUsage`, `Etape`, `Parcours`, `Attente`), exécution multi-profils + parcours, **stubs par scénario**, **rapport** (texte / markdown / json). |
| `scenarios/` | Le **catalogue par domaine** : un module par sous-système (`service`, `auth`, `ask`, `feedback_partage`, `insight`, `cabinet`, `veille`, `vault`, `admin`). Chacun expose `CAS` et/ou `PARCOURS` ; `scenarios/__init__.py` les agrège (import tolérant). C'est ici qu'on ajoute de la couverture. |
| `cas.py` / `parcours.py` | Cas/parcours historiques du cœur + agrégation du paquet `scenarios`. |
| `run.py` | CLI. |

## Ajouter un cas d'usage

Éditer le module de domaine concerné dans `scenarios/` (il commence par `from ._base import *`) :

```python
CasUsage("mon-cas", "Ma fonctionnalité",
         "Ce que dit la doc / le contrat d'API.",
         "POST", "/api/mon-endpoint",
         {"anonyme": refuse(401),
          "pro": ok(lambda j: j["champ"] == "valeur attendue")},
         corps={"clef": "valeur"})
```

Pour un cas qui nécessite un **montage** (créer un espace, déposer un document, saturer un
quota), fournir un `preparer(banc, nom_profil) -> (headers, contexte)` ; le `contexte`
alimente les `{placeholders}` du chemin et un `corps` sous forme de `lambda ctx: {...}`.

### Tester une branche d'erreur (stubs par scénario)

Pour forcer une panne le temps d'une requête, passer `stubs=[(objet, attribut, valeur)]`
(restauré automatiquement après). Objets exposés par `_base` : `SEARCH`, `RAG`, `VAULT`,
`MAIN`, `SETTINGS`. Exemples :

```python
stubs=[(SEARCH, "search", lambda q, k, f: [])]          # aucun résultat → refus gracieux
stubs=[(SEARCH, "meili_healthy", lambda: False)]        # Meili en panne → /health 503
stubs=[(MAIN, "_VAULT_MAX_BYTES", 5)]                   # + contenu long → upload 413
```

## Injection de données

Le banc injecte un corpus déterministe (`HITS_CORPUS`), des apparitions insight
(`INSIGHT_ROWS`) et stube l'indexation/recherche Vault — de sorte que les fonctionnalités
**réussissent réellement** sans Meilisearch ni Anthropic. Pour enrichir le jeu de données,
compléter ces constantes dans `banc.py`.

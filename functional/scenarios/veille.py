"""Couverture : veille / alertes « nouvelle jurisprudence sur mes sujets » (/api/alerts).

Cycle de vie complet d'une alerte : création (auth, validation, filtre source_type),
listing, vérification (une alerte / toutes), consultation des décisions remontées,
suppression — plus l'**isolation stricte** (un utilisateur n'atteint jamais l'alerte
d'un autre : 404, jamais 403 qui divulguerait l'existence).

`alert_runner.check` est stubé dans le banc (renvoie 0) : les vérifications testent le
contrat d'API (« new » présent), pas la logique de retrieval.
"""
from __future__ import annotations

from ._base import *


# ============================ préparateurs ============================
def _prep_alerte(banc: Banc, nom: str) -> tuple:
    """Provisionne le profil compte demandé puis lui crée une alerte ; met `aid` en contexte.
    Sert aux cas check / hits / delete sur une alerte que l'acteur possède bien."""
    headers, ctx = banc.profil(nom)
    aid = banc.creer_alerte(headers, query="licenciement abusif")
    return headers, {**ctx, "aid": aid}


def _prep_isolation(banc: Banc, nom: str) -> tuple:
    """A (propriétaire) crée une alerte ; B (intrus) est l'acteur. Renvoie les en-têtes de
    B et l'`aid` de A → toute action de B sur cette alerte doit répondre 404."""
    proprio = banc.enregistrer(plan="pro")
    aid = banc.creer_alerte(proprio["headers"], query="sujet du propriétaire")
    intrus = banc.enregistrer()
    return intrus["headers"], {"aid": aid}


# ============================ catalogue des cas d'usage ============================
CAS = [
    # --- création d'une alerte ---
    CasUsage("alert-create-anonyme", "Veille — créer une alerte",
             "Créer une alerte sans authentification → 401.",
             "POST", "/api/alerts", {"anonyme": refuse(401)},
             corps={"query": "licenciement abusif"}),
    CasUsage("alert-create-succes", "Veille — créer une alerte",
             "Création réussie pour chaque profil connecté → {id, …, unseen}.",
             "POST", "/api/alerts",
             {p: ok(lambda j: bool(j.get("id"))) for p in AUTHENTIFIES},
             corps={"query": "licenciement abusif"}),
    CasUsage("alert-create-query-courte", "Veille — créer une alerte",
             "Query < 2 caractères → 422 (validation Pydantic min_length).",
             "POST", "/api/alerts",
             {p: refuse(422) for p in AUTHENTIFIES},
             corps={"query": "a"}),
    CasUsage("alert-create-query-manquante", "Veille — créer une alerte",
             "Champ query absent → 422.",
             "POST", "/api/alerts", {"pro": refuse(422)},
             corps={"source_type": "jurisprudence"}),
    CasUsage("alert-create-source-type", "Veille — créer une alerte",
             "Création avec filtre source_type=jurisprudence → succès.",
             "POST", "/api/alerts",
             {p: ok(lambda j: bool(j.get("id"))) for p in AUTHENTIFIES},
             corps={"query": "faute grave", "source_type": "jurisprudence"}),
    CasUsage("alert-create-unseen", "Veille — créer une alerte",
             "La réponse expose le compteur unseen (1er check à la création).",
             "POST", "/api/alerts", {"pro": ok(lambda j: "unseen" in j)},
             corps={"query": "préavis légal"}),

    # --- lister ses alertes ---
    CasUsage("alert-list-anonyme", "Veille — lister les alertes",
             "Lister sans authentification → 401.",
             "GET", "/api/alerts", {"anonyme": refuse(401)}),
    CasUsage("alert-list-succes", "Veille — lister les alertes",
             "GET /api/alerts → {items} pour chaque profil connecté.",
             "GET", "/api/alerts",
             {p: ok(lambda j: "items" in j) for p in AUTHENTIFIES}),

    # --- vérifier toutes les alertes ---
    CasUsage("alert-checkall-anonyme", "Veille — vérifier toutes les alertes",
             "check-all sans authentification → 401.",
             "POST", "/api/alerts/check-all", {"anonyme": refuse(401)}),
    CasUsage("alert-checkall-succes", "Veille — vérifier toutes les alertes",
             "POST /api/alerts/check-all → {new} pour chaque profil connecté.",
             "POST", "/api/alerts/check-all",
             {p: ok(lambda j: "new" in j) for p in AUTHENTIFIES}),

    # --- vérifier une alerte ---
    CasUsage("alert-check-succes", "Veille — vérifier une alerte",
             "Vérifier une alerte que l'on possède → {new}.",
             "POST", "/api/alerts/{aid}/check",
             {p: ok(lambda j: "new" in j) for p in AUTHENTIFIES},
             preparer=_prep_alerte),
    CasUsage("alert-check-inconnu", "Veille — vérifier une alerte",
             "aid inconnu → 404.",
             "POST", "/api/alerts/999999/check",
             {p: refuse(404) for p in AUTHENTIFIES}),
    CasUsage("alert-check-anonyme", "Veille — vérifier une alerte",
             "Vérifier une alerte sans authentification → 401.",
             "POST", "/api/alerts/1/check", {"anonyme": refuse(401)}),
    CasUsage("alert-check-isolation", "Veille — vérifier une alerte",
             "Vérifier l'alerte d'un AUTRE utilisateur → 404 (isolation stricte).",
             "POST", "/api/alerts/{aid}/check", {"intrus": refuse(404)},
             preparer=_prep_isolation),

    # --- consulter les décisions remontées (hits) ---
    CasUsage("alert-hits-succes", "Veille — décisions remontées",
             "GET /api/alerts/{aid}/hits → {items} pour le propriétaire.",
             "GET", "/api/alerts/{aid}/hits",
             {p: ok(lambda j: "items" in j) for p in AUTHENTIFIES},
             preparer=_prep_alerte),
    CasUsage("alert-hits-inconnu", "Veille — décisions remontées",
             "aid inconnu → 404.",
             "GET", "/api/alerts/999999/hits",
             {p: refuse(404) for p in AUTHENTIFIES}),
    CasUsage("alert-hits-anonyme", "Veille — décisions remontées",
             "Consulter les hits sans authentification → 401.",
             "GET", "/api/alerts/1/hits", {"anonyme": refuse(401)}),
    CasUsage("alert-hits-isolation", "Veille — décisions remontées",
             "Consulter les hits de l'alerte d'un AUTRE → 404 (isolation stricte).",
             "GET", "/api/alerts/{aid}/hits", {"intrus": refuse(404)},
             preparer=_prep_isolation),

    # --- supprimer une alerte ---
    CasUsage("alert-delete-succes", "Veille — supprimer une alerte",
             "Supprimer une alerte que l'on possède → {ok:true}.",
             "DELETE", "/api/alerts/{aid}",
             {p: ok(lambda j: j.get("ok") is True) for p in AUTHENTIFIES},
             preparer=_prep_alerte),
    CasUsage("alert-delete-inconnu", "Veille — supprimer une alerte",
             "aid inconnu → 404.",
             "DELETE", "/api/alerts/999999",
             {p: refuse(404) for p in AUTHENTIFIES}),
    CasUsage("alert-delete-anonyme", "Veille — supprimer une alerte",
             "Supprimer sans authentification → 401.",
             "DELETE", "/api/alerts/1", {"anonyme": refuse(401)}),
    CasUsage("alert-delete-isolation", "Veille — supprimer une alerte",
             "Supprimer l'alerte d'un AUTRE utilisateur → 404 (isolation stricte).",
             "DELETE", "/api/alerts/{aid}", {"intrus": refuse(404)},
             preparer=_prep_isolation),
]


# ============================ préparateur de parcours ============================
def _pre_veille_avancee(banc: Banc) -> dict:
    """Un avocat pro acteur du parcours, plus un tiers qui possède sa propre alerte (cible
    d'isolation). Renvoie les en-têtes du pro et l'`aid` de l'alerte du tiers."""
    pro = banc.enregistrer(plan="pro")
    tiers = banc.enregistrer()
    aid_tiers = banc.creer_alerte(tiers["headers"], query="sujet du tiers")
    return {"headers": pro["headers"], "aid_tiers": aid_tiers}


# ============================ catalogue des parcours ============================
PARCOURS = [
    Parcours("veille-avancee", "Veille avancée : deux alertes, vérification, purge et isolation",
             "pro", [
        E("crée une 1re alerte", "POST", "/api/alerts", ok(lambda j: bool(j.get("id"))),
          corps={"query": "licenciement abusif"},
          capture=lambda j, c: c.__setitem__("aid1", j["id"])),
        E("crée une 2e alerte", "POST", "/api/alerts", ok(lambda j: bool(j.get("id"))),
          corps={"query": "bail commercial"},
          capture=lambda j, c: c.__setitem__("aid2", j["id"])),
        E("les deux alertes apparaissent dans la liste", "GET", "/api/alerts",
          ok(lambda j: len(j["items"]) == 2)),
        E("vérifie toutes ses alertes", "POST", "/api/alerts/check-all",
          ok(lambda j: "new" in j)),
        E("consulte les décisions de la 1re alerte", "GET", "/api/alerts/{aid1}/hits",
          ok(lambda j: "items" in j)),
        E("supprime la 1re alerte", "DELETE", "/api/alerts/{aid1}",
          ok(lambda j: j.get("ok") is True)),
        E("il ne reste qu'une alerte", "GET", "/api/alerts",
          ok(lambda j: len(j["items"]) == 1)),
        E("ne peut PAS vérifier l'alerte d'un tiers", "POST", "/api/alerts/{aid_tiers}/check",
          refuse(404)),
        E("ne peut PAS supprimer l'alerte d'un tiers", "DELETE", "/api/alerts/{aid_tiers}",
          refuse(404)),
    ], preparer=_pre_veille_avancee),
]

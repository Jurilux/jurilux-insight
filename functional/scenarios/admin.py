"""Couverture : backoffice `/api/admin/*` — gate `is_admin` (RBAC) sur chaque endpoint,
plus les branches fonctionnelles de gestion des utilisateurs (plan, droits admin,
suppression) et la validation de l'inspecteur de récupération (probe).

RBAC standard attendu partout : anonyme → 401, étudiant → 403, pro → 403, admin → 2xx.
"""
from __future__ import annotations

from ._base import *


# --------- fabrique de profils RBAC (anonyme/étudiant/pro/admin) ---------
def _rbac(verif) -> dict:
    """Attentes RBAC standard d'un endpoint réservé aux admins : refus 401/403 pour les
    non-admins, succès (avec prédicat `verif`) pour l'admin."""
    return {"anonyme": refuse(401), "etudiant": refuse(403), "pro": refuse(403),
            "admin": ok(verif)}


# --------- préparateur des cas « niveau fonction » (admin courant + cible) ---------
def _pre_admin_cible(banc: Banc, nom: str) -> tuple:
    """Provisionne un ADMIN courant (dont on renvoie les en-têtes) et une CIBLE distincte
    (compte étudiant normal). Expose `uid_admin` (soi-même) et `uid_cible` (l'autre)."""
    c = banc.enregistrer(admin=True)
    return c["headers"], {"uid_admin": c["uid"], "uid_cible": banc.enregistrer()["uid"]}


CAS = [
    # ============ RBAC : un cas par endpoint (gate is_admin) ============
    CasUsage("admin-overview", "Backoffice — overview",
             "GET /api/admin/overview : tableau de bord, réservé aux admins.",
             "GET", "/api/admin/overview",
             _rbac(lambda j: isinstance(j, dict))),

    CasUsage("admin-feedback", "Backoffice — feedback",
             "GET /api/admin/feedback : {items, stats}, réservé aux admins.",
             "GET", "/api/admin/feedback",
             _rbac(lambda j: "items" in j and "stats" in j)),

    CasUsage("admin-activity", "Backoffice — activité",
             "GET /api/admin/activity : {per_day}, réservé aux admins.",
             "GET", "/api/admin/activity",
             _rbac(lambda j: "per_day" in j)),

    CasUsage("admin-eval", "Backoffice — eval",
             "GET /api/admin/eval : banc des 10 questions de référence (sans LLM).",
             "GET", "/api/admin/eval",
             _rbac(lambda j: j["total"] == 10)),

    CasUsage("admin-probe", "Backoffice — probe",
             "POST /api/admin/probe : inspecteur de récupération, réservé aux admins.",
             "POST", "/api/admin/probe",
             _rbac(lambda j: "hits" in j and "count" in j),
             corps={"q": "faute grave"}),

    CasUsage("admin-users", "Backoffice — utilisateurs",
             "GET /api/admin/users : liste des comptes, réservé aux admins.",
             "GET", "/api/admin/users",
             _rbac(lambda j: len(j["items"]) >= 1)),

    CasUsage("admin-questions", "Backoffice — questions",
             "GET /api/admin/questions : dernières questions, réservé aux admins.",
             "GET", "/api/admin/questions",
             _rbac(lambda j: "items" in j)),

    CasUsage("admin-llm", "Backoffice — routage LLM",
             "GET /api/admin/llm : routage du modèle par sensibilité (souveraineté), admins.",
             "GET", "/api/admin/llm",
             _rbac(lambda j: j["public"]["fournisseur"] and j["confidentiel"]["fournisseur"])),

    # ============ Niveau fonction : plan utilisateur ============
    CasUsage("plan-ok", "Backoffice — plan utilisateur",
             "POST /api/admin/users/{id}/plan : passe une cible en plan pro → {ok:true}.",
             "POST", "/api/admin/users/{uid_cible}/plan",
             {"admin": ok(lambda j: j.get("ok") is True)},
             corps={"plan": "pro"}, preparer=_pre_admin_cible),
    CasUsage("plan-invalide", "Backoffice — plan utilisateur",
             "Plan inconnu (ni student ni pro) → 400.",
             "POST", "/api/admin/users/{uid_cible}/plan",
             {"admin": refuse(400)},
             corps={"plan": "gold"}, preparer=_pre_admin_cible),
    CasUsage("plan-user-inconnu", "Backoffice — plan utilisateur",
             "Utilisateur inexistant → 404.",
             "POST", "/api/admin/users/999999/plan",
             {"admin": refuse(404)},
             corps={"plan": "pro"}, preparer=_pre_admin_cible),

    # ============ Niveau fonction : droits admin ============
    CasUsage("admin-set-ok", "Backoffice — droits admin",
             "POST /api/admin/users/{id}/admin : promeut une cible admin → {ok:true}.",
             "POST", "/api/admin/users/{uid_cible}/admin",
             {"admin": ok(lambda j: j.get("ok") is True)},
             corps={"is_admin": True}, preparer=_pre_admin_cible),
    CasUsage("admin-set-self-retrait", "Backoffice — droits admin",
             "Un admin ne peut pas retirer SES PROPRES droits admin → 400.",
             "POST", "/api/admin/users/{uid_admin}/admin",
             {"admin": refuse(400)},
             corps={"is_admin": False}, preparer=_pre_admin_cible),
    CasUsage("admin-set-user-inconnu", "Backoffice — droits admin",
             "Utilisateur inexistant → 404.",
             "POST", "/api/admin/users/999999/admin",
             {"admin": refuse(404)},
             corps={"is_admin": True}, preparer=_pre_admin_cible),

    # ============ Niveau fonction : suppression de compte ============
    CasUsage("delete-ok", "Backoffice — suppression compte",
             "DELETE /api/admin/users/{id} : supprime une cible → {ok:true}.",
             "DELETE", "/api/admin/users/{uid_cible}",
             {"admin": ok(lambda j: j.get("ok") is True)},
             preparer=_pre_admin_cible),
    CasUsage("delete-self", "Backoffice — suppression compte",
             "Un admin ne peut pas supprimer SON PROPRE compte → 400.",
             "DELETE", "/api/admin/users/{uid_admin}",
             {"admin": refuse(400)},
             preparer=_pre_admin_cible),
    CasUsage("delete-user-inconnu", "Backoffice — suppression compte",
             "Utilisateur inexistant → 404.",
             "DELETE", "/api/admin/users/999999",
             {"admin": refuse(404)},
             preparer=_pre_admin_cible),

    # ============ Niveau fonction : validation probe ============
    CasUsage("probe-q-vide", "Backoffice — probe",
             "Requête vide (q=\"\") → 422 (validation Pydantic min_length=1).",
             "POST", "/api/admin/probe",
             {"admin": refuse(422)},
             corps={"q": ""}),
]

PARCOURS = []

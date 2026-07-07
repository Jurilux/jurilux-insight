"""Couverture : offre cabinet (V3) — espaces de travail, membres, rôles, dossiers partagés.

C'est le plus gros domaine du produit. On y couvre, en matrice d'autorisation, tout le
cycle de vie :
  - **espaces** : création (auth requise, validation) et listing ;
  - **membres** : lister, ajouter (RBAC owner/admin, doublon, e-mail inconnu, rôle invalide),
    retirer, changer de rôle (auto-changement interdit, propriétaire intouchable) ;
  - **espace** : supprimer (propriétaire seul) et quitter (`/leave`) ;
  - **dossiers** : créer, lister, ajouter/lister des éléments, supprimer.

Chaque cas provisionne, **par profil**, SON espace isolé avec l'acteur dans le rôle voulu
(`proprietaire` / `admin_espace` / `membre` / `etranger` / `anonyme`). Codes d'erreur
vérifiés un à un contre `app/main.py` (routes ~194-325) et `app/workspace.py`.
"""
from __future__ import annotations

from ._base import *


# ============================ montage multi-acteurs ============================
def _acteurs_espace(banc: Banc) -> dict:
    """Monte un espace peuplé : owner (pro), un admin et un membre déjà rattachés, plus un
    étranger non-membre. Renvoie les comptes et l'id de l'espace."""
    owner = banc.enregistrer(plan="pro")
    admin = banc.enregistrer()
    membre = banc.enregistrer()
    etranger = banc.enregistrer()
    wid = banc.creer_espace(owner["headers"], "Cabinet")
    banc.ajouter_membre(owner["headers"], wid, admin["email"], "admin")
    banc.ajouter_membre(owner["headers"], wid, membre["email"], "member")
    return {"owner": owner, "admin": admin, "membre": membre, "etranger": etranger, "wid": wid}


def _headers_pour(a: dict, nom: str) -> dict:
    """En-têtes de l'acteur correspondant au nom de profil (anonyme = aucun jeton)."""
    return {"proprietaire": a["owner"]["headers"],
            "admin_espace": a["admin"]["headers"],
            "membre": a["membre"]["headers"],
            "etranger": a["etranger"]["headers"],
            "anonyme": {}}[nom]


def _pre_membres(banc: Banc, nom: str) -> tuple:
    """Espace peuplé + une cible fraîche (e-mail d'un compte non encore membre) à ajouter."""
    a = _acteurs_espace(banc)
    cible = banc.enregistrer()["email"]
    return _headers_pour(a, nom), {"wid": a["wid"], "cible": cible}


def _pre_doublon(banc: Banc, nom: str) -> tuple:
    """La cible est un membre DÉJÀ rattaché → l'ajout doit échouer en doublon (400)."""
    a = _acteurs_espace(banc)
    return _headers_pour(a, nom), {"wid": a["wid"], "cible": a["membre"]["email"]}


def _pre_cible_membre(banc: Banc, nom: str) -> tuple:
    """Espace peuplé + uid du membre (cible retirable/promouvable) et uid du propriétaire."""
    a = _acteurs_espace(banc)
    return _headers_pour(a, nom), {"wid": a["wid"], "uid_cible": a["membre"]["uid"],
                                   "uid_owner": a["owner"]["uid"]}


def _pre_dossiers(banc: Banc, nom: str) -> tuple:
    """Espace peuplé (sans dossier) — pour créer/lister des dossiers."""
    a = _acteurs_espace(banc)
    return _headers_pour(a, nom), {"wid": a["wid"]}


def _pre_dossier(banc: Banc, nom: str) -> tuple:
    """Espace peuplé + un dossier déjà créé par le propriétaire (id dans `did`)."""
    a = _acteurs_espace(banc)
    did = banc.creer_dossier(a["owner"]["headers"], a["wid"], "Dossier")
    return _headers_pour(a, nom), {"wid": a["wid"], "did": did}


# raccourci d'attente réutilisé : 200 + {ok:true}
def _ok_true():
    return ok(lambda j: j.get("ok") is True)


# ============================ catalogue des cas d'usage ============================
CAS = [
    # ---------------------------------------------------------------- espaces
    CasUsage("ws-create-anonyme", "Cabinet — espaces",
             "Créer un espace sans authentification → 401.",
             "POST", "/api/workspaces", {"anonyme": refuse(401)},
             corps={"name": "Cabinet Test"}),
    CasUsage("ws-create-ok", "Cabinet — espaces",
             "Créer un espace (connecté) → {id, role:'owner'}.",
             "POST", "/api/workspaces",
             {p: ok(lambda j: bool(j.get("id")) and j.get("role") == "owner") for p in AUTHENTIFIES},
             corps={"name": "Cabinet Test"}),
    CasUsage("ws-create-nom-vide", "Cabinet — espaces",
             "Nom vide → 422 (validation Pydantic min_length=1).",
             "POST", "/api/workspaces", {"pro": refuse(422)},
             corps={"name": ""}),
    CasUsage("ws-list-anonyme", "Cabinet — espaces",
             "Lister ses espaces sans authentification → 401.",
             "GET", "/api/workspaces", {"anonyme": refuse(401)}),
    CasUsage("ws-list-ok", "Cabinet — espaces",
             "Lister ses espaces (connecté) → {items}.",
             "GET", "/api/workspaces",
             {p: ok(lambda j: "items" in j) for p in AUTHENTIFIES}),

    # ---------------------------------------------------------------- membres : liste
    CasUsage("members-list", "Cabinet — membres (liste)",
             "Tout membre voit la liste ; non-membre → 404 ; anonyme → 401.",
             "GET", "/api/workspaces/{wid}/members",
             {"proprietaire": ok(lambda j: len(j["items"]) == 3),
              "admin_espace": ok(lambda j: len(j["items"]) == 3),
              "membre": ok(lambda j: len(j["items"]) == 3),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_membres),

    # ---------------------------------------------------------------- membres : ajout
    CasUsage("member-add", "Cabinet — membres (ajout)",
             "Ajout : owner/admin OK ; membre → 403 ; étranger → 404 ; anonyme → 401.",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": ok(lambda j: j.get("role") == "member"),
              "admin_espace": ok(lambda j: j.get("role") == "member"),
              "membre": refuse(403),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             corps=lambda c: {"email": c["cible"], "role": "member"},
             preparer=_pre_membres),
    CasUsage("member-add-admin", "Cabinet — membres (ajout)",
             "Ajout d'un admin par le propriétaire → role:'admin'.",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": ok(lambda j: j.get("role") == "admin")},
             corps=lambda c: {"email": c["cible"], "role": "admin"},
             preparer=_pre_membres),
    CasUsage("member-add-doublon", "Cabinet — membres (ajout)",
             "Utilisateur déjà membre → 400.",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": refuse(400)},
             corps=lambda c: {"email": c["cible"], "role": "member"},
             preparer=_pre_doublon),
    CasUsage("member-add-inconnu", "Cabinet — membres (ajout)",
             "E-mail sans compte → 400 (l'utilisateur doit s'inscrire d'abord).",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": refuse(400)},
             corps={"email": "jamais-inscrit@test.lu", "role": "member"},
             preparer=_pre_membres),
    CasUsage("member-add-role-invalide", "Cabinet — membres (ajout)",
             "Rôle invalide « chef » → 400 (admin | member seuls acceptés).",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": refuse(400)},
             corps=lambda c: {"email": c["cible"], "role": "chef"},
             preparer=_pre_membres),
    CasUsage("member-add-email-court", "Cabinet — membres (ajout)",
             "E-mail trop court (< 3) → 422 (validation Pydantic).",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": refuse(422)},
             corps={"email": "a", "role": "member"},
             preparer=_pre_membres),

    # ---------------------------------------------------------------- membres : retrait
    CasUsage("member-remove", "Cabinet — membres (retrait)",
             "Retrait d'un membre : owner/admin OK ; membre → 403 ; étranger → 404 ; anonyme → 401.",
             "DELETE", "/api/workspaces/{wid}/members/{uid_cible}",
             {"proprietaire": _ok_true(),
              "admin_espace": _ok_true(),
              "membre": refuse(403),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_cible_membre),
    CasUsage("member-remove-owner", "Cabinet — membres (retrait)",
             "Le propriétaire n'est jamais retirable → 400.",
             "DELETE", "/api/workspaces/{wid}/members/{uid_owner}",
             {"proprietaire": refuse(400)},
             preparer=_pre_cible_membre),

    # ---------------------------------------------------------------- membres : changement de rôle
    CasUsage("member-role", "Cabinet — membres (rôle)",
             "Changer le rôle d'un membre : owner/admin OK ; membre → 403 ; étranger → 404 ; anonyme → 401.",
             "POST", "/api/workspaces/{wid}/members/{uid_cible}/role",
             {"proprietaire": _ok_true(),
              "admin_espace": _ok_true(),
              "membre": refuse(403),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             corps={"role": "admin"},
             preparer=_pre_cible_membre),
    CasUsage("member-role-soi-meme", "Cabinet — membres (rôle)",
             "Changer son propre rôle → 400.",
             "POST", "/api/workspaces/{wid}/members/{uid_owner}/role",
             {"proprietaire": refuse(400)},
             corps={"role": "member"},
             preparer=_pre_cible_membre),
    CasUsage("member-role-cible-owner", "Cabinet — membres (rôle)",
             "Un admin ne peut pas changer le rôle du propriétaire → 404.",
             "POST", "/api/workspaces/{wid}/members/{uid_owner}/role",
             {"admin_espace": refuse(404)},
             corps={"role": "admin"},
             preparer=_pre_cible_membre),
    CasUsage("member-role-invalide", "Cabinet — membres (rôle)",
             "Rôle cible invalide → 400.",
             "POST", "/api/workspaces/{wid}/members/{uid_cible}/role",
             {"proprietaire": refuse(400)},
             corps={"role": "chef"},
             preparer=_pre_cible_membre),

    # ---------------------------------------------------------------- espace : suppression
    CasUsage("ws-delete", "Cabinet — espace (suppression / quitter)",
             "Supprimer l'espace : propriétaire OK ; admin/membre → 403 ; étranger → 404 ; anonyme → 401.",
             "DELETE", "/api/workspaces/{wid}",
             {"proprietaire": _ok_true(),
              "admin_espace": refuse(403),
              "membre": refuse(403),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_membres),

    # ---------------------------------------------------------------- espace : quitter (/leave)
    CasUsage("ws-leave", "Cabinet — espace (suppression / quitter)",
             "Quitter l'espace : admin/membre OK ; propriétaire → 400 ; étranger → 404 ; anonyme → 401.",
             "POST", "/api/workspaces/{wid}/leave",
             {"admin_espace": _ok_true(),
              "membre": _ok_true(),
              "proprietaire": refuse(400),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_membres),

    # ---------------------------------------------------------------- dossiers : créer / lister
    CasUsage("dossier-create", "Cabinet — dossiers",
             "Créer un dossier : tout membre OK ; non-membre → 404 ; anonyme → 401.",
             "POST", "/api/workspaces/{wid}/dossiers",
             {"proprietaire": ok(lambda j: bool(j.get("id"))),
              "admin_espace": ok(lambda j: bool(j.get("id"))),
              "membre": ok(lambda j: bool(j.get("id"))),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             corps={"name": "Affaire Dupont"},
             preparer=_pre_dossiers),
    CasUsage("dossier-create-nom-vide", "Cabinet — dossiers",
             "Nom de dossier vide → 422.",
             "POST", "/api/workspaces/{wid}/dossiers",
             {"proprietaire": refuse(422)},
             corps={"name": ""},
             preparer=_pre_dossiers),
    CasUsage("dossier-list", "Cabinet — dossiers",
             "Lister les dossiers : tout membre OK ; non-membre → 404 ; anonyme → 401.",
             "GET", "/api/workspaces/{wid}/dossiers",
             {"proprietaire": ok(lambda j: "items" in j),
              "membre": ok(lambda j: "items" in j),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_dossier),

    # ---------------------------------------------------------------- dossiers : éléments
    CasUsage("dossier-item-add", "Cabinet — dossiers (éléments)",
             "Ajouter un élément à un dossier : membre OK ; étranger → 404 ; anonyme → 401.",
             "POST", "/api/dossiers/{did}/items",
             {"proprietaire": ok(lambda j: bool(j.get("id"))),
              "membre": ok(lambda j: bool(j.get("id"))),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             corps={"question": "licenciement immédiat ?", "answer": "Réponse sourcée.",
                    "status": "ok"},
             preparer=_pre_dossier),
    CasUsage("dossier-item-list", "Cabinet — dossiers (éléments)",
             "Lister les éléments : membre OK ; étranger (dossier d'autrui) → 404 ; anonyme → 401.",
             "GET", "/api/dossiers/{did}/items",
             {"proprietaire": ok(lambda j: "items" in j),
              "membre": ok(lambda j: "items" in j),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_dossier),
    CasUsage("dossier-delete", "Cabinet — dossiers (éléments)",
             "Supprimer un dossier : owner/admin OK ; membre → 403 ; étranger → 404 ; anonyme → 401.",
             "DELETE", "/api/dossiers/{did}",
             {"proprietaire": _ok_true(),
              "admin_espace": _ok_true(),
              "membre": refuse(403),
              "etranger": refuse(404),
              "anonyme": refuse(401)},
             preparer=_pre_dossier),
]


# ============================ parcours cabinet réalistes ============================
def _pre_cab_dossiers(banc: Banc) -> dict:
    """Un propriétaire pro et un collègue à inviter (dossier collaboratif)."""
    owner = banc.enregistrer(plan="pro")
    collegue = banc.enregistrer()
    return {"headers": owner["headers"], "email_collegue": collegue["email"],
            "headers_collegue": collegue["headers"]}


def _pre_cab_gouvernance(banc: Banc) -> dict:
    """Un propriétaire pro et deux collaborateurs (rôles, retrait, rétrogradation, départ)."""
    owner = banc.enregistrer(plan="pro")
    a = banc.enregistrer()
    b = banc.enregistrer()
    return {"headers": owner["headers"],
            "email_a": a["email"], "headers_a": a["headers"],
            "email_b": b["email"], "headers_b": b["headers"]}


PARCOURS = [
    # --- Cabinet : dossier collaboratif (le membre produit, le propriétaire supervise) ---
    Parcours("cabinet-dossier-collaboratif",
             "Cabinet : un dossier alimenté par un collègue et supervisé par le propriétaire",
             "pro", [
        E("le propriétaire crée l'espace", "POST", "/api/workspaces",
          ok(lambda j: bool(j.get("id"))), corps={"name": "Cabinet Gamma"},
          capture=lambda j, c: c.__setitem__("wid", j["id"])),
        E("il invite un collègue (membre)", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"),
          corps=lambda c: {"email": c["email_collegue"], "role": "member"}),
        E("le collègue crée un dossier", "POST", "/api/workspaces/{wid}/dossiers",
          ok(lambda j: bool(j.get("id"))), corps={"name": "Affaire Weber"},
          acteur="headers_collegue", role="collègue",
          capture=lambda j, c: c.__setitem__("did", j["id"])),
        E("le collègue y archive une recherche", "POST", "/api/dossiers/{did}/items",
          ok(lambda j: bool(j.get("id"))), acteur="headers_collegue", role="collègue",
          corps={"question": "préavis de licenciement ?", "answer": "Réponse sourcée.",
                 "status": "ok"}),
        E("le propriétaire voit l'élément archivé", "GET", "/api/dossiers/{did}/items",
          ok(lambda j: len(j["items"]) == 1)),
        E("le collègue (membre) NE peut PAS supprimer le dossier", "DELETE", "/api/dossiers/{did}",
          refuse(403), acteur="headers_collegue", role="collègue"),
        E("le propriétaire supprime le dossier", "DELETE", "/api/dossiers/{did}",
          ok(lambda j: j.get("ok") is True)),
        E("la liste des dossiers est de nouveau vide", "GET", "/api/workspaces/{wid}/dossiers",
          ok(lambda j: j["items"] == [])),
    ], preparer=_pre_cab_dossiers),

    # --- Cabinet : gouvernance (nomination, retrait, rétrogradation, départ) ---
    Parcours("cabinet-gouvernance",
             "Cabinet : gouvernance des membres (nommer, retirer, rétrograder, quitter)",
             "pro", [
        E("le propriétaire crée l'espace", "POST", "/api/workspaces",
          ok(lambda j: bool(j.get("id"))), corps={"name": "Cabinet Delta"},
          capture=lambda j, c: c.__setitem__("wid", j["id"])),
        E("il nomme A administrateur", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "admin"),
          corps=lambda c: {"email": c["email_a"], "role": "admin"},
          capture=lambda j, c: c.__setitem__("uid_a", j["user_id"])),
        E("il ajoute B comme membre", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"),
          corps=lambda c: {"email": c["email_b"], "role": "member"},
          capture=lambda j, c: c.__setitem__("uid_b", j["user_id"])),
        E("l'admin A voit les 3 membres", "GET", "/api/workspaces/{wid}/members",
          ok(lambda j: len(j["items"]) == 3), acteur="headers_a", role="admin A"),
        E("l'admin A retire B", "DELETE", "/api/workspaces/{wid}/members/{uid_b}",
          ok(lambda j: j.get("ok") is True), acteur="headers_a", role="admin A"),
        E("il ne reste que 2 membres", "GET", "/api/workspaces/{wid}/members",
          ok(lambda j: len(j["items"]) == 2)),
        E("le propriétaire rétrograde A en simple membre",
          "POST", "/api/workspaces/{wid}/members/{uid_a}/role",
          ok(lambda j: j.get("ok") is True), corps={"role": "member"}),
        E("A (désormais membre) NE peut PLUS ajouter", "POST", "/api/workspaces/{wid}/members",
          refuse(403), acteur="headers_a", role="A rétrogradé",
          corps=lambda c: {"email": c["email_b"], "role": "member"}),
        E("A quitte le cabinet", "POST", "/api/workspaces/{wid}/leave",
          ok(lambda j: j.get("ok") is True), acteur="headers_a", role="A rétrogradé"),
        E("le propriétaire reste seul", "GET", "/api/workspaces/{wid}/members",
          ok(lambda j: len(j["items"]) == 1)),
        E("le propriétaire supprime l'espace", "DELETE", "/api/workspaces/{wid}",
          ok(lambda j: j.get("ok") is True)),
    ], preparer=_pre_cab_gouvernance),
]

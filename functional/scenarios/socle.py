"""Couverture : socle entreprise/on-prem + fonctionnalités concurrentes ajoutées —
journal d'audit, rétention/purge, export RGPD, clés d'API, bibliothèque de prompts,
rédaction assistée (draft), revue tabulaire et chronologie (Vault)."""
from __future__ import annotations

from ._base import *


def _rbac_admin(verif) -> dict:
    return {"anonyme": refuse(401), "etudiant": refuse(403), "pro": refuse(403), "admin": ok(verif)}


def _pre_cle(banc: Banc, nom: str) -> tuple:
    """Crée une clé d'API et met son id dans le contexte."""
    c = banc.enregistrer(plan="pro")
    kid = banc.client.post("/api/keys", json={"name": "t"}, headers=c["headers"]).json()["id"]
    return c["headers"], {"kid": kid}


def _pre_doc(banc: Banc, nom: str) -> tuple:
    headers, ctx = banc.profil(nom)
    doc_id = banc.deposer_doc(
        headers, "aff.txt",
        b"Le 12 mars 2020, licenciement. Contrat de travail. Par ces motifs, fait droit et "
        b"condamne a payer 1.500,00 EUR. Le 03/04/2021, appel.")
    return headers, {**ctx, "doc_id": doc_id}


CAS = [
    # ===== Journal d'audit (admin) =====
    CasUsage("audit-rbac", "Socle — journal d'audit",
             "GET /api/admin/audit : trace qui/quoi/quand, réservé aux admins.",
             "GET", "/api/admin/audit", _rbac_admin(lambda j: "items" in j)),
    CasUsage("purge-rbac", "Socle — rétention/purge",
             "POST /api/admin/purge : purge au-delà de N jours, réservé aux admins.",
             "POST", "/api/admin/purge", _rbac_admin(lambda j: "deleted" in j),
             corps={"days": 3650}),
    CasUsage("purge-validation", "Socle — rétention/purge",
             "days hors bornes → 422.",
             "POST", "/api/admin/purge", {"admin": refuse(422)}, corps={"days": 0}),

    # ===== Export RGPD =====
    CasUsage("export-rgpd", "Socle — export RGPD",
             "GET /api/me/export : portabilité des données de l'utilisateur.",
             "GET", "/api/me/export",
             {"anonyme": refuse(401),
              "etudiant": ok(lambda j: "user" in j and "history" in j and "vault_documents" in j),
              "pro": ok(lambda j: j["user"]["plan"] == "pro")}),

    # ===== Clés d'API =====
    CasUsage("cle-create", "Socle — clés d'API",
             "POST /api/keys : la valeur (jlx_…) n'est montrée qu'une fois.",
             "POST", "/api/keys",
             {"anonyme": refuse(401),
              "pro": ok(lambda j: j["key"].startswith("jlx_") and bool(j["prefix"]))},
             corps={"name": "intégration"}),
    CasUsage("cle-list", "Socle — clés d'API",
             "GET /api/keys : liste sans la valeur en clair.",
             "GET", "/api/keys",
             {"anonyme": refuse(401), "pro": ok(lambda j: "items" in j)}),
    CasUsage("cle-revoke", "Socle — clés d'API",
             "DELETE /api/keys/{id} : révocation.",
             "DELETE", "/api/keys/{kid}", {"pro": ok(lambda j: j.get("ok") is True)},
             preparer=_pre_cle),
    CasUsage("cle-revoke-inconnue", "Socle — clés d'API",
             "Révoquer une clé inexistante → 404.",
             "DELETE", "/api/keys/999999", {"pro": refuse(404)}),

    # ===== Bibliothèque de prompts =====
    CasUsage("prompt-create", "Socle — bibliothèque de prompts",
             "POST /api/prompts : prompt personnel.",
             "POST", "/api/prompts",
             {"anonyme": refuse(401),
              "pro": ok(lambda j: j["scope"] == "perso" and bool(j.get("id")))},
             corps={"title": "Résumé", "body": "Résume ceci"}),
    CasUsage("prompt-list", "Socle — bibliothèque de prompts",
             "GET /api/prompts : prompts visibles (perso + cabinet).",
             "GET", "/api/prompts",
             {"anonyme": refuse(401), "pro": ok(lambda j: "items" in j)}),
    CasUsage("prompt-cabinet-etranger", "Socle — bibliothèque de prompts",
             "Créer un prompt cabinet dans un espace dont on n'est pas membre → 404.",
             "POST", "/api/prompts", {"pro": refuse(404)},
             corps={"title": "X", "body": "Y", "workspace_id": 999999}),

    # ===== Rédaction assistée (draft) =====
    CasUsage("draft-sourced", "Concurrence — rédaction assistée (draft)",
             "POST /api/draft : document sourcé sur le corpus officiel.",
             "POST", "/api/draft",
             {"anonyme": refuse(401),
              "pro": ok(lambda j: j["refused"] is False and bool(j["citations"]))},
             corps={"instruction": "Rédige une mise en demeure pour loyers impayés"}),
    CasUsage("draft-validation", "Concurrence — rédaction assistée (draft)",
             "instruction vide → 422.",
             "POST", "/api/draft", {"pro": refuse(422)}, corps={"instruction": ""}),

    # ===== Revue tabulaire + chronologie (Vault) =====
    CasUsage("vault-review", "Concurrence — revue tabulaire (Vault)",
             "POST /api/vault/review : 1 doc = 1 ligne, colonnes extraites.",
             "POST", "/api/vault/review",
             {"pro": ok(lambda j: j["rows"][0]["matter"] == "Droit du travail"
                        and "1.500,00 EUR" in j["rows"][0]["amounts"])},
             corps=lambda ctx: {"doc_ids": [ctx["doc_id"]]}, preparer=_pre_doc),
    CasUsage("vault-review-auth", "Concurrence — revue tabulaire (Vault)",
             "Revue sans authentification → 401.",
             "POST", "/api/vault/review", {"anonyme": refuse(401)}, corps={"doc_ids": [1]}),
    CasUsage("vault-timeline", "Concurrence — chronologie (Vault)",
             "task=timeline : dates + contexte, déterministe.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {"pro": ok(lambda j: j["task"] == "timeline" and len(j["events"]) >= 2)},
             corps={"task": "timeline"}, preparer=_pre_doc),
]


# ===== A10/A12 : observabilité + paramétrage runtime (admin) =====
CAS += [
    CasUsage("admin-health", "Socle — observabilité",
             "GET /api/admin/health : dépendances + volumétrie + routage LLM, admins.",
             "GET", "/api/admin/health",
             _rbac_admin(lambda j: j["meilisearch"] is True and "counts" in j and "llm_routing" in j)),
    CasUsage("config-get", "Socle — paramétrage runtime",
             "GET /api/admin/config : réglages non-secrets modifiables.",
             "GET", "/api/admin/config",
             _rbac_admin(lambda j: "config" in j and "rate_limit_per_min" in j["modifiables"])),
    CasUsage("config-patch", "Socle — paramétrage runtime",
             "PATCH /api/admin/config : applique un réglage, ignore les clés hors liste.",
             "PATCH", "/api/admin/config", {"admin": ok(lambda j: j["applied"].get("rate_limit_per_min") == 42)},
             corps={"values": {"rate_limit_per_min": 42, "anthropic_api_key": "x"}}),
]


# ===== A8 : cloisons déontologiques (parcours multi-acteurs) =====
def _pre_cloison(banc: Banc) -> dict:
    owner = banc.enregistrer(plan="pro")
    membre = banc.enregistrer()
    wid = banc.creer_espace(owner["headers"])
    banc.ajouter_membre(owner["headers"], wid, membre["email"], "member")
    did = banc.creer_dossier(owner["headers"], wid)
    return {"headers": owner["headers"], "headers_membre": membre["headers"],
            "email_membre": membre["email"], "did": did}


PARCOURS = [
    Parcours("cloison-deontologique", "Cloison déontologique : restreindre un dossier puis autoriser", "pro", [
        E("le membre voit le dossier (non restreint)", "GET", "/api/dossiers/{did}/items",
          ok(lambda j: "items" in j), acteur="headers_membre", role="membre"),
        E("l'owner restreint le dossier", "POST", "/api/dossiers/{did}/restrict",
          ok(lambda j: j["restricted"] is True), corps={"restricted": True}),
        E("le membre ne voit plus (404)", "GET", "/api/dossiers/{did}/items",
          refuse(404), acteur="headers_membre", role="membre"),
        E("l'owner garde l'accès", "GET", "/api/dossiers/{did}/items", ok(lambda j: "items" in j)),
        E("l'owner autorise nommément le membre", "POST", "/api/dossiers/{did}/access",
          ok(lambda j: bool(j.get("user_id"))), corps=lambda c: {"email": c["email_membre"]}),
        E("le membre revoit le dossier", "GET", "/api/dossiers/{did}/items",
          ok(lambda j: "items" in j), acteur="headers_membre", role="membre"),
        E("un membre ne peut pas gérer la cloison", "POST", "/api/dossiers/{did}/restrict",
          refuse(403), acteur="headers_membre", role="membre", corps={"restricted": False}),
    ], preparer=_pre_cloison),
]


# ===== B9 : revue de contrats + playbooks =====
def _pre_playbook(banc: Banc, nom: str) -> tuple:
    headers, ctx = banc.profil(nom)
    pb = banc.client.post("/api/playbooks", json={"name": "CDI", "rules": [
        {"label": "Loi applicable", "instruction": "Clause de loi applicable LU."},
        {"label": "Résiliation", "instruction": "Préavis."}]}, headers=headers).json()
    doc_id = banc.deposer_doc(headers, "contrat.txt", b"Contrat de travail entre les parties.")
    return headers, {**ctx, "pid": pb["id"], "doc_id": doc_id}


CAS += [
    CasUsage("playbook-create", "Concurrence — playbooks (revue de contrats)",
             "POST /api/playbooks : jeu de règles maison.",
             "POST", "/api/playbooks",
             {"anonyme": refuse(401),
              "pro": ok(lambda j: j["scope"] == "perso" and len(j["rules"]) == 2)},
             corps={"name": "CDI", "rules": [{"label": "Loi applicable", "instruction": "LU ?"},
                                             {"label": "Préavis", "instruction": "Durée ?"}]}),
    CasUsage("playbook-validation", "Concurrence — playbooks (revue de contrats)",
             "Playbook sans règle → 422.",
             "POST", "/api/playbooks", {"pro": refuse(422)}, corps={"name": "X", "rules": []}),
    CasUsage("playbook-list", "Concurrence — playbooks (revue de contrats)",
             "GET /api/playbooks : playbooks visibles.",
             "GET", "/api/playbooks", {"anonyme": refuse(401), "pro": ok(lambda j: "items" in j)}),
    CasUsage("contract-review", "Concurrence — revue de contrats",
             "POST /api/vault/documents/{id}/review-contract : verdict par règle (ok/issue/missing).",
             "POST", "/api/vault/documents/{doc_id}/review-contract",
             {"pro": ok(lambda j: j["task"] == "contract" and j["summary"]["total"] == 2)},
             corps=lambda c: {"playbook_id": c["pid"]}, preparer=_pre_playbook),
    CasUsage("contract-review-playbook-inconnu", "Concurrence — revue de contrats",
             "Playbook inexistant → 404.",
             "POST", "/api/vault/documents/{doc_id}/review-contract",
             {"pro": refuse(404)}, corps={"playbook_id": 999999}, preparer=_pre_playbook),
]

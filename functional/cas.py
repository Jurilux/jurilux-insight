"""Cas d'usage dérivés de la documentation (contrat d'API — `CLAUDE.md`).

Chaque cas est tracé à la fonctionnalité qu'il couvre et déclare, par profil, l'attente.
Deux dimensions de profils :
  - **compte** : anonyme / étudiant / pro / admin (plan + is_admin) ;
  - **rôle d'espace** ou **isolation** : provisionnés à la volée par un `preparer` dédié.
"""
from __future__ import annotations

from .banc import Banc
from .engine import CasUsage, gracieux, ok, refuse

from .scenarios import CAS as _CAS_DOMAINES  # cas par domaine (couverture étendue)

COMPTE = ["anonyme", "etudiant", "pro", "admin"]


# --------- préparateurs spécifiques (rôles d'espace, quota, isolation) ---------
def _prep_quota(banc: Banc, nom: str) -> tuple:
    """Étudiant : sature son quota avant l'appel → doit être refusé gracieusement."""
    headers, ctx = banc.profil(nom)
    if nom == "etudiant":
        banc.saturer_quota(ctx["uid"])
    return headers, ctx


def _prep_partage(banc: Banc, nom: str) -> tuple:
    """Crée un permalien (anonyme) puis renvoie les headers du profil qui va le lire."""
    sid = banc.creer_partage()
    headers, ctx = banc.profil(nom)
    return headers, {**ctx, "sid": sid}


def _prep_role_espace(banc: Banc, nom: str) -> tuple:
    """Chaque rôle provisionne SON espace isolé, avec l'acteur dans le rôle voulu, plus une
    cible à ajouter. Renvoie (headers de l'acteur, {wid, cible})."""
    proprio = banc.enregistrer()
    wid = banc.creer_espace(proprio["headers"])
    cible = banc.enregistrer()["email"]
    if nom == "proprietaire":
        return proprio["headers"], {"wid": wid, "cible": cible}
    if nom == "admin_espace":
        acteur = banc.enregistrer()
        banc.ajouter_membre(proprio["headers"], wid, acteur["email"], "admin")
        return acteur["headers"], {"wid": wid, "cible": cible}
    if nom == "membre":
        acteur = banc.enregistrer()
        banc.ajouter_membre(proprio["headers"], wid, acteur["email"], "member")
        return acteur["headers"], {"wid": wid, "cible": cible}
    # "etranger" : non membre de l'espace
    return banc.enregistrer()["headers"], {"wid": wid, "cible": cible}


def _prep_isolation_vault(banc: Banc, nom: str) -> tuple:
    """Le propriétaire dépose un document ; l'intrus tente d'y accéder."""
    proprio = banc.enregistrer()
    doc_id = banc.deposer_doc(proprio["headers"], "prive.txt", b"Vu l'article L.124-10.")
    if nom == "proprietaire":
        return proprio["headers"], {"doc_id": doc_id}
    return banc.enregistrer()["headers"], {"doc_id": doc_id}  # intrus


def _prep_doc_citations(banc: Banc, nom: str) -> tuple:
    headers, ctx = banc.profil(nom)
    doc_id = banc.deposer_doc(headers, "concl.txt",
                              b"Vu l'article L.124-10 du Code du travail et l'article 9999 inexistant.")
    return headers, {**ctx, "doc_id": doc_id}


def _prep_doc_extract(banc: Banc, nom: str) -> tuple:
    headers, ctx = banc.profil(nom)
    corps = (b"Pour le demandeur, Maitre Jean DUPONT a plaide. Licenciement et contrat de travail. "
             b"Par ces motifs, fait droit a la demande et condamne a payer 1.500,00 EUR.")
    doc_id = banc.deposer_doc(headers, "piece.txt", corps)
    return headers, {**ctx, "doc_id": doc_id}


# --------- catalogue des cas d'usage ---------
CAS = [
    # === Santé & périmètre (public) ===
    CasUsage("health-ok", "Santé (/health)",
             "503 si Meili down ou clé LLM absente ; sinon 200 status=ok.",
             "GET", "/health",
             {p: ok(lambda j: j["status"] == "ok") for p in COMPTE}),
    CasUsage("corpus-public", "Corpus (/api/corpus)",
             "Périmètre du corpus, public (affichage front).",
             "GET", "/api/corpus",
             {p: ok(lambda j: j.get("decisions") is not None) for p in COMPTE}),

    # === Compte utilisateur ===
    CasUsage("me-auth", "Compte (/api/me, /api/history)",
             "GET /api/me : {user:{email,plan,is_admin}, quota}. Auth requise.",
             "GET", "/api/me",
             {"anonyme": refuse(401),
              "etudiant": ok(lambda j: j["user"]["plan"] == "student" and j["user"]["is_admin"] is False),
              "pro": ok(lambda j: j["user"]["plan"] == "pro"),
              "admin": ok(lambda j: j["user"]["is_admin"] is True)}),
    CasUsage("history-auth", "Compte (/api/me, /api/history)",
             "GET /api/history → {items}. Auth requise.",
             "GET", "/api/history",
             {"anonyme": refuse(401), "etudiant": ok(lambda j: "items" in j),
              "pro": ok(lambda j: "items" in j), "admin": ok(lambda j: "items" in j)}),

    # === RAG /api/ask ===
    CasUsage("ask-sourced", "RAG (/api/ask)",
             "Réponse sourcée (answer + citations, refused=false) pour tout profil.",
             "POST", "/api/ask", {p: ok(lambda j: j["answer"] and j["citations"] and j["refused"] is False)
                                  for p in COMPTE},
             corps={"q": "faute grave et licenciement"}),
    CasUsage("ask-quota", "RAG (/api/ask)",
             "Quota mensuel étudiant : refus gracieux au-delà ; pro illimité.",
             "POST", "/api/ask",
             {"anonyme": ok(lambda j: j["refused"] is False),
              "etudiant": gracieux(),
              "pro": ok(lambda j: j["refused"] is False),
              "admin": ok(lambda j: j["refused"] is False)},
             corps={"q": "question quota"}, preparer=_prep_quota),

    # === Feedback & partage (publics) ===
    CasUsage("feedback-public", "Feedback (/api/feedback)",
             "Retour 👍/👎 ouvert aux anonymes ; rattaché au compte si connecté.",
             "POST", "/api/feedback", {p: ok(lambda j: j.get("ok") is True) for p in COMPTE},
             corps={"question": "Q ?", "helpful": True, "status": "ok"}),
    CasUsage("share-create", "Partage (/api/share)",
             "Crée un permalien (ouvert aux anonymes) → {id}.",
             "POST", "/api/share", {p: ok(lambda j: bool(j.get("id"))) for p in COMPTE},
             corps={"question": "Q ?", "answer": "A", "status": "ok"}),
    CasUsage("share-read", "Partage (/api/share)",
             "Lecture publique d'un permalien : accessible à tous.",
             "GET", "/api/share/{sid}", {p: ok(lambda j: j.get("question") is not None) for p in COMPTE},
             preparer=_prep_partage),

    # === Insight avocats (PUBLIC) ===
    CasUsage("insight-stats", "Insight avocats (public)",
             "GET /api/insight/stats : public, jamais de magistrats.",
             "GET", "/api/insight/stats", {p: ok(lambda j: j.get("lawyers", 0) >= 1) for p in COMPTE}),
    CasUsage("insight-lawyers", "Insight avocats (public)",
             "GET /api/insight/lawyers : liste publique des avocats.",
             "GET", "/api/insight/lawyers", {p: ok(lambda j: "items" in j) for p in COMPTE}),

    # === Cabinet : création + RBAC des rôles d'espace ===
    CasUsage("ws-create", "Cabinet (/api/workspaces)",
             "Créer un espace : auth requise.",
             "POST", "/api/workspaces",
             {"anonyme": refuse(401), "etudiant": ok(lambda j: bool(j.get("id"))),
              "pro": ok(lambda j: bool(j.get("id"))), "admin": ok(lambda j: bool(j.get("id")))},
             corps={"name": "Cabinet Test"}),
    CasUsage("ws-add-member", "Cabinet — rôles d'espace",
             "Ajout de membre : owner/admin autorisés, member interdit (403), non-membre 404.",
             "POST", "/api/workspaces/{wid}/members",
             {"proprietaire": ok(lambda j: j.get("role") == "member"),
              "admin_espace": ok(lambda j: j.get("role") == "member"),
              "membre": refuse(403),
              "etranger": refuse(404)},
             corps=lambda ctx: {"email": ctx["cible"], "role": "member"},
             preparer=_prep_role_espace),

    # === Veille / alertes ===
    CasUsage("alert-create", "Veille (/api/alerts)",
             "Créer une alerte : auth requise.",
             "POST", "/api/alerts",
             {"anonyme": refuse(401), "etudiant": ok(lambda j: bool(j.get("id"))),
              "pro": ok(lambda j: bool(j.get("id"))), "admin": ok(lambda j: bool(j.get("id")))},
             corps={"query": "licenciement abusif"}),

    # === Backoffice (gate is_admin) ===
    CasUsage("admin-overview", "Backoffice (gate is_admin)",
             "GET /api/admin/overview : réservé aux admins (401 anonyme, 403 non-admin).",
             "GET", "/api/admin/overview",
             {"anonyme": refuse(401), "etudiant": refuse(403), "pro": refuse(403),
              "admin": ok(lambda j: isinstance(j, dict))}),
    CasUsage("admin-users", "Backoffice (gate is_admin)",
             "GET /api/admin/users : réservé aux admins.",
             "GET", "/api/admin/users",
             {"anonyme": refuse(401), "etudiant": refuse(403), "pro": refuse(403),
              "admin": ok(lambda j: "items" in j or isinstance(j, list))}),

    # === Vault : dépôt, Q&A hybride, isolation, analyses locales ===
    CasUsage("vault-upload", "Vault (documents privés)",
             "Dépôt d'un document : auth requise ; statut ready après indexation.",
             "POST", "/api/vault/documents?filename=note.txt",
             {"anonyme": refuse(401),
              "etudiant": ok(lambda j: j.get("status") == "ready"),
              "pro": ok(lambda j: j.get("status") == "ready"),
              "admin": ok(lambda j: j.get("status") == "ready")},
             contenu=b"un contenu de test", entetes={"Content-Type": "text/plain"}),
    CasUsage("vault-ask", "Vault (documents privés)",
             "Q&A sourcé isolé sur le Vault.",
             "POST", "/api/vault/ask",
             {"anonyme": refuse(401),
              "etudiant": ok(lambda j: bool(j.get("answer")) and j["refused"] is False),
              "pro": ok(lambda j: j["refused"] is False),
              "admin": ok(lambda j: j["refused"] is False)},
             corps={"q": "quel délai ?"}),
    CasUsage("vault-isolation", "Vault — isolation stricte",
             "Un utilisateur n'atteint jamais le document d'un autre (analyze → 404).",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {"proprietaire": ok(lambda j: j.get("task") == "citations"),
              "intrus": refuse(404)},
             corps={"task": "citations"}, preparer=_prep_isolation_vault),
    CasUsage("vault-citations", "Vault — vérificateur de citations",
             "Extrait les références et les vérifie contre le corpus officiel.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {"etudiant": ok(lambda j: j["total"] == 2 and j["verified"] == 1)},
             corps={"task": "citations"}, preparer=_prep_doc_citations),
    CasUsage("vault-extract", "Vault — extraction structurée",
             "Extraction locale/déterministe : matière, montants, avocats, issue.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {"pro": ok(lambda j: j["matter"] == "Droit du travail" and "1.500,00 EUR" in j["amounts"])},
             corps={"task": "extract"}, preparer=_prep_doc_extract),
]

# Couverture matrice = cas historiques (ci-dessus) + cas par domaine (paquet scenarios).
CAS = CAS + _CAS_DOMAINES

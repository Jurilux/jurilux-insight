"""Parcours utilisateur réalistes : objectif clair, séquence d'étapes enchaînées (l'état
se transmet — token, id d'espace/dossier/document…), plusieurs acteurs par parcours, et
évaluation étape par étape puis pour le parcours entier.

Chaque parcours reflète un usage concret du produit, pas un endpoint isolé.
"""
from __future__ import annotations

from .banc import Banc
from .engine import Etape as E
from .engine import Parcours, gracieux, ok, refuse
from .scenarios import PARCOURS as _PARCOURS_DOMAINES


# ============================ préparateurs (montage multi-acteurs) ============================
def _pre_pro_seul(banc: Banc) -> dict:
    c = banc.enregistrer(plan="pro")
    return {"headers": c["headers"], "uid": c["uid"], "email": c["email"]}


def _pre_pro_plus_collegue(banc: Banc) -> dict:
    pro = banc.enregistrer(plan="pro")
    collegue = banc.enregistrer()
    tiers = banc.enregistrer()
    return {"headers": pro["headers"], "email": pro["email"],
            "headers_collegue": collegue["headers"], "email_collegue": collegue["email"],
            "email_tiers": tiers["email"]}


def _pre_cabinet(banc: Banc) -> dict:
    owner = banc.enregistrer(plan="pro")
    adm = banc.enregistrer()
    mbr = banc.enregistrer()
    etr = banc.enregistrer()
    c1 = banc.enregistrer()
    c2 = banc.enregistrer()
    return {"headers": owner["headers"],
            "headers_admin": adm["headers"], "email_admin": adm["email"],
            "headers_membre": mbr["headers"], "email_membre": mbr["email"],
            "headers_etranger": etr["headers"],
            "email_c1": c1["email"], "email_c2": c2["email"]}


def _pre_vault(banc: Banc) -> dict:
    pro = banc.enregistrer(plan="pro")
    intrus = banc.enregistrer()
    return {"headers": pro["headers"], "headers_intrus": intrus["headers"]}


def _pre_admin(banc: Banc) -> dict:
    adm = banc.enregistrer(admin=True)
    cible = banc.enregistrer()
    return {"headers": adm["headers"], "uid_cible": cible["uid"], "headers_cible": cible["headers"]}


# ============================ catalogue des parcours ============================
PARCOURS = [
    # --- 1. Étudiant freemium : de l'usage nominal jusqu'au mur du quota ---
    Parcours("etudiant-freemium", "Étudiant freemium : usage nominal puis mur du quota", "etudiant", [
        E("consulte son compte", "GET", "/api/me", ok(lambda j: j["user"]["plan"] == "student")),
        E("pose sa 1re question", "POST", "/api/ask", ok(lambda j: j["answer"] and j["refused"] is False),
          corps={"q": "préavis de licenciement au Luxembourg"}),
        E("retrouve la question dans l'historique", "GET", "/api/history",
          ok(lambda j: len(j["items"]) >= 1)),
        E("note la réponse (feedback 👍)", "POST", "/api/feedback", ok(lambda j: j.get("ok") is True),
          corps={"question": "préavis ?", "helpful": True, "status": "ok"}),
        E("question 2", "POST", "/api/ask", ok(lambda j: j["refused"] is False), corps={"q": "faute grave ?"}),
        E("question 3", "POST", "/api/ask", ok(lambda j: j["refused"] is False), corps={"q": "période d'essai ?"}),
        E("question 4", "POST", "/api/ask", ok(lambda j: j["refused"] is False), corps={"q": "heures sup ?"}),
        E("question 5 (quota atteint)", "POST", "/api/ask", ok(lambda j: j["refused"] is False),
          corps={"q": "bail d'habitation ?"}),
        E("question 6 → refus gracieux (quota épuisé)", "POST", "/api/ask", gracieux(),
          corps={"q": "une question de trop"}),
        E("partage tout de même une réponse", "POST", "/api/share", ok(lambda j: bool(j.get("id"))),
          corps={"question": "préavis ?", "answer": "…", "status": "ok"},
          capture=lambda j, c: c.__setitem__("sid", j["id"])),
        E("le permalien est lisible publiquement", "GET", "/api/share/{sid}",
          ok(lambda j: j.get("question") is not None)),
    ]),

    # --- 2. Avocat pro : recherche → dossier partagé avec un collègue ---
    Parcours("pro-dossier", "Avocat pro : recherche sourcée archivée dans un dossier partagé", "pro", [
        E("crée un espace de travail", "POST", "/api/workspaces",
          ok(lambda j: bool(j.get("id"))), corps={"name": "Cabinet Alpha"},
          capture=lambda j, c: c.__setitem__("wid", j["id"])),
        E("l'espace apparaît dans sa liste", "GET", "/api/workspaces",
          ok(lambda j: len(j["items"]) >= 1)),
        E("crée un dossier", "POST", "/api/workspaces/{wid}/dossiers",
          ok(lambda j: bool(j.get("id"))), corps={"name": "Affaire Dupont"},
          capture=lambda j, c: c.__setitem__("did", j["id"])),
        E("pose une question de recherche", "POST", "/api/ask",
          ok(lambda j: bool(j["answer"])), corps={"q": "licenciement avec effet immédiat"},
          capture=lambda j, c: c.__setitem__("rep", j)),
        E("archive la réponse dans le dossier", "POST", "/api/dossiers/{did}/items",
          ok(lambda j: bool(j.get("id"))),
          corps=lambda c: {"question": "licenciement immédiat ?", "answer": c["rep"]["answer"],
                           "citations": [dict(x) for x in c["rep"]["citations"]], "status": "ok"}),
        E("le dossier contient bien l'élément", "GET", "/api/dossiers/{did}/items",
          ok(lambda j: len(j["items"]) == 1)),
        E("invite un collègue (membre)", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"),
          corps=lambda c: {"email": c["email_collegue"], "role": "member"}),
        E("le collègue voit les dossiers", "GET", "/api/workspaces/{wid}/dossiers",
          ok(lambda j: len(j["items"]) >= 1), acteur="headers_collegue", role="collègue"),
        E("le collègue ne peut PAS supprimer l'espace", "DELETE", "/api/workspaces/{wid}",
          refuse(403), acteur="headers_collegue", role="collègue"),
        E("le propriétaire supprime l'espace", "DELETE", "/api/workspaces/{wid}", ok()),
    ], preparer=_pre_pro_plus_collegue),

    # --- 3. Cabinet : cycle de vie des rôles et permissions ---
    Parcours("cabinet-roles", "Cabinet : cycle de vie des rôles (owner/admin/membre/étranger)", "pro", [
        E("owner crée l'espace", "POST", "/api/workspaces", ok(lambda j: bool(j.get("id"))),
          corps={"name": "Cabinet Beta"}, capture=lambda j, c: c.__setitem__("wid", j["id"])),
        E("owner nomme un admin", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "admin"),
          corps=lambda c: {"email": c["email_admin"], "role": "admin"}),
        E("owner ajoute un membre", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"),
          corps=lambda c: {"email": c["email_membre"], "role": "member"},
          capture=lambda j, c: c.__setitem__("uid_membre", j["user_id"])),
        E("l'admin peut ajouter un collaborateur", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"), acteur="headers_admin", role="admin_espace",
          corps=lambda c: {"email": c["email_c1"], "role": "member"}),
        E("le membre NE peut PAS ajouter", "POST", "/api/workspaces/{wid}/members",
          refuse(403), acteur="headers_membre", role="membre",
          corps=lambda c: {"email": c["email_c2"], "role": "member"}),
        E("owner promeut le membre en admin", "POST", "/api/workspaces/{wid}/members/{uid_membre}/role",
          ok(lambda j: j.get("ok") is True), corps={"role": "admin"}),
        E("le membre promu peut désormais ajouter", "POST", "/api/workspaces/{wid}/members",
          ok(lambda j: j.get("role") == "member"), acteur="headers_membre", role="membre→admin",
          corps=lambda c: {"email": c["email_c2"], "role": "member"}),
        E("un étranger ne voit pas l'espace", "GET", "/api/workspaces/{wid}/members",
          refuse(404), acteur="headers_etranger", role="étranger"),
        E("le membre promu quitte le cabinet", "POST", "/api/workspaces/{wid}/leave",
          ok(lambda j: j.get("ok") is True), acteur="headers_membre", role="membre→admin"),
    ], preparer=_pre_cabinet),

    # --- 4. Veille juridique : créer, vérifier, consulter, supprimer ---
    Parcours("veille", "Veille juridique : cycle de vie d'une alerte", "pro", [
        E("crée une alerte", "POST", "/api/alerts", ok(lambda j: bool(j.get("id"))),
          corps={"query": "licenciement abusif"}, capture=lambda j, c: c.__setitem__("aid", j["id"])),
        E("l'alerte apparaît dans la liste", "GET", "/api/alerts", ok(lambda j: len(j["items"]) == 1)),
        E("vérifie l'alerte", "POST", "/api/alerts/{aid}/check", ok(lambda j: "new" in j)),
        E("consulte les décisions remontées", "GET", "/api/alerts/{aid}/hits", ok(lambda j: "items" in j)),
        E("supprime l'alerte", "DELETE", "/api/alerts/{aid}", ok(lambda j: j.get("ok") is True)),
        E("la liste est de nouveau vide", "GET", "/api/alerts", ok(lambda j: j["items"] == [])),
    ], preparer=_pre_pro_seul),

    # --- 5. Vault : dépôt → analyses locales → Q&A isolé & hybride → isolation → purge ---
    Parcours("vault-complet", "Vault : dépôt, analyses, Q&A hybride, isolation et purge", "pro", [
        E("dépose un document", "POST", "/api/vault/documents?filename=concl.txt",
          ok(lambda j: j.get("status") == "ready"),
          contenu=(b"Pour le demandeur, Maitre Jean DUPONT. Licenciement et contrat de travail. "
                   b"Par ces motifs, fait droit et condamne a payer 1.500,00 EUR. Vu l'article L.124-10."),
          entetes={"Content-Type": "text/plain"},
          capture=lambda j, c: c.__setitem__("doc", j["id"])),
        E("le document est listé, sans son texte", "GET", "/api/vault/documents",
          ok(lambda j: len(j["items"]) == 1 and "text" not in j["items"][0])),
        E("vérifie les citations contre le corpus", "POST", "/api/vault/documents/{doc}/analyze",
          ok(lambda j: j["verified"] >= 1), corps={"task": "citations"}),
        E("extrait la structure (matière, montant)", "POST", "/api/vault/documents/{doc}/analyze",
          ok(lambda j: j["matter"] == "Droit du travail" and "1.500,00 EUR" in j["amounts"]),
          corps={"task": "extract"}),
        E("Q&A isolé sur son document", "POST", "/api/vault/ask",
          ok(lambda j: bool(j["answer"]) and j["refused"] is False),
          corps=lambda c: {"q": "quel montant ?", "doc_ids": [c["doc"]]}),
        E("Q&A hybride privé + corpus public", "POST", "/api/vault/ask",
          ok(lambda j: j["refused"] is False), corps={"q": "quel article ?", "include_corpus": True}),
        E("un intrus ne peut PAS l'analyser", "POST", "/api/vault/documents/{doc}/analyze",
          refuse(404), acteur="headers_intrus", role="intrus", corps={"task": "citations"}),
        E("le propriétaire purge le document", "DELETE", "/api/vault/documents/{doc}",
          ok(lambda j: j.get("ok") is True)),
        E("le Vault est vide", "GET", "/api/vault/documents", ok(lambda j: j["items"] == [])),
    ], preparer=_pre_vault),

    # --- 6. Administrateur : supervision de bout en bout ---
    Parcours("admin-supervision", "Administrateur : supervision et gouvernance", "admin", [
        E("consulte la vue d'ensemble", "GET", "/api/admin/overview", ok(lambda j: isinstance(j, dict))),
        E("liste les utilisateurs", "GET", "/api/admin/users", ok(lambda j: len(j["items"]) >= 1)),
        E("passe un utilisateur en pro", "POST", "/api/admin/users/{uid_cible}/plan",
          ok(lambda j: j.get("ok") is True), corps={"plan": "pro"}),
        E("consulte l'activité par jour", "GET", "/api/admin/activity", ok(lambda j: "per_day" in j)),
        E("consulte les questions récentes", "GET", "/api/admin/questions", ok(lambda j: "items" in j)),
        E("inspecte le retrieval (probe)", "POST", "/api/admin/probe",
          ok(lambda j: "hits" in j), corps={"q": "faute grave"}),
        E("lance le banc d'évaluation", "GET", "/api/admin/eval", ok(lambda j: j["total"] == 10)),
        E("un non-admin est refusé", "GET", "/api/admin/overview", refuse(403),
          acteur="headers_cible", role="non-admin"),
    ], preparer=_pre_admin),

    # --- 7. Sécurité : cycle de session (mot de passe, déconnexion, reconnexion) ---
    Parcours("securite-session", "Sécurité : changement de mot de passe et cycle de session", "etudiant", [
        E("change son mot de passe", "POST", "/api/auth/change-password", ok(lambda j: j.get("ok") is True),
          corps={"old_password": "password123", "new_password": "nouveauMDP2026"}),
        E("se déconnecte", "POST", "/api/auth/logout", ok(lambda j: j.get("ok") is True)),
        E("l'ancien jeton est invalidé", "GET", "/api/me", refuse(401)),
        E("se reconnecte avec le nouveau mot de passe", "POST", "/api/auth/login",
          ok(lambda j: bool(j.get("token"))),
          corps=lambda c: {"email": c["email"], "password": "nouveauMDP2026"},
          capture=lambda j, c: c.__setitem__("headers", {"Authorization": f"Bearer {j['token']}"})),
        E("accède de nouveau à son compte", "GET", "/api/me", ok(lambda j: bool(j["user"]["email"]))),
    ]),

    # --- 8. Anonyme : parcours de découverte sans compte ---
    Parcours("anonyme-decouverte", "Anonyme : découverte du produit sans compte", "anonyme", [
        E("vérifie l'état du service", "GET", "/health", ok(lambda j: j["status"] == "ok")),
        E("consulte le périmètre du corpus", "GET", "/api/corpus", ok(lambda j: j["decisions"] is not None)),
        E("pose une question sans compte", "POST", "/api/ask",
          ok(lambda j: bool(j["answer"])), corps={"q": "quel est le préavis légal ?"}),
        E("explore l'insight avocats (public)", "GET", "/api/insight/stats",
          ok(lambda j: j["lawyers"] >= 1)),
        E("liste les avocats", "GET", "/api/insight/lawyers", ok(lambda j: len(j["items"]) >= 1),
          capture=lambda j, c: c.__setitem__("key", j["items"][0]["name_key"])),
        E("ouvre un profil d'avocat", "GET", "/api/insight/lawyers/{key}",
          ok(lambda j: bool(j.get("name")))),
        E("laisse un feedback", "POST", "/api/feedback", ok(lambda j: j.get("ok") is True),
          corps={"question": "préavis ?", "helpful": True}),
        E("crée un permalien", "POST", "/api/share", ok(lambda j: bool(j.get("id"))),
          corps={"question": "préavis ?", "answer": "…"}, capture=lambda j, c: c.__setitem__("sid", j["id"])),
        E("le relit publiquement", "GET", "/api/share/{sid}", ok(lambda j: j.get("question") is not None)),
        E("son espace privé lui est fermé", "GET", "/api/me", refuse(401)),
        E("le backoffice lui est fermé", "GET", "/api/admin/overview", refuse(401)),
    ]),
]

# Parcours = parcours cœur (ci-dessus) + parcours par domaine (paquet scenarios).
PARCOURS = PARCOURS + _PARCOURS_DOMAINES

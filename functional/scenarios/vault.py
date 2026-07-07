"""Couverture ÉTENDUE du Vault (documents privés déposés par l'utilisateur).

Dépôt (corps brut), liste (sans le texte), suppression, Q&A isolé/ciblé/hybride/vide,
et analyses locales déterministes (vérificateur de citations + extraction structurée).
On insiste ici sur les **branches d'erreur** (401/404/413/422), l'hybride et le vide,
qui complètent les quelques cas nominaux couverts ailleurs.

Isolation stricte : un utilisateur n'atteint jamais le document d'un autre (→ 404, jamais
403 : on ne révèle même pas l'existence du document).
"""
from __future__ import annotations

from ._base import *

# Contenu de référence pour les analyses : une matière (« droit du travail »), un montant
# normalisable et une référence d'article résolue par le corpus stub (« 124-10 »).
_CONTENU_ANALYSE = (
    b"Pour le demandeur, Maitre Jean DUPONT. Licenciement et contrat de travail. "
    b"Par ces motifs, fait droit et condamne a payer 1.500,00 EUR. Vu l'article L.124-10."
)


# --------- preparers (provisionnement des documents et des acteurs) ---------
def _pre_avec_doc(banc: Banc, nom: str) -> tuple:
    """Le profil courant dépose un document et reçoit son doc_id dans le contexte."""
    headers, ctx = banc.profil(nom)
    doc_id = banc.deposer_doc(headers, "note.txt", b"contenu de test du vault")
    return headers, {**ctx, "doc_id": doc_id}


def _pre_doc_analyse(banc: Banc, nom: str) -> tuple:
    """Le profil courant dépose le document de référence (matière + montant + article)."""
    headers, ctx = banc.profil(nom)
    doc_id = banc.deposer_doc(headers, "concl.txt", _CONTENU_ANALYSE)
    return headers, {**ctx, "doc_id": doc_id}


def _pre_doc_inconnu(banc: Banc, nom: str) -> tuple:
    """Profil authentifié sans document : on vise un id inexistant (999999)."""
    headers, ctx = banc.profil(nom)
    return headers, {**ctx, "doc_id": 999999}


def _pre_doc_autrui(banc: Banc, nom: str) -> tuple:
    """Un propriétaire (A) dépose ; l'acteur (B, l'intrus) tente d'y accéder → 404."""
    proprio = banc.enregistrer()
    doc_id = banc.deposer_doc(proprio["headers"], "prive.txt", _CONTENU_ANALYSE)
    intrus = banc.enregistrer()
    return intrus["headers"], {"doc_id": doc_id}


def _pre_doc_autrui_anonyme(banc: Banc, nom: str) -> tuple:
    """Un propriétaire dépose ; l'acteur est anonyme (pas de jeton) → 401 avant tout."""
    proprio = banc.enregistrer()
    doc_id = banc.deposer_doc(proprio["headers"], "prive.txt", b"contenu prive")
    return {}, {"doc_id": doc_id}


CAS = [
    # === Dépôt (POST /api/vault/documents?filename=…, corps brut) ===
    CasUsage("vlt-upload-anon", "Vault — dépôt",
             "Dépôt sans authentification → 401.",
             "POST", "/api/vault/documents?filename=note.txt",
             {"anonyme": refuse(401)},
             contenu=b"contenu prive", entetes={"Content-Type": "text/plain"}),
    CasUsage("vlt-upload-ok", "Vault — dépôt",
             "Dépôt authentifié → {id, status:'ready', n_chunks}.",
             "POST", "/api/vault/documents?filename=note.txt",
             {p: ok(lambda j: j["status"] == "ready" and "id" in j and "n_chunks" in j)
              for p in AUTHENTIFIES},
             contenu=b"une note juridique de test", entetes={"Content-Type": "text/plain"}),
    CasUsage("vlt-upload-trop-gros", "Vault — dépôt",
             "Fichier au-delà de la borne de taille → 413 (garde-fou anti-OOM).",
             "POST", "/api/vault/documents?filename=gros.txt",
             {"pro": refuse(413)},
             contenu=b"contenu bien plus long que cinq octets",
             entetes={"Content-Type": "text/plain"},
             stubs=[(MAIN, "_VAULT_MAX_BYTES", 5)]),

    # === Liste (GET /api/vault/documents) — jamais le texte brut ===
    CasUsage("vlt-list-anon", "Vault — liste",
             "Liste sans authentification → 401.",
             "GET", "/api/vault/documents", {"anonyme": refuse(401)}),
    CasUsage("vlt-list-ok", "Vault — liste",
             "Liste du propriétaire → {items} contenant le doc déposé, sans champ 'text'.",
             "GET", "/api/vault/documents",
             {p: ok(lambda j: len(j["items"]) >= 1 and "text" not in j["items"][0])
              for p in AUTHENTIFIES},
             preparer=_pre_avec_doc),

    # === Suppression (DELETE /api/vault/documents/{doc_id}) ===
    CasUsage("vlt-del-anon", "Vault — suppression",
             "Suppression sans authentification → 401.",
             "DELETE", "/api/vault/documents/{doc_id}",
             {"anonyme": refuse(401)}, preparer=_pre_doc_autrui_anonyme),
    CasUsage("vlt-del-inconnu", "Vault — suppression",
             "Suppression d'un document inexistant → 404.",
             "DELETE", "/api/vault/documents/{doc_id}",
             {p: refuse(404) for p in AUTHENTIFIES}, preparer=_pre_doc_inconnu),
    CasUsage("vlt-del-autrui", "Vault — suppression",
             "Suppression du document d'un autre utilisateur → 404 (isolation stricte).",
             "DELETE", "/api/vault/documents/{doc_id}",
             {"intrus": refuse(404)}, preparer=_pre_doc_autrui),
    CasUsage("vlt-del-ok", "Vault — suppression",
             "Le propriétaire supprime son document → {ok:true}.",
             "DELETE", "/api/vault/documents/{doc_id}",
             {p: ok(lambda j: j.get("ok") is True) for p in AUTHENTIFIES},
             preparer=_pre_avec_doc),

    # === Q&A (POST /api/vault/ask) ===
    CasUsage("vlt-ask-anon", "Vault — Q&A",
             "Q&A sans authentification → 401.",
             "POST", "/api/vault/ask", {"anonyme": refuse(401)},
             corps={"q": "quel délai de préavis ?"}),
    CasUsage("vlt-ask-ok", "Vault — Q&A",
             "Q&A sourcé isolé : answer non vide et refused=false.",
             "POST", "/api/vault/ask",
             {p: ok(lambda j: bool(j["answer"]) and j["refused"] is False)
              for p in AUTHENTIFIES},
             corps={"q": "quel délai de préavis ?"}),
    CasUsage("vlt-ask-vide", "Vault — Q&A",
             "Aucun passage pertinent dans le Vault → refus gracieux (refused=true).",
             "POST", "/api/vault/ask",
             {p: ok(lambda j: j["refused"] is True) for p in AUTHENTIFIES},
             corps={"q": "question sans réponse dans mes documents"},
             stubs=[(VAULT, "search_vault", lambda o, q, ids, k: [])]),
    CasUsage("vlt-ask-hybride", "Vault — Q&A",
             "Q&A hybride : documents privés + corpus public officiel (include_corpus).",
             "POST", "/api/vault/ask",
             {p: ok(lambda j: j["refused"] is False) for p in AUTHENTIFIES},
             corps={"q": "quel article encadre le licenciement ?", "include_corpus": True}),
    CasUsage("vlt-ask-ciblage", "Vault — Q&A",
             "Q&A ciblé sur des documents précis (doc_ids) → réponse sourcée.",
             "POST", "/api/vault/ask",
             {p: ok(lambda j: bool(j["answer"]) and j["refused"] is False)
              for p in AUTHENTIFIES},
             corps={"q": "quel montant a été alloué ?", "doc_ids": [1]}),

    # === Analyses locales déterministes (POST /api/vault/documents/{doc_id}/analyze) ===
    CasUsage("vlt-analyze-citations", "Vault — analyse citations",
             "Extrait les références et les VÉRIFIE contre le corpus officiel.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: ok(lambda j: j["verified"] >= 1 and j["total"] >= 1) for p in AUTHENTIFIES},
             corps={"task": "citations"}, preparer=_pre_doc_analyse),
    CasUsage("vlt-analyze-extract", "Vault — analyse extraction",
             "Extraction structurée locale : matière dominante + montants normalisés.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: ok(lambda j: j["matter"] == "Droit du travail" and "1.500,00 EUR" in j["amounts"])
              for p in AUTHENTIFIES},
             corps={"task": "extract"}, preparer=_pre_doc_analyse),
    CasUsage("vlt-analyze-summary", "Vault — résumé",
             "task=summary : résumé fidèle du document (LLM routé « confidentiel »).",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: ok(lambda j: j["task"] == "summary" and bool(j["summary"])) for p in AUTHENTIFIES},
             corps={"task": "summary"}, preparer=_pre_doc_analyse),
    CasUsage("vlt-analyze-counter", "Vault — contre-argumentaire sourcé",
             "task=counter : réfutation ancrée à la jurisprudence LU, citations vérifiables.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: ok(lambda j: j["task"] == "counter" and j["refused"] is False and bool(j["citations"]))
              for p in AUTHENTIFIES},
             corps={"task": "counter"}, preparer=_pre_doc_analyse),
    CasUsage("vlt-analyze-invalide", "Vault — analyse erreurs",
             "Tâche d'analyse inconnue → 422 (validation Pydantic du champ task).",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: refuse(422) for p in AUTHENTIFIES},
             corps={"task": "foo"}, preparer=_pre_doc_analyse),
    CasUsage("vlt-analyze-inconnu", "Vault — analyse erreurs",
             "Analyse d'un document inexistant → 404.",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {p: refuse(404) for p in AUTHENTIFIES},
             corps={"task": "citations"}, preparer=_pre_doc_inconnu),
    CasUsage("vlt-analyze-autrui", "Vault — analyse erreurs",
             "Analyse du document d'un autre utilisateur → 404 (isolation stricte).",
             "POST", "/api/vault/documents/{doc_id}/analyze",
             {"intrus": refuse(404)},
             corps={"task": "citations"}, preparer=_pre_doc_autrui),
]


# --------- parcours multi-documents ---------
def _pre_multi(banc: Banc) -> dict:
    """Un avocat pro (bac à sable vide) et un intrus pour la vérification d'isolation."""
    pro = banc.enregistrer(plan="pro")
    intrus = banc.enregistrer()
    return {"headers": pro["headers"], "headers_intrus": intrus["headers"]}


PARCOURS = [
    # Vault avancé : deux documents, liste, Q&A ciblé, analyses, isolation, purge complète.
    Parcours("vault-multi", "Vault : gestion multi-documents (dépôt, ciblage, analyses, purge)", "pro", [
        E("dépose un 1er document", "POST", "/api/vault/documents?filename=doc1.txt",
          ok(lambda j: j["status"] == "ready"),
          contenu=_CONTENU_ANALYSE, entetes={"Content-Type": "text/plain"},
          capture=lambda j, c: c.__setitem__("doc1", j["id"])),
        E("dépose un 2e document", "POST", "/api/vault/documents?filename=doc2.txt",
          ok(lambda j: j["status"] == "ready"),
          contenu=_CONTENU_ANALYSE, entetes={"Content-Type": "text/plain"},
          capture=lambda j, c: c.__setitem__("doc2", j["id"])),
        E("les deux documents sont listés (sans texte)", "GET", "/api/vault/documents",
          ok(lambda j: len(j["items"]) == 2 and "text" not in j["items"][0])),
        E("Q&A ciblé sur le 1er document uniquement", "POST", "/api/vault/ask",
          ok(lambda j: bool(j["answer"]) and j["refused"] is False),
          corps=lambda c: {"q": "quel montant a été alloué ?", "doc_ids": [c["doc1"]]}),
        E("vérifie les citations du 1er document", "POST", "/api/vault/documents/{doc1}/analyze",
          ok(lambda j: j["verified"] >= 1 and j["total"] >= 1), corps={"task": "citations"}),
        E("extrait la structure du 2e document", "POST", "/api/vault/documents/{doc2}/analyze",
          ok(lambda j: j["matter"] == "Droit du travail" and "1.500,00 EUR" in j["amounts"]),
          corps={"task": "extract"}),
        E("un intrus ne peut PAS analyser le 1er", "POST", "/api/vault/documents/{doc1}/analyze",
          refuse(404), acteur="headers_intrus", role="intrus", corps={"task": "citations"}),
        E("purge le 1er document", "DELETE", "/api/vault/documents/{doc1}",
          ok(lambda j: j.get("ok") is True)),
        E("purge le 2e document", "DELETE", "/api/vault/documents/{doc2}",
          ok(lambda j: j.get("ok") is True)),
        E("le Vault est de nouveau vide", "GET", "/api/vault/documents",
          ok(lambda j: j["items"] == [])),
    ], preparer=_pre_multi),
]

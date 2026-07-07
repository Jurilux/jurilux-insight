"""Couverture : feedback & partage — /api/feedback et /api/share.

Deux endpoints **ouverts à tous** (anonymes inclus) : le retour utilisateur (👍/👎 +
ce qui manquait) et les permaliens partageables. On couvre largement :
  - Feedback : succès pour anonyme et chaque profil connecté, variantes 👍/👎, champ
    `missing` très long (borné côté serveur), et toute la validation Pydantic (422).
  - Partage : création (avec/sans citations) pour tous les profils, validation 422,
    lecture PUBLIQUE d'un permalien (anonyme ET connecté), et 404 sur id inconnu.
Plus un parcours de bout en bout : créer un permalien puis le relire publiquement.
"""
from __future__ import annotations

from ._base import *


# --------- préparateurs spécifiques ---------
def _prep_lecture_partage(banc: Banc, nom: str) -> tuple:
    """Crée un permalien (anonyme) puis renvoie les en-têtes du profil qui va le lire,
    avec l'id du partage dans le contexte (`sid`) pour formater le chemin."""
    sid = banc.creer_partage()
    headers, ctx = banc.profil(nom)
    return headers, {**ctx, "sid": sid}


# --------- catalogue des cas d'usage ---------
CAS = [
    # ============================ Feedback — /api/feedback ============================
    # --- succès 👍 : ouvert à l'anonyme et à chaque profil connecté ---
    CasUsage("feedback-pouce-haut", "Feedback — /api/feedback",
             "Retour positif (👍) accepté pour l'anonyme et tout profil connecté → {ok:true}.",
             "POST", "/api/feedback",
             {p: ok(lambda j: j.get("ok") is True) for p in COMPTE},
             corps={"question": "Q ?", "helpful": True, "status": "ok"}),

    # --- succès 👎 avec ce qui manquait ---
    CasUsage("feedback-pouce-bas-missing", "Feedback — /api/feedback",
             "Retour négatif (👎) avec le champ `missing` → {ok:true}.",
             "POST", "/api/feedback",
             {p: ok(lambda j: j.get("ok") is True) for p in COMPTE},
             corps={"question": "Q ?", "helpful": False, "missing": "il manquait X"}),

    # --- succès 👎 avec status=partial (statut de la réponse notée) ---
    CasUsage("feedback-status-partial", "Feedback — /api/feedback",
             "Retour sur une réponse partielle (status=partial) → {ok:true}.",
             "POST", "/api/feedback",
             {p: ok(lambda j: j.get("ok") is True) for p in AUTHENTIFIES},
             corps={"question": "Q partielle ?", "helpful": False,
                    "missing": "la source exacte", "status": "partial"}),

    # --- champ `missing` très long : borné côté serveur, jamais d'erreur ---
    CasUsage("feedback-missing-tres-long", "Feedback — /api/feedback",
             "Champ `missing` très long (5000 car.) : accepté/borné côté serveur → {ok:true}.",
             "POST", "/api/feedback",
             {"anonyme": ok(lambda j: j.get("ok") is True)},
             corps={"question": "Q ?", "helpful": False, "missing": "x" * 5000}),

    # --- feedback minimal (juste question + helpful) ---
    CasUsage("feedback-minimal", "Feedback — /api/feedback",
             "Corps minimal (question + helpful) sans missing ni status → {ok:true}.",
             "POST", "/api/feedback",
             {"anonyme": ok(lambda j: j.get("ok") is True)},
             corps={"question": "Q ?", "helpful": True}),

    # --- validation 422 : question vide (min_length 1) ---
    CasUsage("feedback-question-vide", "Feedback — /api/feedback",
             "Question vide (viole min_length=1) → 422 (validation Pydantic).",
             "POST", "/api/feedback", {"anonyme": refuse(422)},
             corps={"question": "", "helpful": True}),

    # --- validation 422 : champ question absent ---
    CasUsage("feedback-question-manquante", "Feedback — /api/feedback",
             "Champ `question` absent → 422.",
             "POST", "/api/feedback", {"anonyme": refuse(422)},
             corps={"helpful": True}),

    # --- validation 422 : champ helpful absent (requis, sans défaut) ---
    CasUsage("feedback-helpful-manquant", "Feedback — /api/feedback",
             "Champ `helpful` absent (requis) → 422.",
             "POST", "/api/feedback", {"anonyme": refuse(422)},
             corps={"question": "Q ?"}),

    # ============================ Partage — /api/share ============================
    # --- création : ouvert à l'anonyme et à chaque profil connecté ---
    CasUsage("share-creation", "Partage — /api/share",
             "Création d'un permalien ouverte à tous → {id}.",
             "POST", "/api/share",
             {p: ok(lambda j: bool(j.get("id"))) for p in COMPTE},
             corps={"question": "Q ?", "answer": "A", "status": "ok"}),

    # --- création avec citations (instantané des sources) ---
    CasUsage("share-creation-citations", "Partage — /api/share",
             "Création avec un instantané de citations → {id}.",
             "POST", "/api/share",
             {p: ok(lambda j: bool(j.get("id"))) for p in COMPTE},
             corps={"question": "Q ?", "answer": "A",
                    "citations": [{"doc_id": "x", "title": "t"}], "status": "ok"}),

    # --- création minimale : seule la question est requise ---
    CasUsage("share-creation-minimale", "Partage — /api/share",
             "Création avec la seule question (answer/citations optionnels) → {id}.",
             "POST", "/api/share",
             {"anonyme": ok(lambda j: bool(j.get("id")))},
             corps={"question": "Q ?"}),

    # --- validation 422 : question vide ---
    CasUsage("share-question-vide", "Partage — /api/share",
             "Question vide (viole min_length=1) → 422.",
             "POST", "/api/share", {"anonyme": refuse(422)},
             corps={"question": "", "answer": "A"}),

    # --- validation 422 : champ question absent ---
    CasUsage("share-question-manquante", "Partage — /api/share",
             "Champ `question` absent → 422.",
             "POST", "/api/share", {"anonyme": refuse(422)},
             corps={"answer": "A"}),

    # --- lecture PUBLIQUE : accessible à l'anonyme comme au connecté ---
    CasUsage("share-lecture-publique", "Partage — /api/share",
             "Lecture d'un permalien : PUBLIQUE (anonyme et connecté) → {question,...}.",
             "GET", "/api/share/{sid}",
             {p: ok(lambda j: j.get("question") is not None) for p in COMPTE},
             preparer=_prep_lecture_partage),

    # --- lecture PUBLIQUE : forme complète du permalien ---
    CasUsage("share-lecture-forme", "Partage — /api/share",
             "Le permalien lu expose question/answer/citations/status/created_at.",
             "GET", "/api/share/{sid}",
             {"anonyme": ok(lambda j: "question" in j and "answer" in j
                            and "citations" in j and "created_at" in j)},
             preparer=_prep_lecture_partage),

    # --- lecture d'un id inconnu → 404 ---
    CasUsage("share-lecture-inconnu", "Partage — /api/share",
             "Identifiant de permalien inconnu → 404.",
             "GET", "/api/share/inexistant123", {"anonyme": refuse(404)}),
]


# --------- parcours utilisateur : permalien créé puis relu publiquement ---------
PARCOURS = [
    Parcours("partage-permalien",
             "Partage : créer un permalien puis le relire publiquement", "etudiant", [
        E("crée un permalien pour une réponse", "POST", "/api/share",
          ok(lambda j: bool(j.get("id"))),
          corps={"question": "quel est le préavis légal ?", "answer": "Deux mois.",
                 "citations": [{"doc_id": "x", "title": "t"}], "status": "ok"},
          capture=lambda j, c: c.__setitem__("sid", j["id"])),
        E("le relit publiquement (même session)", "GET", "/api/share/{sid}",
          ok(lambda j: j.get("question") is not None)),
        E("un anonyme le relit aussi (public)", "GET", "/api/share/{sid}",
          ok(lambda j: j.get("answer") == "Deux mois."), acteur="anonyme", role="anonyme"),
    ]),
]

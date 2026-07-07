"""Couverture TRÈS ÉTENDUE du endpoint /api/ask (RAG).

Succès sourcé pour chaque profil, branches d'erreur injectées par **stubs**
(aucun résultat, Meilisearch en panne, LLM en panne — le endpoint refuse
toujours GRACIEUSEMENT, jamais de 500), validation Pydantic (422), mode
pédagogique, contexte conversationnel, filtres, et raccourci avocat (Insight
court-circuite le RAG). On NE teste PAS /api/ask/stream (appellerait le vrai
Anthropic).
"""
from __future__ import annotations

from ._base import *


def _boom(*a, **k):
    """Simule une panne d'un service externe (Meili/LLM) : lève une exception."""
    raise RuntimeError("meili")


def _rag_selon_hits(q, hits, *a, **k):
    """Reproduit le vrai comportement de rag.answer : refus gracieux quand aucun extrait
    n'est trouvé (le stub par défaut du banc répond toujours, quelle que soit la liste
    d'extraits)."""
    if not hits:
        return RAG.refusal("Aucun extrait pertinent trouvé pour cette question.")
    return RAG.answer(q, hits, *a, **k)


CAS = [
    # --- succès sourcé, pour chaque profil compte ---
    CasUsage("ask-ok", "RAG — /api/ask succès",
             "Réponse sourcée : answer + citations, refused=false, pour chaque profil.",
             "POST", "/api/ask",
             {p: ok(lambda j: bool(j["answer"]) and bool(j["citations"]) and j["refused"] is False)
              for p in COMPTE},
             corps={"q": "faute grave licenciement"}),

    # --- aucun résultat (recherche vide) → refus gracieux ---
    CasUsage("ask-aucun-resultat", "RAG — /api/ask refus gracieux",
             "Aucun extrait trouvé → refused=true (jamais de 500).",
             "POST", "/api/ask", {"anonyme": ok(lambda j: j["refused"] is True)},
             corps={"q": "question sans extrait"},
             stubs=[(SEARCH, "search", lambda q, k, f: []),
                    (RAG, "answer", _rag_selon_hits)]),

    # --- panne Meilisearch → refus gracieux ---
    CasUsage("ask-meili-panne", "RAG — /api/ask refus gracieux",
             "Meilisearch en panne → refused=true, le endpoint ne renvoie jamais 500.",
             "POST", "/api/ask", {"anonyme": ok(lambda j: j["refused"] is True)},
             corps={"q": "faute grave licenciement"},
             stubs=[(SEARCH, "search", _boom)]),

    # --- panne LLM → refus gracieux ---
    CasUsage("ask-llm-panne", "RAG — /api/ask refus gracieux",
             "Génération LLM en panne → refused=true, jamais de 500.",
             "POST", "/api/ask", {"anonyme": ok(lambda j: j["refused"] is True)},
             corps={"q": "faute grave licenciement"},
             stubs=[(RAG, "answer", _boom)]),

    # --- validation Pydantic (422) ---
    CasUsage("ask-q-vide", "RAG — /api/ask validation",
             "q vide (min_length=1) → 422.",
             "POST", "/api/ask", {"anonyme": refuse(422)},
             corps={"q": ""}),
    CasUsage("ask-topk-zero", "RAG — /api/ask validation",
             "topK=0 (ge=1) → 422.",
             "POST", "/api/ask", {"anonyme": refuse(422)},
             corps={"q": "faute grave", "topK": 0}),
    CasUsage("ask-topk-trop-grand", "RAG — /api/ask validation",
             "topK=101 (le=100) → 422.",
             "POST", "/api/ask", {"anonyme": refuse(422)},
             corps={"q": "faute grave", "topK": 101}),
    CasUsage("ask-temp-trop-haute", "RAG — /api/ask validation",
             "temperature=1.5 (le=1.0) → 422.",
             "POST", "/api/ask", {"anonyme": refuse(422)},
             corps={"q": "faute grave", "temperature": 1.5}),
    CasUsage("ask-temp-negative", "RAG — /api/ask validation",
             "temperature=-0.1 (ge=0.0) → 422.",
             "POST", "/api/ask", {"anonyme": refuse(422)},
             corps={"q": "faute grave", "temperature": -0.1}),

    # --- mode pédagogique ---
    CasUsage("ask-pedagogique", "RAG — /api/ask options",
             "Mode pédagogique (plan étudiant) → réponse sourcée, refused=false.",
             "POST", "/api/ask",
             {"etudiant": ok(lambda j: bool(j["answer"]) and j["refused"] is False)},
             corps={"q": "expliquer la faute grave", "pedagogical": True}),

    # --- contexte conversationnel (history) ---
    CasUsage("ask-history", "RAG — /api/ask options",
             "Contexte conversationnel (tours précédents) → réponse sourcée.",
             "POST", "/api/ask",
             {"pro": ok(lambda j: bool(j["answer"]) and j["refused"] is False)},
             corps={"q": "et le préavis ?", "history": [
                 {"role": "user", "content": "qu'est-ce que la faute grave ?"},
                 {"role": "assistant", "content": "La faute grave prive du préavis."},
             ]}),

    # --- filtres de recherche ---
    CasUsage("ask-filtres", "RAG — /api/ask options",
             "Filtres source_type=law + year_min → réponse sourcée.",
             "POST", "/api/ask",
             {"anonyme": ok(lambda j: bool(j["answer"]) and j["refused"] is False)},
             corps={"q": "code du travail", "filters": {"source_type": "law", "year_min": 2000}}),

    # --- raccourci avocat : Insight court-circuite le RAG ---
    CasUsage("ask-avocat", "RAG — /api/ask raccourci avocat",
             "Recherche nominative (« décisions de Maître X ») → profil + décisions, refused=false.",
             "POST", "/api/ask",
             {"anonyme": ok(lambda j: j["refused"] is False and "Dupont" in (j.get("answer") or ""))},
             corps={"q": "décisions de Maître Jean DUPONT"}),
]

PARCOURS = []

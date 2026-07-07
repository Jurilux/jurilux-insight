"""Couverture : Insight avocats — endpoints /api/insight/* (tous PUBLICS).

Profiling des AVOCATS uniquement (« Maître X »), données publiques de jurisprudence.
Aucune authentification requise : on vérifie donc l'accès pour l'anonyme ET pour chaque
profil compte (la couverture publique doit tenir quel que soit l'appelant).

Données injectées par le banc (`INSIGHT_ROWS`) : deux avocats —
  · « MAITRE JEAN DUPONT » : matières « Droit du travail » (côté A, gagné) et
    « Bail / logement » (côté B, perdu) ;
  · « MAITRE ANNE MARTIN » : « Droit du travail » (côté B, perdu).
"""
from __future__ import annotations

from ._base import *

CAS = [
    # === /api/insight/analytics (public) ===
    CasUsage("insight-analytics", "Insight — analytics contentieux (public)",
             "GET /api/insight/analytics : volumes + taux de succès par matière/juridiction/année.",
             "GET", "/api/insight/analytics",
             {"anonyme": ok(lambda j: "overall" in j and "by_matter" in j and j["overall"]["cases"] >= 1),
              "pro": ok(lambda j: "by_juridiction" in j and "by_year" in j)}),
    CasUsage("insight-analytics-filtre", "Insight — analytics contentieux (public)",
             "GET /api/insight/analytics?matter=... : filtrable par matière.",
             "GET", "/api/insight/analytics?matter=Droit du travail",
             {"anonyme": ok(lambda j: "overall" in j)}),

    # === /api/insight/stats (public) ===
    CasUsage("insight-stats", "Insight — statistiques (public)",
             "GET /api/insight/stats : compteurs publics ; testé anonyme + chaque profil.",
             "GET", "/api/insight/stats",
             {p: ok(lambda j: j["lawyers"] >= 1 and "appearances" in j) for p in COMPTE}),

    # === /api/insight/matters (public) ===
    CasUsage("insight-matters", "Insight — matières (public)",
             "GET /api/insight/matters : domaines de droit disponibles pour le filtre.",
             "GET", "/api/insight/matters",
             {"anonyme": ok(lambda j: "items" in j and len(j["items"]) >= 1)}),

    # === /api/insight/lawyers (public) — liste et variantes de tri/filtre ===
    CasUsage("insight-lawyers", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers : liste publique (anonyme + un profil connecté).",
             "GET", "/api/insight/lawyers",
             {"anonyme": ok(lambda j: len(j["items"]) >= 1),
              "pro": ok(lambda j: len(j["items"]) >= 1)}),
    CasUsage("insight-lawyers-recent", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers?sort=recent : tri par décision la plus récente.",
             "GET", "/api/insight/lawyers?sort=recent",
             {"anonyme": ok(lambda j: "items" in j)}),
    CasUsage("insight-lawyers-winrate", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers?sort=winrate : tri par taux (exige decided>=10 → "
             "peut être vide ici, on n'exige que le 200 + forme).",
             "GET", "/api/insight/lawyers?sort=winrate",
             {"anonyme": ok(lambda j: "items" in j)}),
    CasUsage("insight-lawyers-matter", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers?matter=Droit du travail : top du domaine.",
             "GET", "/api/insight/lawyers?matter=Droit du travail",
             {"anonyme": ok(lambda j: len(j["items"]) >= 1)}),
    CasUsage("insight-lawyers-q", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers?q=Dupont : recherche nominative.",
             "GET", "/api/insight/lawyers?q=Dupont",
             {"anonyme": ok(lambda j: any("DUPONT" in x["name_key"] for x in j["items"]))}),
    CasUsage("insight-lawyers-limit", "Insight — liste des avocats (public)",
             "GET /api/insight/lawyers?limit=1 : la limite est respectée.",
             "GET", "/api/insight/lawyers?limit=1",
             {"anonyme": ok(lambda j: len(j["items"]) <= 1)}),

    # === /api/insight/lawyers/{key} (public) — profil détaillé + introuvable ===
    CasUsage("insight-lawyer-detail", "Insight — profil d'un avocat (public)",
             "GET /api/insight/lawyers/{key} : profil détaillé (nom + liste de décisions). "
             "L'espace du nom-clé est encodé (%20) dans l'URL.",
             "GET", "/api/insight/lawyers/MAITRE%20JEAN%20DUPONT",
             {"anonyme": ok(lambda j: bool(j.get("name")) and "cases" in j)}),
    CasUsage("insight-lawyer-404", "Insight — profil d'un avocat (public)",
             "GET /api/insight/lawyers/ZZZINCONNU : avocat introuvable → 404.",
             "GET", "/api/insight/lawyers/ZZZINCONNU",
             {"anonyme": refuse(404)}),
]

PARCOURS = []

"""Couverture : endpoints de service — /health (nominal + pannes injectées), /api/corpus,
/api/metrics. Montre l'usage des **stubs par scénario** pour les branches d'erreur."""
from __future__ import annotations

from ._base import *

CAS = [
    # --- /health : nominal + pannes (stubs) ---
    CasUsage("health-ok", "Service — /health",
             "Meili up + clé LLM présente → 200 status=ok.",
             "GET", "/health", {"anonyme": ok(lambda j: j["status"] == "ok")}),
    CasUsage("health-meili-down", "Service — /health",
             "Meilisearch en panne → 503 degraded.",
             "GET", "/health", {"anonyme": refuse(503)},
             stubs=[(SEARCH, "meili_healthy", lambda: False)]),
    CasUsage("health-sans-cle-llm", "Service — /health",
             "Clé Anthropic absente → 503 (le front n'affiche « Connecté » que sur res.ok).",
             "GET", "/health", {"anonyme": refuse(503)},
             stubs=[(SETTINGS, "anthropic_api_key", "")]),

    # --- /api/corpus (public) ---
    CasUsage("corpus-ok", "Service — /api/corpus",
             "Périmètre du corpus, public.",
             "GET", "/api/corpus",
             {p: ok(lambda j: j.get("decisions") is not None and "latest_year" in j) for p in COMPTE}),

    # --- /api/metrics (public, volatile) ---
    CasUsage("metrics-ok", "Service — /api/metrics",
             "Instantané des compteurs d'observabilité.",
             "GET", "/api/metrics", {"anonyme": ok(lambda j: isinstance(j, dict))}),
]

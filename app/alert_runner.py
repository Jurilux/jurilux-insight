"""Vérification des alertes de veille : recherche + stockage des résultats non vus.

Utilisé par les endpoints (check à la demande) ET par le cron d'ingestion (push
automatique après ré-indexation) : `python -m app.alert_runner`.
"""
from __future__ import annotations

from . import alert as alert_store, search
from .schemas import SearchFilters


def check(al: dict) -> int:
    """Passe le sujet d'une alerte dans la recherche et stocke les résultats non vus. Nb de nouveaux."""
    f = SearchFilters(source_type=al.get("source_type") or None)
    hits = search.search(al["query"], 20, f)
    payload = [{"doc_id": h.doc_id, "source_type": h.source_type, "title": h.title,
                "year": h.year, "juridiction_key": h.juridiction_key,
                "url": h.url, "pdf_url": h.pdf_url} for h in hits]
    return alert_store.add_hits(al["id"], payload)


def run() -> int:
    """Vérifie toutes les alertes (tous utilisateurs). Renvoie le total de nouveaux résultats."""
    alerts = alert_store.all_alerts()
    total = 0
    for al in alerts:
        try:
            total += check(al)
        except Exception:
            continue
    print(f"alertes : {len(alerts)} vérifiées, {total} nouveaux résultats")
    return total


if __name__ == "__main__":
    run()

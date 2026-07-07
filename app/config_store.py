"""Paramétrage runtime (socle on-prem) : régler certains paramètres NON secrets sans
redéploiement. Persisté dans `app_config`, appliqué à `settings` au démarrage (`db.init_db`)
et à chaque écriture. Les secrets (clés API, JWT…) restent en `.env` — jamais ici.
"""
from __future__ import annotations

from . import db
from .config import settings

# Réglages exposés (non secrets uniquement). Toute autre clé est refusée.
CLES_AUTORISEES = (
    "llm_provider_public", "llm_provider_confidential", "mistral_model", "local_model",
    "ollama_url", "anthropic_model", "prompt_version", "rate_limit_per_min",
    "student_monthly_quota", "hybrid_semantic_ratio", "max_context_chunks", "snippet_len",
)


def get_all() -> dict:
    """Valeurs runtime effectives des clés exposées (telles qu'appliquées à settings)."""
    return {k: getattr(settings, k) for k in CLES_AUTORISEES if hasattr(settings, k)}


def set_many(valeurs: dict) -> dict:
    """Persiste et applique des réglages. Ignore les clés non autorisées. Renvoie l'état."""
    appliques = {}
    with db.get_conn() as conn:
        for key, value in valeurs.items():
            if key not in CLES_AUTORISEES:
                continue
            sval = str(value)
            conn.execute(
                "INSERT INTO app_config(key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, sval))
            db._caster_sur_settings(key, sval)
            appliques[key] = getattr(settings, key)
    return appliques

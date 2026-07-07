"""Portabilité (export) et rétention/purge des données (socle conformité on-prem, RGPD).

Export : toutes les données d'un utilisateur, en clair, pour la portabilité (art. 20 RGPD).
Purge : minimisation — supprime les données au-delà d'une ancienneté configurable. SQLite.
"""
from __future__ import annotations

from typing import Optional

from .db import get_conn, iso_ago


def export_user(user_id: int) -> dict:
    """Toutes les données rattachées à un utilisateur (portabilité RGPD)."""
    with get_conn() as conn:
        u = conn.execute("SELECT id, email, plan, is_admin, created_at FROM users WHERE id = ?",
                         (user_id,)).fetchone()
        if not u:
            return {}
        def _q(sql):
            return [dict(r) for r in conn.execute(sql, (user_id,)).fetchall()]
        return {
            "user": dict(u),
            "history": _q("SELECT question, answer, status, created_at FROM history WHERE user_id = ? ORDER BY id"),
            "feedback": _q("SELECT question, helpful, missing, status, created_at FROM feedback WHERE user_id = ? ORDER BY id"),
            "shares": _q("SELECT id, question, answer, status, created_at FROM shares WHERE user_id = ? ORDER BY id"),
            "alerts": _q("SELECT query, source_type, created_at FROM alerts WHERE user_id = ? ORDER BY id"),
            "vault_documents": _q("SELECT id, filename, mime, status, n_chunks, created_at FROM vault_documents WHERE owner_id = ? ORDER BY id"),
            "api_keys": _q("SELECT name, prefix, created_at, last_used_at, revoked FROM api_keys WHERE owner_id = ? ORDER BY id"),
        }


def purge(days: int) -> dict:
    """Supprime les données au-delà de `days` jours (rétention). Renvoie les compteurs.
    N'affecte ni les comptes ni les documents Vault (données actives de l'utilisateur)."""
    seuil = iso_ago(days=max(0, days))
    out = {}
    with get_conn() as conn:
        for table in ("history", "feedback", "shares", "audit_log"):
            col = "ts" if table == "audit_log" else "created_at"
            cur = conn.execute(f"DELETE FROM {table} WHERE {col} < ?", (seuil,))
            out[table] = cur.rowcount
    return {"before": seuil, "deleted": out}

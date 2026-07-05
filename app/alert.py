"""Alertes « nouvelle jurisprudence sur mes sujets » — veille in-app (sans e-mail).

Un utilisateur enregistre un sujet (requête). À la vérification, on passe le sujet dans
la recherche (fait par main.py) et on stocke les résultats non encore vus (dédup doc_id)
comme « hits » non lus. SQLite ; la recherche n'est pas importée ici (séparation).
"""
from __future__ import annotations

import datetime
import sqlite3
from typing import List, Optional

from .db import get_conn


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def create_alert(user_id: int, query: str, source_type: Optional[str]) -> dict:
    st = source_type if source_type in ("jurisprudence", "law", "projet_loi") else None
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO alerts(user_id, query, source_type, created_at) VALUES (?,?,?,?)",
                           (user_id, query.strip(), st, _now()))
    return {"id": cur.lastrowid, "query": query.strip(), "source_type": st, "unseen": 0, "total": 0}


def list_alerts(user_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.id, a.query, a.source_type, a.checked_at, "
            "  (SELECT COUNT(*) FROM alert_hits h WHERE h.alert_id = a.id) AS total, "
            "  (SELECT COUNT(*) FROM alert_hits h WHERE h.alert_id = a.id AND h.seen = 0) AS unseen "
            "FROM alerts a WHERE a.user_id = ? ORDER BY a.id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def all_alerts() -> List[dict]:
    """Toutes les alertes (tous utilisateurs) — pour le check groupé par le cron d'ingestion."""
    with get_conn() as conn:
        rows = conn.execute("SELECT id, query, source_type FROM alerts").fetchall()
    return [dict(r) for r in rows]


def get_alert(alert_id: int, user_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute("SELECT id, query, source_type FROM alerts WHERE id = ? AND user_id = ?",
                           (alert_id, user_id)).fetchone()
    return dict(row) if row else None


def delete_alert(alert_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, user_id))
        return cur.rowcount > 0


def add_hits(alert_id: int, hits: List[dict]) -> int:
    """Ajoute les résultats non encore vus (UNIQUE(alert_id, doc_id) => dédup). Renvoie le nb de nouveaux."""
    now = _now()
    new = 0
    with get_conn() as conn:
        for h in hits:
            try:
                conn.execute(
                    "INSERT INTO alert_hits(alert_id, doc_id, source_type, title, year, "
                    "juridiction_key, url, pdf_url, seen, created_at) VALUES (?,?,?,?,?,?,?,?,0,?)",
                    (alert_id, h.get("doc_id"), h.get("source_type"), h.get("title"), h.get("year"),
                     h.get("juridiction_key"), h.get("url"), h.get("pdf_url"), now))
                new += 1
            except sqlite3.IntegrityError:
                pass  # déjà connu de cette alerte
        conn.execute("UPDATE alerts SET checked_at = ? WHERE id = ?", (now, alert_id))
    return new


def list_hits(alert_id: int, limit: int = 100) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, doc_id, source_type, title, year, juridiction_key, url, pdf_url, seen, created_at "
            "FROM alert_hits WHERE alert_id = ? ORDER BY id DESC LIMIT ?", (alert_id, limit)).fetchall()
    return [dict(r) for r in rows]


def mark_seen(alert_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE alert_hits SET seen = 1 WHERE alert_id = ? AND seen = 0", (alert_id,))

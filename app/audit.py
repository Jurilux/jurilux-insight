"""Journal d'audit souverain (socle on-prem) : trace locale qui/quoi/quand.

Écriture **best-effort** (ne casse jamais l'action métier). Lecture réservée au backoffice,
avec export. Sert le secret professionnel/RGPD et répond à la question de rétention des
requêtes (`COMPLIANCE.md §4). SQLite, stdlib.
"""
from __future__ import annotations

from typing import List, Optional

from .db import get_conn, now_iso

_MAX_DETAIL = 2000


def log(action: str, user: Optional[dict] = None, detail: Optional[str] = None,
        ip: Optional[str] = None) -> None:
    """Journalise une action. `user` = dict utilisateur (ou None si anonyme)."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log(ts, user_id, email, action, detail, ip) VALUES (?,?,?,?,?,?)",
                (now_iso(), (user or {}).get("id"), (user or {}).get("email"),
                 action, (detail or "")[:_MAX_DETAIL] or None, ip))
    except Exception:
        pass  # l'audit ne doit jamais faire échouer l'action métier


def recent(limit: int = 200, action: Optional[str] = None) -> List[dict]:
    where, args = "", []
    if action:
        where = "WHERE action LIKE ? "
        args.append(action + "%")
    args.append(max(1, min(limit, 1000)))
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, ts, user_id, email, action, detail, ip FROM audit_log "
            f"{where}ORDER BY id DESC LIMIT ?", args).fetchall()
    return [dict(r) for r in rows]


def purge(before_iso: str) -> int:
    """Supprime les entrées antérieures à `before_iso` (rétention). Renvoie le nb supprimé."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM audit_log WHERE ts < ?", (before_iso,))
        return cur.rowcount

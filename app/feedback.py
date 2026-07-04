"""Retours utilisateurs (👍/👎 + « ce qui manquait ») sur les réponses.

Écriture publique (POST /api/feedback, avec ou sans compte) ; lectures réservées
au backoffice. SQLite, stdlib. La table est créée dans db.init_db().
"""
from __future__ import annotations

import datetime
from typing import List, Optional

from .db import get_conn


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def add(user_id: Optional[int], question: str, helpful: bool,
        missing: Optional[str], status: Optional[str],
        prompt_version: Optional[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO feedback(user_id, question, helpful, missing, status, "
            "prompt_version, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, question, 1 if helpful else 0,
             (missing or None), status, prompt_version, _now_iso()))


def stats() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN helpful=1 THEN 1 ELSE 0 END) AS up "
            "FROM feedback").fetchone()
    total = row["total"] or 0
    up = row["up"] or 0
    return {"total": total, "helpful": up, "not_helpful": total - up,
            "satisfaction": round(up / total, 3) if total else None}


def recent(limit: int = 100) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT f.id, u.email, f.question, f.helpful, f.missing, f.status, f.created_at "
            "FROM feedback f LEFT JOIN users u ON u.id = f.user_id "
            "ORDER BY f.id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "email": r["email"], "question": r["question"],
             "helpful": bool(r["helpful"]), "missing": r["missing"],
             "status": r["status"], "created_at": r["created_at"]} for r in rows]

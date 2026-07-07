"""Accès aux données pour le backoffice admin.

Lecture (stats, listes) et gestion des comptes (plan, promotion admin, suppression).
Toutes ces fonctions supposent que l'appelant a déjà été authentifié comme admin
côté endpoint (voir app.main._require_admin). SQLite, stdlib uniquement.
"""
from __future__ import annotations

from typing import List, Optional

from .db import get_conn, iso_ago


# ---------- statistiques ----------
def user_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN plan='pro' THEN 1 ELSE 0 END) AS pros, "
            "SUM(CASE WHEN is_admin=1 THEN 1 ELSE 0 END) AS admins "
            "FROM users").fetchone()
    total = row["total"] or 0
    pros = row["pros"] or 0
    return {"total": total, "pros": pros, "students": total - pros,
            "admins": row["admins"] or 0}


def questions_per_day(days: int = 14) -> List[dict]:
    """Nombre de questions loggées par jour (derniers `days` jours) — mini-graphe d'activité."""
    since = iso_ago(days=days)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT substr(created_at, 1, 10) AS d, COUNT(*) AS n FROM history "
            "WHERE created_at >= ? GROUP BY d ORDER BY d", (since,)).fetchall()
    return [{"date": r["d"], "count": r["n"]} for r in rows]


def question_stats() -> dict:
    """Volume de questions loggées (utilisateurs connectés) + 24 h + refus/partielles."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM history").fetchone()["n"]
        last24 = conn.execute(
            "SELECT COUNT(*) AS n FROM history WHERE created_at >= ?",
            (iso_ago(hours=24),)).fetchone()["n"]
        partial = conn.execute(
            "SELECT COUNT(*) AS n FROM history WHERE status = 'partial'").fetchone()["n"]
    return {"total": total, "last_24h": last24, "partial": partial}


# ---------- utilisateurs ----------
def list_users(limit: int = 500) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.email, u.plan, u.is_admin, u.created_at, "
            "       COUNT(h.id) AS questions "
            "FROM users u LEFT JOIN history h ON h.user_id = u.id "
            "GROUP BY u.id ORDER BY u.id DESC LIMIT ?", (limit,)).fetchall()
    return [{"id": r["id"], "email": r["email"], "plan": r["plan"],
             "is_admin": bool(r["is_admin"]), "created_at": r["created_at"],
             "questions": r["questions"]} for r in rows]


def set_user_plan(user_id: int, plan: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))
        return cur.rowcount > 0


def set_user_admin(user_id: int, is_admin: bool) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE users SET is_admin = ? WHERE id = ?",
                           (1 if is_admin else 0, user_id))
        return cur.rowcount > 0


def delete_user(user_id: int) -> bool:
    # sessions + history sont supprimés en cascade (PRAGMA foreign_keys = ON dans get_conn).
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cur.rowcount > 0


def user_exists(user_id: int) -> bool:
    with get_conn() as conn:
        return conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is not None


# ---------- suivi des questions (qualité / modération) ----------
def recent_questions(limit: int = 100) -> List[dict]:
    """Dernières questions posées, tous comptes confondus. On expose un extrait de
    réponse (pas la réponse entière) pour un aperçu qualité sans surcharge."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT h.id, u.email, h.question, h.status, h.answer, h.created_at "
            "FROM history h JOIN users u ON u.id = h.user_id "
            "ORDER BY h.id DESC LIMIT ?", (limit,)).fetchall()
    out: List[dict] = []
    for r in rows:
        ans: Optional[str] = r["answer"]
        preview = (ans[:160] + "…") if ans and len(ans) > 160 else ans
        out.append({"id": r["id"], "email": r["email"], "question": r["question"],
                    "status": r["status"], "answer_preview": preview,
                    "created_at": r["created_at"]})
    return out

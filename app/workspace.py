"""V3 — offre cabinet : espaces de travail, membres/rôles, dossiers de recherche partagés.

Un « espace de travail » = un cabinet/une équipe. Rôles : owner > admin > member.
Les dossiers regroupent des questions/réponses sourcées, partagés entre les membres.
SQLite, stdlib. Cloisonnement : chaque endpoint vérifie l'appartenance avant tout accès.
"""
from __future__ import annotations

import datetime
import json
import sqlite3
from typing import List, Optional

from .db import get_conn

ROLES = ("owner", "admin", "member")


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------- espaces de travail ----------
def create_workspace(user_id: int, name: str) -> dict:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO workspaces(name, owner_id, created_at) VALUES (?,?,?)",
                           (name.strip(), user_id, now))
        wid = cur.lastrowid
        conn.execute("INSERT INTO workspace_members(workspace_id, user_id, role, created_at) "
                     "VALUES (?,?,?,?)", (wid, user_id, "owner", now))
    return {"id": wid, "name": name.strip(), "role": "owner", "members": 1}


def list_workspaces(user_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT w.id, w.name, m.role, "
            "  (SELECT COUNT(*) FROM workspace_members mm WHERE mm.workspace_id = w.id) AS members "
            "FROM workspaces w JOIN workspace_members m ON m.workspace_id = w.id "
            "WHERE m.user_id = ? ORDER BY w.id DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def membership(workspace_id: int, user_id: int) -> Optional[str]:
    """Renvoie le rôle de l'utilisateur dans l'espace, ou None s'il n'est pas membre."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id)).fetchone()
    return row["role"] if row else None


# ---------- membres ----------
def add_member(workspace_id: int, email: str, role: str) -> dict:
    email = email.strip().lower()
    if role not in ("admin", "member"):
        raise ValueError("rôle invalide (admin | member)")
    with get_conn() as conn:
        u = conn.execute("SELECT id, email FROM users WHERE email = ?", (email,)).fetchone()
        if not u:
            raise ValueError("aucun compte avec cet email (l'utilisateur doit s'inscrire d'abord)")
        try:
            conn.execute("INSERT INTO workspace_members(workspace_id, user_id, role, created_at) "
                         "VALUES (?,?,?,?)", (workspace_id, u["id"], role, _now()))
        except sqlite3.IntegrityError:
            raise ValueError("cet utilisateur est déjà membre")
    return {"user_id": u["id"], "email": u["email"], "role": role}


def list_members(workspace_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT m.user_id, u.email, m.role, m.created_at FROM workspace_members m "
            "JOIN users u ON u.id = m.user_id WHERE m.workspace_id = ? "
            "ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.email",
            (workspace_id,)).fetchall()
    return [dict(r) for r in rows]


def remove_member(workspace_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        # On ne retire jamais le propriétaire.
        cur = conn.execute("DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ? "
                           "AND role != 'owner'", (workspace_id, user_id))
        return cur.rowcount > 0


# ---------- dossiers ----------
def create_dossier(workspace_id: int, name: str, created_by: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO dossiers(workspace_id, name, created_by, created_at) "
                           "VALUES (?,?,?,?)", (workspace_id, name.strip(), created_by, _now()))
    return {"id": cur.lastrowid, "name": name.strip(), "items": 0}


def list_dossiers(workspace_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.created_at, "
            "  (SELECT COUNT(*) FROM dossier_items i WHERE i.dossier_id = d.id) AS items "
            "FROM dossiers d WHERE d.workspace_id = ? ORDER BY d.id DESC", (workspace_id,)).fetchall()
    return [dict(r) for r in rows]


def dossier_workspace(dossier_id: int) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute("SELECT workspace_id FROM dossiers WHERE id = ?", (dossier_id,)).fetchone()
    return row["workspace_id"] if row else None


def add_item(dossier_id: int, question: str, answer: Optional[str],
             citations: List[dict], status: Optional[str], added_by: int) -> dict:
    cites = json.dumps(citations)[:60000]
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO dossier_items(dossier_id, question, answer, citations, status, added_by, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (dossier_id, question[:2000], (answer or "")[:20000], cites, status, added_by, _now()))
    return {"id": cur.lastrowid}


def list_items(dossier_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.id, i.question, i.answer, i.citations, i.status, i.created_at, u.email AS added_by "
            "FROM dossier_items i LEFT JOIN users u ON u.id = i.added_by "
            "WHERE i.dossier_id = ? ORDER BY i.id DESC", (dossier_id,)).fetchall()
    out = []
    for r in rows:
        try:
            cites = json.loads(r["citations"] or "[]")
        except Exception:
            cites = []
        out.append({"id": r["id"], "question": r["question"], "answer": r["answer"],
                    "citations": cites, "status": r["status"], "created_at": r["created_at"],
                    "added_by": r["added_by"]})
    return out

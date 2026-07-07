"""V3 — offre cabinet : espaces de travail, membres/rôles, dossiers de recherche partagés.

Un « espace de travail » = un cabinet/une équipe. Rôles : owner > admin > member.
Les dossiers regroupent des questions/réponses sourcées, partagés entre les membres.
SQLite, stdlib. Cloisonnement : chaque endpoint vérifie l'appartenance avant tout accès.
"""
from __future__ import annotations

import json
import sqlite3
from typing import List, Optional

from .db import get_conn, loads_list, now_iso as _now

ROLES = ("owner", "admin", "member")


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


def set_member_role(workspace_id: int, user_id: int, role: str) -> bool:
    if role not in ("admin", "member"):
        raise ValueError("rôle invalide (admin | member)")
    with get_conn() as conn:
        cur = conn.execute("UPDATE workspace_members SET role = ? WHERE workspace_id = ? "
                           "AND user_id = ? AND role != 'owner'", (role, workspace_id, user_id))
        return cur.rowcount > 0


def delete_workspace(workspace_id: int) -> bool:
    # Cascade : membres, dossiers, items (ON DELETE CASCADE + PRAGMA foreign_keys ON).
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
        return cur.rowcount > 0


# ---------- dossiers ----------
def create_dossier(workspace_id: int, name: str, created_by: int) -> dict:
    with get_conn() as conn:
        cur = conn.execute("INSERT INTO dossiers(workspace_id, name, created_by, created_at) "
                           "VALUES (?,?,?,?)", (workspace_id, name.strip(), created_by, _now()))
    return {"id": cur.lastrowid, "name": name.strip(), "items": 0}


def delete_dossier(dossier_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM dossiers WHERE id = ?", (dossier_id,))
        return cur.rowcount > 0


def list_dossiers(workspace_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT d.id, d.name, d.created_at, d.restricted, "
            "  (SELECT COUNT(*) FROM dossier_items i WHERE i.dossier_id = d.id) AS items "
            "FROM dossiers d WHERE d.workspace_id = ? ORDER BY d.id DESC", (workspace_id,)).fetchall()
    return [{**dict(r), "restricted": bool(r["restricted"])} for r in rows]


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


# ---------- cloisons déontologiques (ethical walls) ----------
def set_restricted(dossier_id: int, restricted: bool) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE dossiers SET restricted = ? WHERE id = ?",
                     (1 if restricted else 0, dossier_id))


def grant_access(dossier_id: int, user_id: int) -> None:
    with get_conn() as conn:
        conn.execute("INSERT OR IGNORE INTO dossier_access(dossier_id, user_id) VALUES (?,?)",
                     (dossier_id, user_id))


def revoke_access(dossier_id: int, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM dossier_access WHERE dossier_id = ? AND user_id = ?",
                           (dossier_id, user_id))
        return cur.rowcount > 0


def membership_by_email(workspace_id: int, email: str) -> Optional[int]:
    """user_id d'un membre de l'espace identifié par e-mail, ou None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT m.user_id FROM workspace_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.workspace_id = ? AND u.email = ?",
            (workspace_id, email.strip().lower())).fetchone()
    return row["user_id"] if row else None


def can_access_dossier(dossier_id: int, user_id: int, role: str) -> bool:
    """Dossier NON restreint = visible par tout membre. Restreint = owner/admin de l'espace
    + utilisateurs explicitement autorisés (cloison déontologique / conflits d'intérêts)."""
    with get_conn() as conn:
        row = conn.execute("SELECT restricted FROM dossiers WHERE id = ?", (dossier_id,)).fetchone()
        if row is None:
            return False
        if not row["restricted"] or role in ("owner", "admin"):
            return True
        acc = conn.execute("SELECT 1 FROM dossier_access WHERE dossier_id = ? AND user_id = ?",
                           (dossier_id, user_id)).fetchone()
        return acc is not None


_MAX_ITEMS = 500  # borne défensive (un dossier partagé peut grossir sans limite)


def list_items(dossier_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.id, i.question, i.answer, i.citations, i.status, i.created_at, u.email AS added_by "
            "FROM dossier_items i LEFT JOIN users u ON u.id = i.added_by "
            "WHERE i.dossier_id = ? ORDER BY i.id DESC LIMIT ?", (dossier_id, _MAX_ITEMS)).fetchall()
    return [{"id": r["id"], "question": r["question"], "answer": r["answer"],
             "citations": loads_list(r["citations"]), "status": r["status"],
             "created_at": r["created_at"], "added_by": r["added_by"]} for r in rows]

"""Bibliothèque de prompts/skills réutilisables (concurrent : Legora, CoCounsel).

Un prompt est **personnel** (workspace_id NULL) ou **partagé au cabinet** (workspace_id).
Un membre voit ses prompts persos + ceux des espaces dont il est membre. SQLite, stdlib.
"""
from __future__ import annotations

from typing import List, Optional

from .db import clause_perso_cabinet, get_conn, now_iso, portee

_MAX_TITRE = 200
_MAX_CORPS = 20000


def create(owner_id: int, title: str, body: str, workspace_id: Optional[int] = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO prompts(owner_id, workspace_id, title, body, created_at) VALUES (?,?,?,?,?)",
            (owner_id, workspace_id, title.strip()[:_MAX_TITRE], body[:_MAX_CORPS], now_iso()))
    return {"id": cur.lastrowid, "title": title.strip()[:_MAX_TITRE],
            "workspace_id": workspace_id, "scope": portee(workspace_id)}


def visibles(owner_id: int, workspace_ids: List[int]) -> List[dict]:
    """Prompts persos de l'utilisateur + prompts partagés des espaces dont il est membre."""
    where, args = clause_perso_cabinet(owner_id, workspace_ids)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, owner_id, workspace_id, title, body, created_at FROM prompts "
            f"WHERE {where} ORDER BY id DESC", args).fetchall()
    return [{**dict(r), "scope": portee(r["workspace_id"])} for r in rows]


def delete(prompt_id: int, owner_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM prompts WHERE id = ? AND owner_id = ?", (prompt_id, owner_id))
        return cur.rowcount > 0

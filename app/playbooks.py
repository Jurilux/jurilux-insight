"""Playbooks de revue de contrats (concurrent : Spellbook, Luminance, Robin AI).

Un playbook = un jeu de **règles** maison (`{label, instruction}`) qu'on applique
automatiquement à un contrat déposé au Vault. Perso (workspace_id NULL) ou partagé au
cabinet. La revue elle-même est faite par `rag.revue_contrat`. SQLite, stdlib.
"""
from __future__ import annotations

import json
from typing import List, Optional

from .db import clause_perso_cabinet, get_conn, loads_list, now_iso, portee

_MAX_NOM = 200
_MAX_REGLES = 60


def _norm_rules(rules: list) -> list:
    """Nettoie/borne les règles ({label, instruction})."""
    out = []
    for r in (rules or [])[:_MAX_REGLES]:
        label = str(r.get("label", "")).strip()[:200]
        instr = str(r.get("instruction", "")).strip()[:2000]
        if label and instr:
            out.append({"label": label, "instruction": instr})
    return out


def create(owner_id: int, name: str, rules: list, workspace_id: Optional[int] = None) -> dict:
    normed = _norm_rules(rules)
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO playbooks(owner_id, workspace_id, name, rules, created_at) VALUES (?,?,?,?,?)",
            (owner_id, workspace_id, name.strip()[:_MAX_NOM], json.dumps(normed), now_iso()))
    return {"id": cur.lastrowid, "name": name.strip()[:_MAX_NOM], "workspace_id": workspace_id,
            "scope": portee(workspace_id), "rules": normed}


def _row_to_dict(r) -> dict:
    return {"id": r["id"], "name": r["name"], "workspace_id": r["workspace_id"],
            "scope": portee(r["workspace_id"]), "rules": loads_list(r["rules"]),
            "created_at": r["created_at"]}


def visibles(owner_id: int, workspace_ids: List[int]) -> List[dict]:
    where, args = clause_perso_cabinet(owner_id, workspace_ids)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, owner_id, workspace_id, name, rules, created_at FROM playbooks "
            f"WHERE {where} ORDER BY id DESC", args).fetchall()
    return [_row_to_dict(r) for r in rows]


def get(playbook_id: int, owner_id: int, workspace_ids: List[int]) -> Optional[dict]:
    """Playbook accessible = perso de l'utilisateur OU partagé d'un espace dont il est membre."""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id, owner_id, workspace_id, name, rules, created_at FROM playbooks WHERE id = ?",
            (playbook_id,)).fetchone()
    if not r:
        return None
    if r["workspace_id"] is None:
        return _row_to_dict(r) if r["owner_id"] == owner_id else None
    return _row_to_dict(r) if r["workspace_id"] in (workspace_ids or []) else None


def delete(playbook_id: int, owner_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM playbooks WHERE id = ? AND owner_id = ?", (playbook_id, owner_id))
        return cur.rowcount > 0

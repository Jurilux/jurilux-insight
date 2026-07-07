"""Clés d'API de service (socle on-prem) : jetons pour intégrations/automatisations cabinet.

Même philosophie que l'auth : la clé n'est **jamais stockée en clair** (hachage sha-256),
montrée une seule fois à la création. Vérification via l'en-tête `X-API-Key`. SQLite, stdlib.
"""
from __future__ import annotations

import hashlib
import secrets
from typing import List, Optional

from .db import get_conn, iso_ago, now_iso

_PREFIXE = "jlx_"


def _hash(cle: str) -> str:
    return hashlib.sha256(cle.encode()).hexdigest()


def create(owner_id: int, name: str) -> dict:
    """Crée une clé et renvoie sa valeur EN CLAIR (une seule fois) + ses métadonnées."""
    cle = _PREFIXE + secrets.token_urlsafe(24)
    prefix = cle[:12]
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_keys(owner_id, name, key_hash, prefix, created_at) VALUES (?,?,?,?,?)",
            (owner_id, name.strip() or "clé", _hash(cle), prefix, now_iso()))
    return {"id": cur.lastrowid, "name": name.strip() or "clé", "prefix": prefix, "key": cle}


def list_keys(owner_id: int) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, prefix, created_at, last_used_at, revoked FROM api_keys "
            "WHERE owner_id = ? ORDER BY id DESC", (owner_id,)).fetchall()
    return [{**dict(r), "revoked": bool(r["revoked"])} for r in rows]


def revoke(key_id: int, owner_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("UPDATE api_keys SET revoked = 1 WHERE id = ? AND owner_id = ? AND revoked = 0",
                           (key_id, owner_id))
        return cur.rowcount > 0


def user_for_key(cle: Optional[str]) -> Optional[dict]:
    """Renvoie l'utilisateur propriétaire d'une clé valide (non révoquée), sinon None."""
    if not cle:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT k.id AS kid, k.last_used_at, u.id, u.email, u.plan, u.is_admin FROM api_keys k "
            "JOIN users u ON u.id = k.owner_id WHERE k.key_hash = ? AND k.revoked = 0",
            (_hash(cle),)).fetchone()
        if not row:
            return None
        # Trace d'usage throttlée : une écriture au plus toutes les ~5 min (évite un write-lock
        # SQLite par requête sur le chemin chaud /api/ask authentifié par clé).
        if not row["last_used_at"] or row["last_used_at"] < iso_ago(minutes=5):
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now_iso(), row["kid"]))
    return {"id": row["id"], "email": row["email"], "plan": row["plan"], "is_admin": row["is_admin"]}

"""Permaliens de réponses partageables.

Un instantané (question + réponse + citations) est stocké sous un identifiant court ;
n'importe qui avec le lien peut le consulter (page /r/<id> côté front). Canal
d'acquisition « produit » : partager une réponse sourcée = démo gratuite. SQLite, stdlib.
"""
from __future__ import annotations

import datetime
import json
import secrets
from typing import List, Optional

from .db import get_conn

_MAX_Q = 2000
_MAX_A = 20000
_MAX_CITES = 60000


def create(user_id: Optional[int], question: str, answer: Optional[str],
           citations: List[dict], status: Optional[str]) -> str:
    token = secrets.token_urlsafe(8)
    cites = json.dumps(citations)[:_MAX_CITES]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO shares(id, user_id, question, answer, citations, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (token, user_id, question[:_MAX_Q], (answer or "")[:_MAX_A], cites, status,
             datetime.datetime.now(datetime.timezone.utc).isoformat()))
    return token


def get(token: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT question, answer, citations, status, created_at FROM shares WHERE id = ?",
            (token,)).fetchone()
    if not row:
        return None
    try:
        cites = json.loads(row["citations"] or "[]")
    except Exception:
        cites = []
    return {"question": row["question"], "answer": row["answer"], "citations": cites,
            "status": row["status"], "created_at": row["created_at"]}

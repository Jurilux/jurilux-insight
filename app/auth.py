"""Authentification par comptes (SQLite) — hachage pbkdf2 + tokens de session opaques.

Aucune dépendance externe : hashlib/secrets/hmac (stdlib). Les tokens sont aléatoires,
stockés hachés (sha256) en base ; révocables (logout). Renvoyés en clair au client une
seule fois, à mettre dans l'en-tête Authorization: Bearer <token>.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from typing import Optional

from .config import settings
from .db import get_conn

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PBKDF2_ROUNDS = 200_000


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.isoformat()


# ---------- mots de passe ----------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(),
                                 bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


# ---------- utilisateurs ----------
def create_user(email: str, password: str) -> dict:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("email invalide")
    if len(password) < 8:
        raise ValueError("mot de passe trop court (8 caractères minimum)")
    with get_conn() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users(email, password_hash, created_at) VALUES (?,?,?)",
                (email, hash_password(password), _iso(_now())))
        except sqlite3.IntegrityError:
            raise ValueError("email déjà utilisé")
        return {"id": cur.lastrowid, "email": email, "plan": "student", "is_admin": 0}


def authenticate(email: str, password: str) -> Optional[dict]:
    email = email.strip().lower()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, email, password_hash, plan, is_admin FROM users WHERE email = ?", (email,)).fetchone()
    if row and verify_password(password, row["password_hash"]):
        return {"id": row["id"], "email": row["email"], "plan": row["plan"], "is_admin": row["is_admin"]}
    return None


def change_password(user_id: int, old_password: str, new_password: str) -> bool:
    """Vérifie l'ancien mot de passe puis le remplace. Renvoie False si l'ancien
    est incorrect (ou l'utilisateur introuvable). La longueur du nouveau est validée
    en amont par l'appelant. Les sessions existantes restent valides."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not verify_password(old_password, row["password_hash"]):
            return False
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (hash_password(new_password), user_id))
    return True


# ---------- sessions ----------
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    exp = now + datetime.timedelta(days=settings.session_days)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (_token_hash(token), user_id, _iso(now), _iso(exp)))
    return token


def user_for_token(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT s.expires_at, u.id, u.email, u.plan, u.is_admin FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_token_hash(token),)).fetchone()
    if not row:
        return None
    if datetime.datetime.fromisoformat(row["expires_at"]) < _now():
        return None
    return {"id": row["id"], "email": row["email"], "plan": row["plan"], "is_admin": row["is_admin"]}


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


# ---------- historique ----------
def add_history(user_id: int, question: str, answer: Optional[str], status: Optional[str]) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO history(user_id, question, answer, status, created_at) VALUES (?,?,?,?,?)",
            (user_id, question, answer, status, _iso(_now())))


def list_history(user_id: int, limit: int = 50) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, question, answer, status, created_at FROM history "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    return [dict(r) for r in rows]


def token_from_header(authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


# ---------- quota (plan étudiant, freemium) ----------
def _month_start_iso() -> str:
    now = _now()
    return _iso(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))


def monthly_usage(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM history WHERE user_id = ? AND created_at >= ?",
            (user_id, _month_start_iso())).fetchone()
    return row["n"]


def quota_info(user: dict) -> dict:
    """limit=None => illimité (plan pro). Le quota étudiant se réinitialise chaque mois."""
    plan = user.get("plan", "student")
    limit = settings.student_monthly_quota if plan == "student" else None
    used = monthly_usage(user["id"])
    remaining = None if limit is None else max(0, limit - used)
    return {"plan": plan, "limit": limit, "used": used, "remaining": remaining}

"""Persistance SQLite pour l'espace utilisateur (comptes, sessions, historique).

Léger et sans service supplémentaire ; fichier sur volume persistant (docker-compose).
"""
from __future__ import annotations

import os
import sqlite3

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    plan          TEXT NOT NULL DEFAULT 'student',
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    question   TEXT NOT NULL,
    answer     TEXT,
    status     TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    d = os.path.dirname(settings.db_path)
    if d:
        os.makedirs(d, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # migration : ajouter `plan` si la table users préexiste sans cette colonne
        try:
            conn.execute("ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'student'")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente

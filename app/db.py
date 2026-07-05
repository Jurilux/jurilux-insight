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
    is_admin      INTEGER NOT NULL DEFAULT 0,
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
CREATE TABLE IF NOT EXISTS feedback (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    question       TEXT NOT NULL,
    helpful        INTEGER NOT NULL,
    missing        TEXT,
    status         TEXT,
    prompt_version TEXT,
    created_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
    id         TEXT PRIMARY KEY,
    user_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    question   TEXT NOT NULL,
    answer     TEXT,
    citations  TEXT,
    status     TEXT,
    created_at TEXT NOT NULL
);
-- V3 offre cabinet : espaces de travail (cabinets) + membres/rôles + dossiers partagés.
CREATE TABLE IF NOT EXISTS workspaces (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',   -- owner | admin | member
    created_at   TEXT NOT NULL,
    PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS dossiers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    created_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dossier_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id) ON DELETE CASCADE,
    question   TEXT NOT NULL,
    answer     TEXT,
    citations  TEXT,
    status     TEXT,
    added_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
-- V3 : alertes « nouvelle jurisprudence sur mes sujets » (veille in-app, sans e-mail).
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    query       TEXT NOT NULL,
    source_type TEXT,
    created_at  TEXT NOT NULL,
    checked_at  TEXT
);
CREATE TABLE IF NOT EXISTS alert_hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id        INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
    doc_id          TEXT NOT NULL,
    source_type     TEXT,
    title           TEXT,
    year            INTEGER,
    juridiction_key TEXT,
    url             TEXT,
    pdf_url         TEXT,
    seen            INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    UNIQUE(alert_id, doc_id)
);
-- Insight : profiling des AVOCATS uniquement (données publiques de jurisprudence).
-- Une ligne = un avocat présent dans une décision (dédupliqué par (name_key, doc_id)).
CREATE TABLE IF NOT EXISTS insight_appearances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name_key        TEXT NOT NULL,      -- clé normalisée (majuscules, sans accents) pour regrouper les variantes
    display_name    TEXT NOT NULL,      -- forme lisible affichée
    doc_id          TEXT NOT NULL,
    year            INTEGER,
    juridiction_key TEXT,
    side            TEXT,               -- 'A' (demandeur/appelant) | 'B' (défendeur/intimé) | NULL
    won             INTEGER,            -- 1 gagné (estimé) | 0 perdu (estimé) | NULL indéterminé
    UNIQUE(name_key, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_insight_name ON insight_appearances(name_key);
CREATE INDEX IF NOT EXISTS idx_insight_doc ON insight_appearances(doc_id);
CREATE INDEX IF NOT EXISTS idx_history_user ON history(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback ON feedback(id DESC);
CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(user_id);
CREATE INDEX IF NOT EXISTS idx_dossiers_ws ON dossiers(workspace_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_dossier_items ON dossier_items(dossier_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_user ON alerts(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_alert_hits ON alert_hits(alert_id, id DESC);
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
        # migrations : ajouter les colonnes si la table users préexiste sans elles
        for ddl in (
            "ALTER TABLE users ADD COLUMN plan TEXT NOT NULL DEFAULT 'student'",
            "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE insight_appearances ADD COLUMN side TEXT",     # 'A' (demandeur/appelant) | 'B' (défendeur/intimé)
            "ALTER TABLE insight_appearances ADD COLUMN won INTEGER",   # 1 gagné (estimé) | 0 perdu | NULL indéterminé
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # colonne déjà présente

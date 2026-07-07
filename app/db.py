"""Persistance SQLite pour l'espace utilisateur (comptes, sessions, historique).

Léger et sans service supplémentaire ; fichier sur volume persistant (docker-compose).
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3

from .config import settings


# --------- petits utilitaires partagés (évitent la redéfinition par module) ---------
def now_iso() -> str:
    """Horodatage UTC ISO-8601 (foyer commun ; remplace les _now_iso() par module)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def iso_ago(days: int = 0, hours: int = 0, minutes: int = 0) -> str:
    """ISO-8601 de « maintenant moins un délai » (rétention/fenêtres temporelles)."""
    dt = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(days=days, hours=hours, minutes=minutes))
    return dt.isoformat()


def loads_list(raw) -> list:
    """Décode une liste JSON de façon défensive (colonnes citations/rules…) → [] si invalide."""
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


def portee(workspace_id) -> str:
    return "cabinet" if workspace_id else "perso"


def clause_perso_cabinet(owner_id: int, workspace_ids) -> tuple:
    """Fragment WHERE + args : entité PERSO (owner) OU PARTAGÉE d'un espace dont on est membre.
    Mutualise la visibilité perso/cabinet (prompts, playbooks)."""
    clauses = ["(workspace_id IS NULL AND owner_id = ?)"]
    args: list = [owner_id]
    if workspace_ids:
        marks = ",".join("?" for _ in workspace_ids)
        clauses.append(f"workspace_id IN ({marks})")
        args.extend(workspace_ids)
    return " OR ".join(clauses), args

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
    matter          TEXT,               -- domaine de droit dominant de la décision (heuristique)
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
-- Vault : documents privés déposés par l'utilisateur (chunks indexés à part dans Meili).
CREATE TABLE IF NOT EXISTS vault_documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename   TEXT NOT NULL,
    mime       TEXT,
    status     TEXT NOT NULL DEFAULT 'indexing',   -- indexing | ready | error
    n_chunks   INTEGER NOT NULL DEFAULT 0,
    text       TEXT,                                -- texte extrait (pour l'analyse/citations)
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vault_owner ON vault_documents(owner_id, id DESC);
-- Socle entreprise : journal d'audit (qui/quoi/quand, local, souverain).
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    email   TEXT,
    action  TEXT NOT NULL,   -- ex. auth.login, vault.upload, admin.set_plan
    detail  TEXT,
    ip      TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(id DESC);
-- Socle entreprise : clés d'API de service (intégrations/automatisations cabinet).
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    key_hash     TEXT UNIQUE NOT NULL,   -- hachage de la clé (jamais stockée en clair)
    prefix       TEXT NOT NULL,          -- préfixe lisible pour l'identifier
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    revoked      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_apikeys_owner ON api_keys(owner_id, id DESC);
-- Bibliothèque de prompts/skills réutilisables (perso ou partagés au cabinet).
CREATE TABLE IF NOT EXISTS prompts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(id) ON DELETE CASCADE,  -- NULL = perso
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_prompts_owner ON prompts(owner_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_prompts_ws ON prompts(workspace_id, id DESC);
-- Paramétrage runtime (réglages non-secrets modifiables sans redéploiement).
CREATE TABLE IF NOT EXISTS app_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Cloisons déontologiques : accès nominatif à un dossier restreint (conflits d'intérêts).
CREATE TABLE IF NOT EXISTS dossier_access (
    dossier_id INTEGER NOT NULL REFERENCES dossiers(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (dossier_id, user_id)
);
-- Revue de contrats : playbooks (jeux de règles) perso ou partagés au cabinet.
CREATE TABLE IF NOT EXISTS playbooks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INTEGER REFERENCES workspaces(id) ON DELETE CASCADE,  -- NULL = perso
    name         TEXT NOT NULL,
    rules        TEXT NOT NULL,   -- JSON: [{label, instruction}]
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_playbooks_owner ON playbooks(owner_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_playbooks_ws ON playbooks(workspace_id, id DESC);
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
            "ALTER TABLE insight_appearances ADD COLUMN matter TEXT",   # domaine de droit dominant
            "ALTER TABLE dossiers ADD COLUMN restricted INTEGER NOT NULL DEFAULT 0",  # cloison déontologique
        ):
            try:
                conn.execute(ddl)
            except sqlite3.OperationalError:
                pass  # colonne déjà présente
        _appliquer_config(conn)


def _appliquer_config(conn) -> None:
    """Charge les réglages runtime persistés (table app_config) sur l'objet settings."""
    try:
        rows = conn.execute("SELECT key, value FROM app_config").fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        _caster_sur_settings(r["key"], r["value"])


def _caster_sur_settings(key: str, value: str) -> None:
    """Applique une valeur (str) à settings en respectant le type de l'attribut existant."""
    if not hasattr(settings, key):
        return
    actuel = getattr(settings, key)
    try:
        if isinstance(actuel, bool):
            val = value.lower() in ("1", "true", "vrai", "oui")
        elif isinstance(actuel, int):
            val = int(value)
        elif isinstance(actuel, float):
            val = float(value)
        else:
            val = value
        setattr(settings, key, val)
    except (ValueError, TypeError):
        pass

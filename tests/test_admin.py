"""Tests du backoffice admin : garde is_admin, stats, gestion des comptes."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db, search
from app.main import app

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _register(email: str, password: str = "password123") -> str:
    return client.post("/api/auth/register",
                       json={"email": email, "password": password}).json()["token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_admin_guard_rejects_non_admin(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "")
    tok = _register("user@b.com")
    assert client.get("/api/admin/overview", headers=_h(tok)).status_code == 403
    assert client.get("/api/admin/overview").status_code == 401  # anonyme


def test_admin_allowlist_grants_access(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    monkeypatch.setattr(search, "meili_healthy", lambda: True)
    monkeypatch.setattr(search, "corpus_overview", lambda: {"decisions": 1})
    monkeypatch.setattr(search, "index_stats", lambda: {"documents": 42, "is_indexing": False})
    tok = _register("boss@b.com")
    # /api/me expose bien is_admin
    assert client.get("/api/me", headers=_h(tok)).json()["user"]["is_admin"] is True
    ov = client.get("/api/admin/overview", headers=_h(tok))
    assert ov.status_code == 200
    body = ov.json()
    assert set(body) >= {"metrics", "corpus", "index", "health", "users", "questions"}
    assert body["index"]["documents"] == 42
    assert body["users"]["total"] == 1 and body["users"]["admins"] == 0  # allowlist ≠ flag


def test_admin_user_management(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    admin_tok = _register("boss@b.com")
    _register("alice@b.com")  # user id 2
    users = client.get("/api/admin/users", headers=_h(admin_tok)).json()["items"]
    assert len(users) == 2
    alice = next(u for u in users if u["email"] == "alice@b.com")

    # changer le plan
    r = client.post(f"/api/admin/users/{alice['id']}/plan",
                    json={"plan": "pro"}, headers=_h(admin_tok))
    assert r.status_code == 200
    users = client.get("/api/admin/users", headers=_h(admin_tok)).json()["items"]
    assert next(u for u in users if u["id"] == alice["id"])["plan"] == "pro"
    # plan invalide
    assert client.post(f"/api/admin/users/{alice['id']}/plan",
                       json={"plan": "gold"}, headers=_h(admin_tok)).status_code == 400

    # promouvoir admin (flag en base)
    assert client.post(f"/api/admin/users/{alice['id']}/admin",
                       json={"is_admin": True}, headers=_h(admin_tok)).status_code == 200
    users = client.get("/api/admin/users", headers=_h(admin_tok)).json()["items"]
    assert next(u for u in users if u["id"] == alice["id"])["is_admin"] is True

    # supprimer alice
    assert client.delete(f"/api/admin/users/{alice['id']}", headers=_h(admin_tok)).status_code == 200
    assert len(client.get("/api/admin/users", headers=_h(admin_tok)).json()["items"]) == 1
    # 404 sur inconnu
    assert client.delete("/api/admin/users/999", headers=_h(admin_tok)).status_code == 404


def test_admin_cannot_lock_self_out(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    admin_tok = _register("boss@b.com")
    me = client.get("/api/me", headers=_h(admin_tok))
    # récupérer son id via la liste
    uid = client.get("/api/admin/users", headers=_h(admin_tok)).json()["items"][0]["id"]
    assert me.status_code == 200
    # ni auto-suppression ni auto-rétrogradation
    assert client.delete(f"/api/admin/users/{uid}", headers=_h(admin_tok)).status_code == 400
    assert client.post(f"/api/admin/users/{uid}/admin",
                       json={"is_admin": False}, headers=_h(admin_tok)).status_code == 400


def test_admin_probe(temp_db, monkeypatch):
    from app.search import Hit
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    monkeypatch.setattr(search, "search", lambda q, k, f: [
        Hit(chunk_id="c1", doc_id="eli-etat-leg-code-travail-x", text="Période d'essai...",
            title=None, source_type="law")])
    tok = _register("boss@b.com")
    assert client.post("/api/admin/probe", json={"q": "période d'essai"}).status_code == 401  # anonyme
    d = client.post("/api/admin/probe", json={"q": "période d'essai"}, headers=_h(tok)).json()
    assert d["count"] == 1
    assert d["hits"][0]["doc_id"] == "eli-etat-leg-code-travail-x"
    assert d["hits"][0]["source_type"] == "law"
    assert "essai" in d["hits"][0]["snippet"]


def test_admin_eval(temp_db, monkeypatch):
    from app.search import Hit
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    monkeypatch.setattr(search, "search", lambda q, k, f: [
        Hit(chunk_id="l1", doc_id="eli-code-travail-x", text="x", source_type="law"),
        Hit(chunk_id="j1", doc_id="csj-1", text="y", source_type="jurisprudence")])
    tok = _register("boss@b.com")
    d = client.get("/api/admin/eval", headers=_h(tok)).json()
    assert d["total"] == 10 and d["with_law"] == 10 and d["with_juris"] == 10
    assert len(d["results"]) == 10 and d["results"][0]["has_law"] is True
    assert client.get("/api/admin/eval").status_code == 401  # anonyme


def test_admin_activity(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    monkeypatch.setattr(search, "search", lambda q, k, f: [])
    tok = _register("boss@b.com")
    client.post("/api/ask", json={"q": "une question"}, headers=_h(tok))  # logge dans history
    d = client.get("/api/admin/activity", headers=_h(tok)).json()
    assert "per_day" in d
    assert sum(x["count"] for x in d["per_day"]) >= 1


def test_admin_questions_feed(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    monkeypatch.setattr(search, "search", lambda q, k, f: [])  # refus "hors corpus", loggé
    admin_tok = _register("boss@b.com")
    client.post("/api/ask", json={"q": "question suivie"}, headers=_h(admin_tok))
    items = client.get("/api/admin/questions", headers=_h(admin_tok)).json()["items"]
    assert items and items[0]["question"] == "question suivie"
    assert items[0]["email"] == "boss@b.com"

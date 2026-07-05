"""Tests V3 alertes : création + premier check, dédup, lecture, suppression, cloisonnement."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db, search
from app.main import app
from app.search import Hit

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _tok(email: str) -> str:
    return client.post("/api/auth/register",
                       json={"email": email, "password": "password123"}).json()["token"]


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


HITS = [
    Hit(chunk_id="c1", doc_id="20240101_JPLTRAVAIL_1", text="x", title="CSJ", year=2024,
        juridiction_key="csj", source_type="jurisprudence"),
    Hit(chunk_id="c2", doc_id="20240202_JPLTRAVAIL_2", text="y", title="Trib", year=2024,
        source_type="jurisprudence"),
]


def test_alert_flow(temp_db, monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    tok = _tok("u@a.lu")

    # créer -> premier check remonte les 2 décisions actuelles
    r = client.post("/api/alerts", json={"query": "faute grave"}, headers=_h(tok))
    assert r.status_code == 200
    aid = r.json()["id"]
    assert r.json()["unseen"] == 2

    # liste : 1 alerte, 2 non lues
    items = client.get("/api/alerts", headers=_h(tok)).json()["items"]
    assert len(items) == 1 and items[0]["unseen"] == 2 and items[0]["total"] == 2

    # re-check sans nouveauté -> 0 nouveau (dédup par doc_id)
    assert client.post(f"/api/alerts/{aid}/check", headers=_h(tok)).json()["new"] == 0

    # une nouvelle décision apparaît -> 1 nouveau
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS + [
        Hit(chunk_id="c3", doc_id="20240303_NEW_3", text="z", source_type="jurisprudence")])
    assert client.post(f"/api/alerts/{aid}/check", headers=_h(tok)).json()["new"] == 1

    # ouvrir les hits -> 3 au total, puis tout est marqué lu
    hits = client.get(f"/api/alerts/{aid}/hits", headers=_h(tok)).json()["items"]
    assert len(hits) == 3
    assert client.get("/api/alerts", headers=_h(tok)).json()["items"][0]["unseen"] == 0

    # cloisonnement : un autre user ne voit pas cette alerte
    other = _tok("v@a.lu")
    assert client.get(f"/api/alerts/{aid}/hits", headers=_h(other)).status_code == 404
    assert client.delete(f"/api/alerts/{aid}", headers=_h(other)).status_code == 404

    # suppression par le propriétaire
    assert client.delete(f"/api/alerts/{aid}", headers=_h(tok)).status_code == 200
    assert client.get("/api/alerts", headers=_h(tok)).json()["items"] == []


def test_alert_requires_auth(temp_db):
    assert client.get("/api/alerts").status_code == 401
    assert client.post("/api/alerts", json={"query": "faute grave"}).status_code == 401

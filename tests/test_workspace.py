"""Tests V3 offre cabinet : espaces, rôles, cloisonnement, dossiers partagés."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db
from app.main import app

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


def test_workspace_lifecycle_and_isolation(temp_db):
    owner = _tok("owner@cab.lu")
    alice = _tok("alice@cab.lu")
    bob = _tok("bob@ext.lu")  # extérieur

    # créer un espace -> créateur = owner
    r = client.post("/api/workspaces", json={"name": "Cabinet Test"}, headers=_h(owner))
    assert r.status_code == 200
    wid = r.json()["id"]
    assert r.json()["role"] == "owner"
    assert client.get("/api/workspaces", headers=_h(owner)).json()["items"][0]["members"] == 1

    # cloisonnement : un non-membre ne voit rien (404)
    assert client.get(f"/api/workspaces/{wid}/members", headers=_h(bob)).status_code == 404
    assert client.get(f"/api/workspaces/{wid}/dossiers", headers=_h(bob)).status_code == 404

    # ajouter alice (admin owner requis)
    r = client.post(f"/api/workspaces/{wid}/members",
                    json={"email": "alice@cab.lu", "role": "member"}, headers=_h(owner))
    assert r.status_code == 200
    # email inexistant -> 400
    assert client.post(f"/api/workspaces/{wid}/members",
                       json={"email": "nobody@x.lu", "role": "member"}, headers=_h(owner)).status_code == 400
    # alice (member) ne peut PAS ajouter de membre
    assert client.post(f"/api/workspaces/{wid}/members",
                       json={"email": "bob@ext.lu", "role": "member"}, headers=_h(alice)).status_code == 403

    # alice voit maintenant l'espace et ses 2 membres
    assert len(client.get("/api/workspaces", headers=_h(alice)).json()["items"]) == 1
    assert len(client.get(f"/api/workspaces/{wid}/members", headers=_h(alice)).json()["items"]) == 2


def test_shared_dossiers(temp_db):
    owner = _tok("o@cab.lu")
    alice = _tok("a@cab.lu")
    wid = client.post("/api/workspaces", json={"name": "Cab"}, headers=_h(owner)).json()["id"]
    client.post(f"/api/workspaces/{wid}/members", json={"email": "a@cab.lu", "role": "member"}, headers=_h(owner))

    # owner crée un dossier + y ajoute une réponse
    did = client.post(f"/api/workspaces/{wid}/dossiers", json={"name": "Affaire Durand"}, headers=_h(owner)).json()["id"]
    client.post(f"/api/dossiers/{did}/items", headers=_h(owner), json={
        "question": "Préavis licenciement ?", "answer": "3 mois... [doc]",
        "citations": [{"doc_id": "eli-code-travail", "source_type": "law"}], "status": "ok"})

    # alice (autre membre) VOIT le dossier et son contenu (partage)
    dossiers = client.get(f"/api/workspaces/{wid}/dossiers", headers=_h(alice)).json()["items"]
    assert dossiers[0]["name"] == "Affaire Durand" and dossiers[0]["items"] == 1
    items = client.get(f"/api/dossiers/{did}/items", headers=_h(alice)).json()["items"]
    assert items[0]["question"] == "Préavis licenciement ?"
    assert items[0]["citations"][0]["doc_id"] == "eli-code-travail"
    assert items[0]["added_by"] == "o@cab.lu"

    # un extérieur n'accède pas au dossier
    ext = _tok("ext@x.lu")
    assert client.get(f"/api/dossiers/{did}/items", headers=_h(ext)).status_code in (403, 404)


def test_workspace_management(temp_db):
    owner = _tok("own@c.lu")
    alice = _tok("al@c.lu")
    wid = client.post("/api/workspaces", json={"name": "C"}, headers=_h(owner)).json()["id"]
    client.post(f"/api/workspaces/{wid}/members", json={"email": "al@c.lu", "role": "member"}, headers=_h(owner))
    uid_alice = next(m["user_id"] for m in
                     client.get(f"/api/workspaces/{wid}/members", headers=_h(owner)).json()["items"]
                     if m["email"] == "al@c.lu")

    # owner promeut alice admin ; ne peut pas changer son propre rôle
    assert client.post(f"/api/workspaces/{wid}/members/{uid_alice}/role",
                       json={"role": "admin"}, headers=_h(owner)).status_code == 200
    uid_owner = next(m["user_id"] for m in
                     client.get(f"/api/workspaces/{wid}/members", headers=_h(owner)).json()["items"]
                     if m["role"] == "owner")
    assert client.post(f"/api/workspaces/{wid}/members/{uid_owner}/role",
                       json={"role": "member"}, headers=_h(owner)).status_code == 400

    # dossier : créer puis supprimer (admin)
    did = client.post(f"/api/workspaces/{wid}/dossiers", json={"name": "D"}, headers=_h(owner)).json()["id"]
    assert client.delete(f"/api/dossiers/{did}", headers=_h(owner)).status_code == 200
    assert len(client.get(f"/api/workspaces/{wid}/dossiers", headers=_h(owner)).json()["items"]) == 0

    # alice (non-owner) quitte ; owner ne peut pas quitter
    assert client.post(f"/api/workspaces/{wid}/leave", headers=_h(alice)).status_code == 200
    assert client.post(f"/api/workspaces/{wid}/leave", headers=_h(owner)).status_code == 400
    assert len(client.get("/api/workspaces", headers=_h(alice)).json()["items"]) == 0

    # suppression du cabinet : propriétaire uniquement
    bob = _tok("bob@c.lu")
    client.post(f"/api/workspaces/{wid}/members", json={"email": "bob@c.lu", "role": "member"}, headers=_h(owner))
    assert client.delete(f"/api/workspaces/{wid}", headers=_h(bob)).status_code in (403, 404)
    assert client.delete(f"/api/workspaces/{wid}", headers=_h(owner)).status_code == 200
    assert len(client.get("/api/workspaces", headers=_h(owner)).json()["items"]) == 0


def test_workspace_requires_auth(temp_db):
    assert client.get("/api/workspaces").status_code == 401
    assert client.post("/api/workspaces", json={"name": "X"}).status_code == 401

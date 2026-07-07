"""Tests du socle entreprise/on-prem : audit, clés d'API, export RGPD, rétention, prompts,
draft (rédaction), revue tabulaire, timeline."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import audit, db, rag, search
from app.main import app
from app.schemas import Citation
from app.search import Hit

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _auth(email="s@b.com", admin=False):
    tok = client.post("/api/auth/register",
                      json={"email": email, "password": "password123"}).json()["token"]
    if admin:
        with db.get_conn() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE email = ?", (email,))
    return {"Authorization": f"Bearer {tok}"}


# ---------- clés d'API ----------
def test_apikey_cycle_et_auth(temp_db, monkeypatch):
    h = _auth()
    k = client.post("/api/keys", json={"name": "intégration"}, headers=h).json()
    assert k["key"].startswith("jlx_") and k["prefix"]
    items = client.get("/api/keys", headers=h).json()["items"]
    assert len(items) == 1 and items[0]["revoked"] is False and "key" not in items[0]

    # la clé authentifie /api/ask via X-API-Key
    monkeypatch.setattr(search, "search", lambda q, k_, f: [Hit(chunk_id="c", doc_id="d", text="t")])
    monkeypatch.setattr(rag, "answer", lambda q, hits, temp, **kw: __import__("app.schemas", fromlist=["AskResponse"]).AskResponse(answer="ok", citations=[], refused=False, status="ok"))
    r = client.post("/api/ask", json={"q": "x"}, headers={"X-API-Key": k["key"]})
    assert r.status_code == 200

    # révocation → la clé ne vaut plus rien
    assert client.delete(f"/api/keys/{k['id']}", headers=h).json() == {"ok": True}
    assert client.delete(f"/api/keys/{k['id']}", headers=h).status_code == 404


def test_apikey_requiert_auth(temp_db):
    assert client.get("/api/keys").status_code == 401


# ---------- audit ----------
def test_audit_journalise_login_et_gate_admin(temp_db):
    _auth("u@b.com")  # register (pas d'event) puis on se connecte
    client.post("/api/auth/login", json={"email": "u@b.com", "password": "password123"})
    ha = _auth("admin@b.com", admin=True)
    items = client.get("/api/admin/audit", headers=ha).json()["items"]
    assert any(e["action"] == "auth.login" for e in items)
    # réservé aux admins
    assert client.get("/api/admin/audit").status_code == 401
    assert client.get("/api/admin/audit", headers=_auth("x@b.com")).status_code == 403


def test_purge_retention(temp_db):
    audit.log("test.vieux", None, "ancien")
    with db.get_conn() as conn:  # forcer une date ancienne
        conn.execute("UPDATE audit_log SET ts = '2000-01-01T00:00:00+00:00'")
    ha = _auth("a@b.com", admin=True)
    res = client.post("/api/admin/purge", json={"days": 30}, headers=ha).json()
    assert res["deleted"]["audit_log"] >= 1
    assert client.post("/api/admin/purge", json={"days": 30}).status_code == 401


# ---------- export RGPD ----------
def test_export_rgpd(temp_db):
    h = _auth("e@b.com")
    body = client.get("/api/me/export", headers=h).json()
    assert body["user"]["email"] == "e@b.com"
    assert set(body) >= {"user", "history", "feedback", "shares", "alerts", "vault_documents", "api_keys"}
    assert client.get("/api/me/export").status_code == 401


# ---------- bibliothèque de prompts ----------
def test_prompts_perso_et_cabinet(temp_db):
    h = _auth("p@b.com")
    perso = client.post("/api/prompts", json={"title": "Résumé", "body": "Résume ceci"}, headers=h).json()
    assert perso["scope"] == "perso"
    wid = client.post("/api/workspaces", json={"name": "Cab"}, headers=h).json()["id"]
    part = client.post("/api/prompts", json={"title": "Clause", "body": "Rédige", "workspace_id": wid}, headers=h).json()
    assert part["scope"] == "cabinet"
    items = client.get("/api/prompts", headers=h).json()["items"]
    assert {i["title"] for i in items} == {"Résumé", "Clause"}
    # un étranger ne voit pas le prompt cabinet, et ne peut pas en créer dans cet espace
    h2 = _auth("q@b.com")
    assert client.get("/api/prompts", headers=h2).json()["items"] == []
    assert client.post("/api/prompts", json={"title": "X", "body": "Y", "workspace_id": wid}, headers=h2).status_code == 404
    assert client.delete(f"/api/prompts/{perso['id']}", headers=h).json() == {"ok": True}


# ---------- draft (rédaction assistée sourcée) ----------
def test_draft_sourced(temp_db, monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: [Hit(chunk_id="c", doc_id="eli-x", text="t", source_type="law")])
    monkeypatch.setattr(rag, "rediger", lambda instruction, hits, sensibilite="public": {
        "answer": "Madame, Monsieur, ... [eli-x]", "citations": [Citation(doc_id="eli-x", source_type="law")],
        "refused": False})
    h = _auth("d@b.com")
    body = client.post("/api/draft", json={"instruction": "Rédige une mise en demeure"}, headers=h).json()
    assert body["refused"] is False and body["citations"][0]["doc_id"] == "eli-x"
    assert client.post("/api/draft", json={"instruction": "x"}).status_code == 401


# ---------- revue tabulaire + timeline (Vault) ----------
def test_vault_review_et_timeline(temp_db, monkeypatch):
    monkeypatch.setattr("app.vault.index_chunks", lambda o, d, f, t: 1)
    h = _auth("v@b.com")
    contenu = (b"Le 12 mars 2020, licenciement. Contrat de travail. Par ces motifs, fait droit "
               b"et condamne a payer 1.500,00 EUR. Le 03/04/2021, appel.")
    up = client.post("/api/vault/documents?filename=aff.txt", content=contenu,
                     headers={**h, "Content-Type": "text/plain"}).json()

    review = client.post("/api/vault/review", json={"doc_ids": [up["id"]]}, headers=h).json()
    assert review["rows"][0]["matter"] == "Droit du travail"
    assert "1.500,00 EUR" in review["rows"][0]["amounts"]

    tl = client.post(f"/api/vault/documents/{up['id']}/analyze", json={"task": "timeline"}, headers=h).json()
    assert tl["task"] == "timeline" and len(tl["events"]) >= 2
    assert any("2020" in e["date"] for e in tl["events"])


# ---------- cloisons déontologiques (ethical walls) ----------
def test_ethical_walls(temp_db):
    ho = _auth("owner@b.com")
    hm = _auth("membre@b.com")
    wid = client.post("/api/workspaces", json={"name": "Cab"}, headers=ho).json()["id"]
    client.post(f"/api/workspaces/{wid}/members", json={"email": "membre@b.com", "role": "member"}, headers=ho)
    did = client.post(f"/api/workspaces/{wid}/dossiers", json={"name": "Aff sensible"}, headers=ho).json()["id"]
    # non restreint : le membre voit
    assert client.get(f"/api/dossiers/{did}/items", headers=hm).status_code == 200
    # on restreint : le membre ne voit plus (404, existence masquée)
    assert client.post(f"/api/dossiers/{did}/restrict", json={"restricted": True}, headers=ho).json()["restricted"] is True
    assert client.get(f"/api/dossiers/{did}/items", headers=hm).status_code == 404
    # l'owner garde l'accès
    assert client.get(f"/api/dossiers/{did}/items", headers=ho).status_code == 200
    # on autorise nommément le membre → il revoit
    uid = client.post(f"/api/dossiers/{did}/access", json={"email": "membre@b.com"}, headers=ho).json()["user_id"]
    assert client.get(f"/api/dossiers/{did}/items", headers=hm).status_code == 200
    # révocation → 404 de nouveau
    assert client.delete(f"/api/dossiers/{did}/access/{uid}", headers=ho).json() == {"ok": True}
    assert client.get(f"/api/dossiers/{did}/items", headers=hm).status_code == 404
    # un membre ne peut pas gérer les cloisons
    assert client.post(f"/api/dossiers/{did}/restrict", json={"restricted": False}, headers=hm).status_code == 403


# ---------- paramétrage runtime ----------
def test_config_runtime(temp_db):
    ha = _auth("a@b.com", admin=True)
    cfg = client.get("/api/admin/config", headers=ha).json()
    assert "llm_provider_confidential" in cfg["config"] and "rate_limit_per_min" in cfg["modifiables"]
    # applique un réglage autorisé (typé int) ; ignore une clé hors liste
    r = client.patch("/api/admin/config",
                     json={"values": {"rate_limit_per_min": 99, "anthropic_api_key": "SECRET"}}, headers=ha).json()
    assert r["applied"]["rate_limit_per_min"] == 99 and "anthropic_api_key" not in r["applied"]
    assert m.settings.rate_limit_per_min == 99
    assert client.get("/api/admin/config").status_code == 401
    assert client.patch("/api/admin/config", json={"values": {}}, headers=_auth("n@b.com")).status_code == 403


# ---------- observabilité ----------
def test_admin_health(temp_db, monkeypatch):
    monkeypatch.setattr(search, "meili_healthy", lambda: True)
    ha = _auth("a@b.com", admin=True)
    h = client.get("/api/admin/health", headers=ha).json()
    assert h["meilisearch"] is True and "llm_routing" in h and "counts" in h
    assert "users" in h["counts"]
    assert client.get("/api/admin/health").status_code == 401
    assert client.get("/api/admin/health", headers=_auth("n@b.com")).status_code == 403


# ---------- B9 : revue de contrats + playbooks ----------
def test_playbooks_et_revue_contrat(temp_db, monkeypatch):
    monkeypatch.setattr("app.vault.index_chunks", lambda o, d, f, t: 1)
    monkeypatch.setattr(rag, "revue_contrat", lambda texte, rules, sensibilite="confidentiel": {
        "findings": [{"label": "Clause de non-concurrence", "status": "issue", "note": "Durée excessive."},
                     {"label": "Loi applicable", "status": "missing", "note": "Absente."},
                     {"label": "Résiliation", "status": "ok", "note": "Conforme."}]})
    h = _auth("c@b.com")
    pb = client.post("/api/playbooks", json={"name": "CDI standard", "rules": [
        {"label": "Loi applicable", "instruction": "Vérifier la clause de loi applicable (LU)."},
        {"label": "Résiliation", "instruction": "Vérifier le préavis."}]}, headers=h).json()
    assert pb["scope"] == "perso" and len(pb["rules"]) == 2
    assert any(p["id"] == pb["id"] for p in client.get("/api/playbooks", headers=h).json()["items"])

    up = client.post("/api/vault/documents?filename=contrat.txt", content=b"Contrat de travail...",
                     headers={**h, "Content-Type": "text/plain"}).json()
    res = client.post(f"/api/vault/documents/{up['id']}/review-contract",
                      json={"playbook_id": pb["id"]}, headers=h).json()
    assert res["task"] == "contract" and res["playbook"] == "CDI standard"
    assert res["summary"] == {"total": 3, "ok": 1, "issue": 1, "missing": 1}

    # playbook inconnu → 404 ; document inconnu → 404
    assert client.post(f"/api/vault/documents/{up['id']}/review-contract",
                       json={"playbook_id": 999999}, headers=h).status_code == 404
    assert client.post("/api/vault/documents/999999/review-contract",
                       json={"playbook_id": pb["id"]}, headers=h).status_code == 404
    # suppression + validation (règles requises)
    assert client.delete(f"/api/playbooks/{pb['id']}", headers=h).json() == {"ok": True}
    assert client.post("/api/playbooks", json={"name": "X", "rules": []}, headers=h).status_code == 422
    assert client.get("/api/playbooks").status_code == 401

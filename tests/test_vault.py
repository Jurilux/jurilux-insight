"""Tests Vault : dépôt/liste/suppression, Ask isolé, vérificateur de citations, isolation."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db, rag, search, vault
from app.main import app
from app.schemas import AskResponse, Citation
from app.search import Hit

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _auth(email="v@b.com"):
    tok = client.post("/api/auth/register",
                      json={"email": email, "password": "password123"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def _upload(headers, filename, content, monkeypatch):
    monkeypatch.setattr(vault, "index_chunks", lambda o, d, f, t: 2)
    return client.post(f"/api/vault/documents?filename={filename}",
                       content=content, headers={**headers, "Content-Type": "text/plain"})


def test_vault_requires_auth(temp_db):
    assert client.get("/api/vault/documents").status_code == 401


def test_upload_list_delete(temp_db, monkeypatch):
    monkeypatch.setattr(vault, "delete_chunks", lambda o, d: None)
    h = _auth()
    r = _upload(h, "note.txt", b"un contenu de test", monkeypatch)
    assert r.status_code == 200
    doc = r.json()
    assert doc["filename"] == "note.txt" and doc["status"] == "ready" and doc["n_chunks"] == 2

    listed = client.get("/api/vault/documents", headers=h).json()["items"]
    assert len(listed) == 1 and listed[0]["id"] == doc["id"] and "text" not in listed[0]

    assert client.delete(f"/api/vault/documents/{doc['id']}", headers=h).json() == {"ok": True}
    assert client.get("/api/vault/documents", headers=h).json()["items"] == []


def test_vault_ask_scoped_and_sourced(temp_db, monkeypatch):
    monkeypatch.setattr(vault, "search_vault",
                        lambda owner, q, ids, k: [Hit(chunk_id="1", doc_id="7", text="extrait", title="conclusions.pdf")])
    monkeypatch.setattr(rag, "answer", lambda q, hits, temp, **kw: AskResponse(
        answer="D'après vos documents...", citations=[Citation(doc_id=hits[0].doc_id, title=hits[0].title)],
        refused=False, status="ok"))
    h = _auth()
    body = client.post("/api/vault/ask", json={"q": "quel délai ?"}, headers=h).json()
    assert body["answer"].startswith("D'après vos documents")
    assert body["citations"][0]["title"] == "conclusions.pdf"


def test_vault_ask_hybrid_corpus(temp_db, monkeypatch):
    # include_corpus : la réponse croise documents privés (sans source_type) ET corpus public.
    monkeypatch.setattr(vault, "search_vault",
                        lambda owner, q, ids, k: [Hit(chunk_id="v1", doc_id="7", text="mon extrait", title="contrat.pdf")])
    monkeypatch.setattr(search, "search",
                        lambda q, k, f: [Hit(chunk_id="c1", doc_id="2020_TAL_1", text="jurisprudence", source_type="jurisprudence")])
    monkeypatch.setattr(rag, "answer", lambda q, hits, temp, **kw: AskResponse(
        answer="Réponse croisée.",
        citations=[Citation(doc_id=h.doc_id, title=h.title, source_type=h.source_type) for h in hits],
        refused=False, status="ok"))
    h = _auth()
    body = client.post("/api/vault/ask",
                       json={"q": "clause de non-concurrence ?", "include_corpus": True}, headers=h).json()
    cites = {c["doc_id"]: c for c in body["citations"]}
    assert "7" in cites and cites["7"]["source_type"] is None          # document privé
    assert "2020_TAL_1" in cites and cites["2020_TAL_1"]["source_type"] == "jurisprudence"  # source publique


def test_vault_ask_no_corpus_by_default(temp_db, monkeypatch):
    # Sans include_corpus : le corpus public n'est PAS interrogé (rétro-compatibilité).
    monkeypatch.setattr(vault, "search_vault",
                        lambda owner, q, ids, k: [Hit(chunk_id="v1", doc_id="7", text="x", title="c.pdf")])

    def _boom(*a, **k):
        raise AssertionError("search.search ne doit pas être appelé sans include_corpus")

    monkeypatch.setattr(search, "search", _boom)
    monkeypatch.setattr(rag, "answer", lambda q, hits, temp, **kw: AskResponse(
        answer="ok", citations=[], refused=False, status="ok"))
    h = _auth()
    assert client.post("/api/vault/ask", json={"q": "x"}, headers=h).status_code == 200


def test_vault_ask_empty(temp_db, monkeypatch):
    monkeypatch.setattr(vault, "search_vault", lambda owner, q, ids, k: [])
    h = _auth()
    body = client.post("/api/vault/ask", json={"q": "x"}, headers=h).json()
    assert body["refused"] is True


def test_citation_checker_against_corpus(temp_db, monkeypatch):
    # Le corpus « connaît » L.124-10 mais pas l'article inventé.
    monkeypatch.setattr(vault, "corpus_search",
                        lambda q, k, f: [Hit(chunk_id="c", doc_id="eli-code-travail", text="t", source_type="law")]
                        if "124-10" in q else [])
    h = _auth()
    body = b"Vu l'article L.124-10 du Code du travail et l'article 9999 inexistant."
    up = _upload(h, "x.txt", body, monkeypatch).json()

    res = client.post(f"/api/vault/documents/{up['id']}/analyze",
                      json={"task": "citations"}, headers=h).json()
    assert res["total"] == 2 and res["verified"] == 1
    refs = {r["ref"]: r for r in res["references"]}
    assert refs["article L.124-10"]["verified"] is True
    assert refs["article L.124-10"]["doc_id"] == "eli-code-travail"
    assert refs["article 9999"]["verified"] is False


def test_structure_extraction_local(temp_db, monkeypatch):
    # Extraction structurée 100 % locale/déterministe (aucun LLM, aucun corpus).
    h = _auth()
    body = ("Pour le demandeur, Maître Jean DUPONT a plaidé. L'affaire porte sur un "
            "licenciement et le contrat de travail du salarié. Par ces motifs, le tribunal "
            "fait droit à la demande et condamne à payer 1.500,00 € au titre du préavis, "
            "vu l'article L.124-10 du Code du travail.").encode()
    up = _upload(h, "conclusions.txt", body, monkeypatch).json()

    res = client.post(f"/api/vault/documents/{up['id']}/analyze",
                      json={"task": "extract"}, headers=h).json()
    assert res["task"] == "extract"
    assert res["matter"] == "Droit du travail"
    assert res["outcome"] == "A"                       # « fait droit » → demandeur (estimé)
    assert "1.500,00 €" in res["amounts"]
    assert any(l["name"] == "Jean DUPONT" and l["side"] == "A" for l in res["lawyers"])
    assert "article L.124-10" in res["references"]


def test_vault_summary(temp_db, monkeypatch):
    monkeypatch.setattr(rag, "resume", lambda texte, sensibilite="confidentiel": "Résumé de test.")
    h = _auth()
    up = _upload(h, "note.txt", b"un long document juridique", monkeypatch).json()
    res = client.post(f"/api/vault/documents/{up['id']}/analyze",
                      json={"task": "summary"}, headers=h).json()
    assert res["task"] == "summary" and res["summary"] == "Résumé de test."


def test_vault_counter_sourced(temp_db, monkeypatch):
    monkeypatch.setattr(search, "search",
                        lambda q, k, f: [Hit(chunk_id="c", doc_id="csj_2019", text="t", source_type="jurisprudence")])
    monkeypatch.setattr(rag, "contre_argumentaire",
                        lambda texte, hits, sensibilite="confidentiel": {
                            "answer": "La prétention adverse est mal fondée [csj_2019].",
                            "citations": [Citation(doc_id="csj_2019", source_type="jurisprudence")],
                            "refused": False})
    h = _auth()
    up = _upload(h, "adverse.txt", b"Les conclusions adverses soutiennent que...", monkeypatch).json()
    res = client.post(f"/api/vault/documents/{up['id']}/analyze",
                      json={"task": "counter"}, headers=h).json()
    assert res["task"] == "counter" and res["refused"] is False
    assert res["citations"][0]["doc_id"] == "csj_2019"


def test_vault_analyze_task_invalide(temp_db, monkeypatch):
    h = _auth()
    up = _upload(h, "x.txt", b"contenu", monkeypatch).json()
    r = client.post(f"/api/vault/documents/{up['id']}/analyze", json={"task": "foo"}, headers=h)
    assert r.status_code == 422


def test_vault_isolation_between_users(temp_db, monkeypatch):
    h = _auth("a@b.com")
    up = _upload(h, "x.txt", b"secret", monkeypatch).json()
    h2 = _auth("b@b.com")
    assert client.post(f"/api/vault/documents/{up['id']}/analyze",
                       json={"task": "citations"}, headers=h2).status_code == 404
    assert client.delete(f"/api/vault/documents/{up['id']}", headers=h2).status_code == 404
    assert client.get("/api/vault/documents", headers=h2).json()["items"] == []

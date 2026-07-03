"""Tests du contrat d'API (sans Meilisearch ni Anthropic : monkeypatch)."""
import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import rag, search
from app.main import app
from app.search import Hit

client = TestClient(app)

HITS = [
    Hit(chunk_id="c1", doc_id="csj_ch08_2019_demo1", text="La faute grave...",
        title="CSJ 8e ch.", year=2019, juridiction_key="csj_ch08",
        source_type="jurisprudence"),
    Hit(chunk_id="c2", doc_id="eli-etat-leg-loi-2006-07-31.pdf", text="Art. L.124-10...",
        title="Code du travail", year=2006, source_type="law",
        pdf_url="https://legilux.public.lu/x.pdf"),
]


def _llm_response(payload: dict):
    block = MagicMock()
    block.type = "text"
    block.text = json.dumps(payload)
    msg = MagicMock()
    msg.content = [block]
    return msg


def test_health_degraded_when_meili_down(monkeypatch):
    monkeypatch.setattr(search, "meili_healthy", lambda: False)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json()["meilisearch"] is False


def test_health_ok(monkeypatch):
    monkeypatch.setattr(search, "meili_healthy", lambda: True)
    monkeypatch.setattr("app.main.settings.anthropic_api_key", "sk-test")
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_ask_refuses_when_no_hits(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: [])
    r = client.post("/api/ask", json={"q": "question hors corpus"})
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["answer"] is None
    assert body["citations"] == []
    assert body["feedback"]["why"]


def test_ask_full_contract(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    llm = _llm_response({
        "answer": "En droit luxembourgeois, la faute grave... [csj_ch08_2019_demo1]",
        "used_doc_ids": ["csj_ch08_2019_demo1", "eli-etat-leg-loi-2006-07-31.pdf"],
        "refused": False,
        "status": "ok",
        "feedback": {"why": "Sources concordantes", "what_we_see": ["arrêt CSJ 2019"],
                     "limits": "Corpus partiel", "how_to_improve": ["préciser l'année"]},
    })
    with patch.object(rag.anthropic, "Anthropic") as A:
        A.return_value.messages.create.return_value = llm
        r = client.post("/api/ask", json={
            "q": "licenciement faute grave", "topK": 5, "temperature": 0,
            "filters": {"year_min": 2015, "juridiction_key": "csj_ch08"},
        })
    assert r.status_code == 200
    body = r.json()
    # contrat front (src/api.ts) : toutes les clés d'AskResponse
    assert set(body) >= {"answer", "citations", "refused", "status", "feedback", "prompt_version"}
    assert body["refused"] is False and body["status"] == "ok"
    assert len(body["citations"]) == 2
    c = body["citations"][0]
    assert set(c) >= {"doc_id", "url", "pdf_url", "year", "juridiction_key",
                      "content", "source_type", "title"}
    assert body["citations"][1]["pdf_url"].startswith("https://legilux")


def test_ask_llm_refusal(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    llm = _llm_response({"answer": None, "used_doc_ids": [], "refused": True,
                         "status": "ok", "feedback": {"why": "Hors du champ du corpus."}})
    with patch.object(rag.anthropic, "Anthropic") as A:
        A.return_value.messages.create.return_value = llm
        r = client.post("/api/ask", json={"q": "recette de kachkéis"})
    body = r.json()
    assert body["refused"] is True
    assert body["feedback"]["why"] == "Hors du champ du corpus."


def test_ask_invalid_llm_json_degrades_to_partial(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    block = MagicMock(); block.type = "text"; block.text = "réponse libre sans JSON"
    msg = MagicMock(); msg.content = [block]
    with patch.object(rag.anthropic, "Anthropic") as A:
        A.return_value.messages.create.return_value = msg
        r = client.post("/api/ask", json={"q": "test"})
    body = r.json()
    assert body["status"] == "partial"
    assert body["answer"] == "réponse libre sans JSON"


def test_filter_expression():
    from app.schemas import SearchFilters
    from app.search import _filter_expr
    f = SearchFilters(year_min=2015, year_max=2020, juridiction_key="csj_ch08")
    assert _filter_expr(f) == 'year >= 2015 AND year <= 2020 AND juridiction_key = "csj_ch08"'
    assert _filter_expr(SearchFilters()) is None

"""Tests du contrat d'API (sans Meilisearch ni Anthropic : monkeypatch)."""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db, rag, search
from app.main import app
from app.schemas import SearchFilters
from app.search import Hit

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Base SQLite jetable pour les tests d'espace utilisateur."""
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield

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
    with patch("app.llm.anthropic.Anthropic") as A:
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


def test_ask_follow_ups_parcours(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    llm = _llm_response({
        "answer": "Le préavis dépend de l'ancienneté. [csj_ch08_2019_demo1]",
        "used_doc_ids": ["csj_ch08_2019_demo1"], "refused": False, "status": "ok",
        "suggested_question": "Quel préavis pour un CDD ?",
        "follow_ups": ["Quel est le préavis selon l'ancienneté ?",
                       "Quelles indemnités en cas de non-respect du préavis ?",
                       "Quel est le préavis selon l'ancienneté ?"],  # doublon → dédupliqué
    })
    with patch("app.llm.anthropic.Anthropic") as A:
        A.return_value.messages.create.return_value = llm
        body = client.post("/api/ask", json={"q": "préavis licenciement"}).json()
    assert body["follow_ups"] == ["Quel est le préavis selon l'ancienneté ?",
                                   "Quelles indemnités en cas de non-respect du préavis ?"]
    assert body["suggested_question"] == "Quel préavis pour un CDD ?"  # autre angle, distinct


def test_ask_llm_refusal_keeps_pistes_and_pivot(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    llm = _llm_response({"answer": None, "used_doc_ids": [], "refused": True,
                         "status": "ok",
                         "suggested_question": "Quel préavis pour un CDD ?",
                         "feedback": {"why": "Hors du champ du corpus.",
                                      "how_to_improve": ["Préciser le type de contrat"]}})
    with patch("app.llm.anthropic.Anthropic") as A:
        A.return_value.messages.create.return_value = llm
        r = client.post("/api/ask", json={"q": "recette de kachkéis"})
    body = r.json()
    assert body["refused"] is True
    assert body["feedback"]["why"] == "Hors du champ du corpus."
    # Cinématique de rebond : le refus n'est PAS une impasse.
    assert body["suggested_question"] == "Quel préavis pour un CDD ?"
    assert len(body["citations"]) >= 1                       # pistes conservées
    assert body["feedback"]["how_to_improve"]                # reformulations proposées


def test_ask_invalid_llm_json_degrades_to_partial(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    block = MagicMock(); block.type = "text"; block.text = "réponse libre sans JSON"
    msg = MagicMock(); msg.content = [block]
    with patch("app.llm.anthropic.Anthropic") as A:
        A.return_value.messages.create.return_value = msg
        r = client.post("/api/ask", json={"q": "test"})
    body = r.json()
    assert body["status"] == "partial"
    assert body["answer"] == "réponse libre sans JSON"


def test_ask_rate_limited(monkeypatch):
    import app.main as m
    m._rl_hits.clear()
    monkeypatch.setattr(m.settings, "rate_limit_per_min", 3)
    monkeypatch.setattr(search, "search", lambda q, k, f: [])  # refus "hors corpus"
    for _ in range(3):
        body = client.post("/api/ask", json={"q": "x"}).json()
        assert "Trop de requêtes" not in (body["feedback"]["why"] or "")
    # la requête au-delà du quota est refusée gracieusement (contrat AskResponse préservé)
    body = client.post("/api/ask", json={"q": "x"}).json()
    assert body["refused"] is True
    assert "Trop de requêtes" in body["feedback"]["why"]
    m._rl_hits.clear()


def test_corpus_overview(monkeypatch):
    monkeypatch.setattr(search, "corpus_overview", lambda: {
        "decisions": 49570, "texts": 10, "updated": "2026-07",
        "chunks": 1236634, "latest_year": 2026,
    })
    b = client.get("/api/corpus").json()
    assert b["decisions"] == 49570 and b["texts"] == 10 and b["latest_year"] == 2026


def test_metrics_endpoint(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: [])
    client.post("/api/ask", json={"q": "x"})  # incrémente les compteurs
    b = client.get("/api/metrics").json()
    assert {"uptime_s", "ask_total", "ask_refused", "refusal_rate"} <= set(b)
    assert b["ask_total"] >= 1


def test_search_federated_includes_law(monkeypatch):
    def fake(q, limit, expr, vector=None):
        st = 'law' if (expr and 'law' in expr) else 'jurisprudence'
        return [Hit(chunk_id=f"{st}{i}", doc_id=f"{st}-{i}", text="x", source_type=st)
                for i in range(limit)]
    monkeypatch.setattr(search, "_search_one", fake)
    types = [h.source_type for h in search.search("q", 12, SearchFilters())]
    assert 'law' in types and 'jurisprudence' in types  # les deux présents
    assert types.count('law') >= 3                       # textes garantis dans le contexte
    # filtre explicite → recherche simple (respecte le type demandé)
    assert search.search("q", 12, SearchFilters(source_type='law'))


def test_auth_flow(temp_db):
    r = client.post("/api/auth/register", json={"email": "A@B.com", "password": "password123"})
    assert r.status_code == 200
    tok = r.json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/me", headers=h).json()["user"]["email"] == "a@b.com"  # normalisé
    assert client.get("/api/me").status_code == 401  # sans token
    # doublon + mot de passe court
    assert client.post("/api/auth/register", json={"email": "a@b.com", "password": "password123"}).status_code == 400
    assert client.post("/api/auth/register", json={"email": "c@d.com", "password": "court"}).status_code == 400
    # login ok / ko
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "password123"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "a@b.com", "password": "nope"}).status_code == 401
    # logout invalide le token
    client.post("/api/auth/logout", headers=h)
    assert client.get("/api/me", headers=h).status_code == 401


def test_history_saved_when_authenticated(temp_db, monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: [])
    tok = client.post("/api/auth/register",
                      json={"email": "h@b.com", "password": "password123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    client.post("/api/ask", json={"q": "ma question test"}, headers=h)
    items = client.get("/api/history", headers=h).json()["items"]
    assert len(items) == 1 and items[0]["question"] == "ma question test"
    # anonyme : pas d'historique requis, /api/history exige un compte
    assert client.get("/api/history").status_code == 401


def test_student_quota(temp_db, monkeypatch):
    monkeypatch.setattr(m.settings, "student_monthly_quota", 2)
    monkeypatch.setattr(search, "search", lambda q, k, f: [])
    tok = client.post("/api/auth/register",
                      json={"email": "q@b.com", "password": "password123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    for i in range(2):  # 2 questions passent (plan étudiant, quota=2)
        b = client.post("/api/ask", json={"q": f"q{i}"}, headers=h).json()
        assert "Quota mensuel" not in (b["feedback"]["why"] or "")
    b = client.post("/api/ask", json={"q": "q3"}, headers=h).json()  # 3e refusée
    assert "Quota mensuel" in b["feedback"]["why"]
    me = client.get("/api/me", headers=h).json()
    assert me["quota"]["limit"] == 2 and me["user"]["plan"] == "student"
    assert me["quota"]["remaining"] == 0


def test_feedback_stored_and_readable(temp_db, monkeypatch):
    # anonyme peut noter (pas de compte requis)
    assert client.post("/api/feedback",
                       json={"question": "q1", "helpful": True}).status_code == 200
    assert client.post("/api/feedback",
                       json={"question": "q2", "helpful": False,
                             "missing": "il manquait la loi applicable",
                             "status": "refused"}).status_code == 200
    # lecture réservée admin
    monkeypatch.setattr(m.settings, "admin_emails", "boss@b.com")
    tok = client.post("/api/auth/register",
                      json={"email": "boss@b.com", "password": "password123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/admin/feedback").status_code == 401  # anonyme
    d = client.get("/api/admin/feedback", headers=h).json()
    assert d["stats"]["total"] == 2 and d["stats"]["helpful"] == 1
    assert d["stats"]["satisfaction"] == 0.5
    miss = [i for i in d["items"] if i["missing"]]
    assert miss and miss[0]["missing"].startswith("il manquait")


def test_change_password(temp_db):
    tok = client.post("/api/auth/register",
                      json={"email": "pw@b.com", "password": "password123"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    # mauvais mot de passe actuel -> 400
    assert client.post("/api/auth/change-password", headers=h,
                       json={"old_password": "faux", "new_password": "nouveaumdp1"}).status_code == 400
    # nouveau trop court -> 400
    assert client.post("/api/auth/change-password", headers=h,
                       json={"old_password": "password123", "new_password": "court"}).status_code == 400
    # anonyme -> 401
    assert client.post("/api/auth/change-password",
                       json={"old_password": "password123", "new_password": "nouveaumdp1"}).status_code == 401
    # changement valide -> 200, puis l'ancien ne marche plus, le nouveau si
    assert client.post("/api/auth/change-password", headers=h,
                       json={"old_password": "password123", "new_password": "nouveaumdp1"}).status_code == 200
    assert client.post("/api/auth/login", json={"email": "pw@b.com", "password": "password123"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "pw@b.com", "password": "nouveaumdp1"}).status_code == 200


def test_ask_stream(monkeypatch):
    monkeypatch.setattr(search, "search", lambda q, k, f: HITS)
    chunks = [
        "La faute grave", " justifie le licenciement", " [csj_ch08_2019_demo1].",
        "\n§§§META§§§\n",
        '{"used_doc_ids":["csj_ch08_2019_demo1"],"status":"ok","refused":false,'
        '"suggested_question":"Un exemple ?","how_to_improve":["préciser l\'année"]}',
    ]

    class FakeStream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        @property
        def text_stream(self):
            return iter(chunks)

    with patch.object(rag.anthropic, "Anthropic") as A:
        A.return_value.messages.stream.return_value = FakeStream()
        r = client.post("/api/ask/stream", json={"q": "faute grave"})
    assert r.status_code == 200
    body = r.text
    assert "faute grave" in body.lower()          # le texte de réponse est streamé
    assert "META" not in body                       # le délimiteur ne fuit PAS dans la sortie
    assert '"type": "meta"' in body                 # un event méta est envoyé
    assert '"refused": false' in body


def test_share_roundtrip(temp_db):
    payload = {"question": "Quel préavis pour un CDD ?",
               "answer": "Le préavis dépend de... [doc]",
               "citations": [{"doc_id": "csj_x", "source_type": "jurisprudence"}],
               "status": "partial"}
    r = client.post("/api/share", json=payload)   # anonyme OK
    assert r.status_code == 200
    sid = r.json()["id"]
    assert sid
    got = client.get(f"/api/share/{sid}")
    assert got.status_code == 200
    b = got.json()
    assert b["question"] == payload["question"]
    assert b["answer"] == payload["answer"]
    assert b["citations"][0]["doc_id"] == "csj_x"
    assert b["status"] == "partial"
    # lien inconnu -> 404
    assert client.get("/api/share/inexistant").status_code == 404


def test_filter_expression():
    from app.schemas import SearchFilters
    from app.search import _filter_expr
    f = SearchFilters(year_min=2015, year_max=2020, juridiction_key="csj_ch08")
    assert _filter_expr(f) == 'year >= 2015 AND year <= 2020 AND juridiction_key = "csj_ch08"'
    assert _filter_expr(SearchFilters()) is None

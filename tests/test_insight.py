"""Tests Insight : extraction des avocats, nettoyage, agrégation, gate admin."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import db, insight
from app.main import app

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def test_extract_lawyers():
    txt = "représenté par Maître Guy CASTEGNARO, avocat à la Cour, et Maître Jean-Luc GONNER. La partie…"
    names = insight.extract_lawyers(txt)
    assert "Guy CASTEGNARO" in names
    assert any("GONNER" in n for n in names)
    # retours-ligne aplatis (le nom est coupé en fin de ligne dans les PDF)
    assert "Guy CASTEGNARO" in insight.extract_lawyers("assisté de Maître Guy\nCASTEGNARO qui plaide")
    # placeholders de pseudonymisation ignorés
    assert insight.extract_lawyers("Maître AVOCAT1. contre Maître PERSONNE DE JUSTICE2.") == []
    # noms de famille composés / accents
    assert any("FRIEDERS" in n for n in insight.extract_lawyers("Maître Tonia FRIEDERS-SCHEIFER plaide"))


def test_name_key_groups_variants():
    assert insight.name_key("François KREMER") == insight.name_key("Francois  KREMER")


def test_parse_chunk_side_and_outcome():
    txt = ("ENTRE la société X, demanderesse, représentée par Maître Guy CASTEGNARO, avocat, "
           "ET la société Y, défenderesse, représentée par Maître Lex THIELEN, avocat. "
           "Par ces motifs, déboute la demanderesse de sa demande, condamne aux dépens.")
    p = insight.parse_chunk(txt)
    assert p["lawyers"][insight.name_key("Guy CASTEGNARO")]["side"] == "A"
    assert p["lawyers"][insight.name_key("Lex THIELEN")]["side"] == "B"
    assert p["outcome"] == "B"  # « déboute » la demanderesse -> côté B l'emporte


def test_matter_classification():
    from collections import Counter
    c = Counter()
    insight.matter_hits("licenciement pour faute grave, préavis et contrat de travail", c)
    assert c.most_common(1)[0][0] == "Droit du travail"
    assert insight.matter_from_docid("20251209_JPLTRAVAIL_4035") == "Droit du travail"
    assert insight.matter_from_docid("20250313_JPLBAIL_978") == "Bail / logement"


def test_insight_store_and_profile(temp_db):
    insight.record_many([
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d1", 2020, "csj", "A", 1, "Droit du travail"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d2", 2021, "tal", "B", 0, "Bail / logement"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d2", 2021, "tal", "B", 0, "Bail / logement"),  # doublon ignoré
        ("LEX THIELEN", "Lex THIELEN", "d1", 2020, "csj", "B", 0, "Droit du travail"),
    ])
    top = insight.list_lawyers(None, 10)
    assert top[0]["name"] == "Guy CASTEGNARO" and top[0]["cases"] == 2
    assert insight.stats() == {"lawyers": 2, "appearances": 3}
    prof = insight.get_lawyer("GUY CASTEGNARO")
    assert prof["cases_count"] == 2 and prof["won"] == 1 and prof["lost"] == 1 and prof["decided"] == 2
    assert prof["as_demandeur"] == 1 and prof["as_defendeur"] == 1
    assert {m["name"] for m in prof["matters"]} == {"Droit du travail", "Bail / logement"}
    # réseau : THIELEN co-cité dans d1, côté opposé -> adversaire
    assert prof["cocounsel"][0]["name"] == "Lex THIELEN"
    assert prof["cocounsel"][0]["count"] == 1 and prof["cocounsel"][0]["relation"] == "adversaire"
    # filtre par matière : seul GUY a du bail
    bail = insight.list_lawyers(None, 10, matter="Bail / logement")
    assert len(bail) == 1 and bail[0]["name"] == "Guy CASTEGNARO" and bail[0]["cases"] == 1
    # matières disponibles
    assert {m["name"] for m in insight.matters()} == {"Droit du travail", "Bail / logement"}
    assert insight.list_lawyers("thielen", 10)[0]["name"] == "Lex THIELEN"


def test_lawyer_lookup(temp_db):
    insight.record_many([
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "20200101_TAL_1", 2020, None, "A", 1, "Droit du travail"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "20210101_TAL_2", 2021, None, "B", 0, "Droit du travail"),
    ])
    res = insight.lawyer_lookup("Quels textes mentionnent l'avocat Castegnaro ?")
    assert res is not None and "CASTEGNARO" in res["answer"].upper() and len(res["citations"]) == 2
    # sans indice d'avocat -> None (recherche juridique normale)
    assert insight.lawyer_lookup("Quel est le préavis de licenciement ?") is None
    # avocat inexistant -> None
    assert insight.lawyer_lookup("Quelles décisions pour l'avocat Zzzzxyz ?") is None


def test_insight_gated(temp_db):
    assert client.get("/api/insight/lawyers").status_code == 401
    assert client.get("/api/insight/stats").status_code == 401

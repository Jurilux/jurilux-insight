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


def test_insight_store_and_profile(temp_db):
    insight.record_many([
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d1", 2020, "csj"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d2", 2021, "tal"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d2", 2021, "tal"),  # doublon -> ignoré (UNIQUE)
        ("LEX THIELEN", "Lex THIELEN", "d1", 2020, "csj"),
    ])
    top = insight.list_lawyers(None, 10)
    assert top[0]["name"] == "Guy CASTEGNARO" and top[0]["cases"] == 2
    assert insight.stats() == {"lawyers": 2, "appearances": 3}
    prof = insight.get_lawyer("GUY CASTEGNARO")
    assert prof["cases_count"] == 2 and prof["first_year"] == 2020 and prof["last_year"] == 2021
    assert len(prof["jurisdictions"]) == 2
    # recherche
    assert insight.list_lawyers("thielen", 10)[0]["name"] == "Lex THIELEN"


def test_insight_gated(temp_db):
    assert client.get("/api/insight/lawyers").status_code == 401
    assert client.get("/api/insight/stats").status_code == 401

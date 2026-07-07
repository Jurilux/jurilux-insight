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


def test_analytics_taux_et_volumes(temp_db):
    insight.record_many([
        ("A A", "A A", "d1", 2020, "csj", "A", 1, "Droit du travail"),
        ("B B", "B B", "d1", 2020, "csj", "B", 0, "Droit du travail"),
        ("C C", "C C", "d2", 2021, "tal", "A", 1, "Bail / logement"),
        ("D D", "D D", "d3", 2021, "tal", "A", None, "Bail / logement"),  # issue non estimable
    ])
    a = insight.analytics()
    assert a["overall"]["cases"] == 4 and a["overall"]["decided"] == 3 and a["overall"]["won"] == 2
    assert a["overall"]["win_rate"] == round(2 / 3, 3) and a["overall"]["lawyers"] == 4
    travail = next(m for m in a["by_matter"] if m["cle"] == "Droit du travail")
    assert travail["cases"] == 2 and travail["decided"] == 2 and travail["win_rate"] == 0.5
    # filtre par matière
    b = insight.analytics(matter="Bail / logement")
    assert b["overall"]["cases"] == 2 and b["overall"]["decided"] == 1 and b["overall"]["win_rate"] == 1.0
    assert {y["cle"] for y in a["by_year"]} == {2020, 2021}


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


def test_insight_public(temp_db):
    # Déploiement client : Insight accessible par défaut (plus de gate admin).
    assert client.get("/api/insight/lawyers").status_code == 200
    assert client.get("/api/insight/stats").status_code == 200


def test_overview_compare_export(temp_db):
    insight.record_many([
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d1", 2020, "csj", "A", 1, "Droit du travail"),
        ("GUY CASTEGNARO", "Guy CASTEGNARO", "d2", 2021, "tal", "B", 0, "Bail / logement"),
        ("LEX THIELEN", "Lex THIELEN", "d1", 2020, "csj", "B", 0, "Droit du travail"),
    ])
    # overview : KPIs d'en-tête du dashboard
    ov = insight.overview()
    assert ov["lawyers"] == 2 and ov["cases"] == 3 and ov["decided"] == 3 and ov["won"] == 1
    assert ov["first_year"] == 2020 and ov["last_year"] == 2021
    assert any(m["cle"] == "Droit du travail" for m in ov["top_matters"])

    # compare : benchmark côte à côte (déduplication + profils condensés)
    cmp = insight.compare(["GUY CASTEGNARO", "LEX THIELEN", "GUY CASTEGNARO"])
    assert len(cmp["profiles"]) == 2
    guy = next(p for p in cmp["profiles"] if p["name_key"] == "GUY CASTEGNARO")
    assert guy["cases"] == 2 and guy["won"] == 1 and guy["decided"] == 2 and guy["win_rate"] == 0.5
    assert guy["as_demandeur"] == 1 and guy["as_defendeur"] == 1

    # export CSV : entête + une ligne par avocat
    csv_text = insight.export_lawyers_csv()
    lines = [l for l in csv_text.splitlines() if l]
    assert lines[0].startswith("name_key,avocat,decisions")
    assert len(lines) == 3 and any("Guy CASTEGNARO" in l for l in lines)


def test_insight_b2b_endpoints(temp_db):
    insight.record_many([
        ("A A", "Anne AUBER", "d1", 2020, "csj", "A", 1, "Droit du travail"),
        ("B B", "Bob BECK", "d1", 2020, "csj", "B", 0, "Droit du travail"),
    ])
    assert client.get("/api/insight/overview").status_code == 200
    # compare exige au moins 2 clés -> 422 sinon
    assert client.get("/api/insight/compare", params={"keys": "A A"}).status_code == 422
    r = client.get("/api/insight/compare", params={"keys": "A A,B B"})
    assert r.status_code == 200 and len(r.json()["profiles"]) == 2
    # export : content-type CSV + pièce jointe
    exp = client.get("/api/insight/export/lawyers.csv")
    assert exp.status_code == 200 and "text/csv" in exp.headers["content-type"]
    assert "attachment" in exp.headers.get("content-disposition", "")


def test_extract_amount():
    # Formats européens + marqueur monétaire ; garde-fous (bruit, absence).
    assert insight.extract_amount("condamne à payer la somme de 12.345,67 €") == 12345.67
    assert insight.extract_amount("dommages de 1 250 000,00 EUR et 500 euros de frais") == 1250000.0
    assert insight.extract_amount("article 1382 du code civil, sans montant") is None
    assert insight.extract_amount("une amende de 50 €") is None  # sous le seuil de bruit (100 €)
    # Le principal (plus grand) domine.
    assert insight.extract_amount("15.000 € en principal, 2.000 € d'intérêts") == 15000.0


def test_analytics_amounts(temp_db):
    insight.record_many([
        ("A A", "Anne A", "d1", 2020, "csj", "A", 1, "Droit du travail", 10000.0),
        ("A A", "Anne A", "d2", 2021, "csj", "A", 1, "Droit du travail", 30000.0),
        ("B B", "Bob B", "d3", 2020, "tal", "B", 0, "Bail / logement", None),  # pas de montant
    ])
    a = insight.analytics()
    assert a["overall"]["amount_median"] == 20000.0 and a["overall"]["amount_n"] == 2
    travail = next(m for m in a["by_matter"] if m["cle"] == "Droit du travail")
    assert travail["amount_median"] == 20000.0 and travail["amount_n"] == 2
    # Profil + overview + compare exposent la médiane.
    assert insight.get_lawyer("A A")["amount_median"] == 20000.0
    assert insight.overview()["amount_median"] == 20000.0
    prof = insight.compare(["A A", "B B"])["profiles"]
    assert next(p for p in prof if p["name_key"] == "A A")["amount_median"] == 20000.0
    assert next(p for p in prof if p["name_key"] == "B B")["amount_median"] is None
    # Rétrocompat : record_many accepte encore des tuples à 8 champs (montant = NULL).
    insight.record_many([("C C", "Carl C", "d9", 2022, "csj", "A", 1, "Pénal")])
    assert insight.get_lawyer("C C")["amount_median"] is None


def test_firm_extraction_and_aggregation(temp_db):
    # Association du cabinet le PLUS PROCHE de chaque avocat (jamais d'inférence).
    p = insight.parse_chunk(
        "Pour le demandeur, Maître Jean DUPONT, de l'Étude WEBER & ASSOCIÉS. "
        "Pour le défendeur, Maître Anne MARTIN du cabinet SCHMIT.")
    assert p["lawyers"][insight.name_key("Jean DUPONT")]["firm"] == "WEBER & ASSOCIÉS"
    assert p["lawyers"][insight.name_key("Anne MARTIN")]["firm"] == "SCHMIT"
    # Sans mention → pas de cabinet.
    assert insight.parse_chunk("Maître Paul REUTER, avocat.")["lawyers"][insight.name_key("Paul REUTER")]["firm"] is None

    insight.record_many([
        ("A A", "Anne A", "d1", 2020, "csj", "A", 1, "Droit du travail", 10000.0, "WEBER & ASSOCIÉS"),
        ("B B", "Bob B", "d1", 2020, "csj", "B", 0, "Droit du travail", 10000.0, "SCHMIT"),
        ("A A", "Anne A", "d2", 2021, "csj", "A", 1, "Bail / logement", 5000.0, "WEBER & ASSOCIÉS"),
        ("C C", "Carl C", "d3", 2021, "tal", "A", 1, "Pénal", None, "WEBER & ASSOCIÉS"),
    ])
    fl = insight.list_firms()
    weber = next(f for f in fl if f["firm"] == "WEBER & ASSOCIÉS")
    assert weber["cases"] == 3 and weber["lawyers"] == 2 and weber["win_rate"] == 1.0
    fp = insight.get_firm("weber & associés")  # insensible à la casse
    assert fp["firm"] == "WEBER & ASSOCIÉS" and fp["lawyers_count"] == 2 and fp["amount_median"] == 7500.0
    assert insight.get_lawyer("A A")["firm"] == "WEBER & ASSOCIÉS"
    assert insight.get_firm("INCONNU") is None


def test_firm_endpoints(temp_db):
    insight.record_many([("A A", "Anne A", "d1", 2020, "csj", "A", 1, "Pénal", None, "ARENDT")])
    assert client.get("/api/insight/firms").status_code == 200
    assert client.get("/api/insight/firms/ARENDT").status_code == 200
    assert client.get("/api/insight/firms/ZZZ").status_code == 404


def test_rgpd_request(temp_db):
    # Demande valide (opposition) -> enregistrée et listable par un admin.
    ok = client.post("/api/insight/rgpd-request",
                     json={"name": "Maître Jean TESTUS", "kind": "opposition", "email": "a@b.lu"})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    # Type invalide -> 422 (garde-fou).
    bad = client.post("/api/insight/rgpd-request", json={"name": "X", "kind": "n_importe_quoi"})
    assert bad.status_code == 422
    # Nom trop court -> 422.
    short = client.post("/api/insight/rgpd-request", json={"name": "x", "kind": "acces"})
    assert short.status_code == 422
    # Store : une seule demande valide enregistrée.
    reqs = insight.list_rgpd_requests()
    assert len(reqs) == 1 and reqs[0]["kind"] == "opposition" and reqs[0]["status"] == "ouverte"

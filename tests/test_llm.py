"""Tests du routeur de modèle par sensibilité (app/llm.py) et de son câblage dans le RAG.

Aucun appel réseau : les fournisseurs sont monkeypatchés.
"""
import pytest

from app import llm, rag
from app.schemas import AskResponse
from app.search import Hit


def test_fournisseur_defaut_anthropic(monkeypatch):
    # Config par défaut : tout sur anthropic (comportement historique inchangé).
    monkeypatch.setattr(llm.settings, "llm_provider_public", "anthropic")
    monkeypatch.setattr(llm.settings, "llm_provider_confidential", "anthropic")
    assert llm.fournisseur("public") == "anthropic"
    assert llm.fournisseur("confidentiel") == "anthropic"


def test_routage_confidentiel_vers_mistral(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider_public", "anthropic")
    monkeypatch.setattr(llm.settings, "llm_provider_confidential", "mistral")
    monkeypatch.setattr(llm.settings, "mistral_model", "mistral-large-latest")
    assert llm.fournisseur("public") == "anthropic"
    assert llm.fournisseur("confidentiel") == "mistral"
    assert llm.modele("confidentiel") == "mistral-large-latest"


def test_fournisseur_inconnu_retombe_sur_anthropic(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider_public", "n-importe-quoi")
    assert llm.fournisseur("public") == "anthropic"


def test_generer_dispatche_selon_sensibilite(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider_public", "anthropic")
    monkeypatch.setattr(llm.settings, "llm_provider_confidential", "local")
    monkeypatch.setattr(llm, "_anthropic", lambda s, m, t: "REP-ANTHROPIC")
    monkeypatch.setattr(llm, "_local", lambda s, m, t: "REP-LOCAL")
    assert llm.generer("sys", [{"role": "user", "content": "q"}], 0.0, "public") == "REP-ANTHROPIC"
    assert llm.generer("sys", [{"role": "user", "content": "q"}], 0.0, "confidentiel") == "REP-LOCAL"


def test_mistral_sans_cle_leve_runtimeerror(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider_confidential", "mistral")
    monkeypatch.setattr(llm.settings, "mistral_api_key", "")
    with pytest.raises(RuntimeError):
        llm.generer("sys", [{"role": "user", "content": "q"}], 0.0, "confidentiel")


def test_panne_fournisseur_normalisee_en_runtimeerror(monkeypatch):
    def _boom(*a, **k):
        raise ValueError("réseau")

    monkeypatch.setattr(llm.settings, "llm_provider_public", "local")
    monkeypatch.setattr(llm, "_local", _boom)
    with pytest.raises(RuntimeError):
        llm.generer("sys", [{"role": "user", "content": "q"}], 0.0, "public")


def test_rag_answer_transmet_la_sensibilite(monkeypatch):
    # rag.answer doit passer la sensibilité au routeur et parser la réponse JSON.
    capte = {}

    def _fake_generer(system_text, messages, temperature, sensibilite):
        capte["sensibilite"] = sensibilite
        return '{"answer": "ok", "used_doc_ids": ["d1"], "status": "ok", "refused": false}'

    monkeypatch.setattr(rag.llm, "generer", _fake_generer)
    hits = [Hit(chunk_id="c", doc_id="d1", text="t", source_type="jurisprudence")]

    rag.answer("q", hits, 0.0)                       # défaut
    assert capte["sensibilite"] == "public"
    resp = rag.answer("q", hits, 0.0, sensibilite="confidentiel")
    assert capte["sensibilite"] == "confidentiel"
    assert isinstance(resp, AskResponse) and resp.answer == "ok"


def test_info_expose_les_deux_sensibilites(monkeypatch):
    monkeypatch.setattr(llm.settings, "llm_provider_public", "anthropic")
    monkeypatch.setattr(llm.settings, "llm_provider_confidential", "mistral")
    info = llm.info()
    assert info["public"]["fournisseur"] == "anthropic"
    assert info["confidentiel"]["fournisseur"] == "mistral"

"""Tests SSO OIDC (A5) : désactivé par défaut, redirection de login, callback qui crée/lie
le compte et émet un jeton de session. Aucun appel réseau (IdP monkeypatché)."""
import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import auth, db, oidc
from app.main import app

client = TestClient(app)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(m.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


def _config_oidc(monkeypatch, frontend=""):
    monkeypatch.setattr(m.settings, "oidc_issuer", "https://idp.test")
    monkeypatch.setattr(m.settings, "oidc_client_id", "jurilux")
    monkeypatch.setattr(m.settings, "oidc_redirect_uri", "https://app.test/api/auth/oidc/callback")
    monkeypatch.setattr(m.settings, "frontend_base_url", frontend)
    monkeypatch.setattr(oidc, "_discover", lambda: {
        "authorization_endpoint": "https://idp.test/auth",
        "token_endpoint": "https://idp.test/token",
        "userinfo_endpoint": "https://idp.test/userinfo"})


def test_oidc_desactive_par_defaut(temp_db):
    assert client.get("/api/auth/oidc/enabled").json() == {"enabled": False}
    assert client.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404


def test_oidc_login_redirige_vers_idp(temp_db, monkeypatch):
    _config_oidc(monkeypatch)
    assert client.get("/api/auth/oidc/enabled").json() == {"enabled": True}
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert r.status_code == 307
    loc = r.headers["location"]
    assert loc.startswith("https://idp.test/auth?") and "state=" in loc and "client_id=jurilux" in loc


def test_oidc_callback_cree_le_compte_et_session(temp_db, monkeypatch):
    _config_oidc(monkeypatch)
    monkeypatch.setattr(oidc, "email_from_callback", lambda code: "avocat@cabinet.lu")
    # obtenir un state valide via le login
    loc = client.get("/api/auth/oidc/login", follow_redirects=False).headers["location"]
    state = loc.split("state=")[1].split("&")[0]

    r = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 200
    token = r.json()["token"]
    # le jeton émis authentifie bien un compte créé à la volée
    assert auth.user_for_token(token)["email"] == "avocat@cabinet.lu"
    # 2e login SSO du même e-mail → même compte (liaison, pas de doublon)
    monkeypatch.setattr(oidc, "email_from_callback", lambda code: "avocat@cabinet.lu")
    loc2 = client.get("/api/auth/oidc/login", follow_redirects=False).headers["location"]
    state2 = loc2.split("state=")[1].split("&")[0]
    r2 = client.get(f"/api/auth/oidc/callback?code=xyz&state={state2}", follow_redirects=False)
    assert auth.user_for_token(r2.json()["token"])["email"] == "avocat@cabinet.lu"


def test_oidc_callback_redirige_vers_le_front(temp_db, monkeypatch):
    _config_oidc(monkeypatch, frontend="https://app.test/")
    monkeypatch.setattr(oidc, "email_from_callback", lambda code: "u@cabinet.lu")
    state = client.get("/api/auth/oidc/login", follow_redirects=False).headers["location"].split("state=")[1].split("&")[0]
    r = client.get(f"/api/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)
    assert r.status_code == 307 and "token=" in r.headers["location"]


def test_oidc_callback_state_invalide(temp_db, monkeypatch):
    _config_oidc(monkeypatch)
    assert client.get("/api/auth/oidc/callback?code=abc&state=FAUX",
                      follow_redirects=False).status_code == 400

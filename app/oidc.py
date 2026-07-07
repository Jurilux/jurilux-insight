"""SSO entreprise via OpenID Connect (A5) — optionnel, activé par la config.

Flux Authorization Code : l'utilisateur est redirigé vers l'IdP du cabinet (Keycloak,
Azure AD, Google…), qui renvoie un `code` ; on l'échange contre des jetons, on lit l'e-mail
via userinfo, puis on crée/lie le compte et on émet **notre** jeton de session (pbkdf2).
Aucun mot de passe géré côté Jurilux pour ces comptes. Dépendances minimales : `urllib`.

Désactivé si `OIDC_ISSUER`/`OIDC_CLIENT_ID` sont vides → le login mot de passe reste seul.
"""
from __future__ import annotations

import json
import secrets
import time
import urllib.parse
import urllib.request
from typing import Optional

from .config import settings

_states: dict = {}          # state -> expiration (protection CSRF, TTL court)
_STATE_TTL = 600.0
_discovery_cache: dict = {"at": 0.0, "data": None}


def enabled() -> bool:
    return bool(settings.oidc_issuer and settings.oidc_client_id and settings.oidc_redirect_uri)


def _discover() -> dict:
    """Endpoints OIDC via le document de découverte (.well-known), mis en cache."""
    if _discovery_cache["data"] and time.monotonic() - _discovery_cache["at"] < 3600:
        return _discovery_cache["data"]
    url = settings.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    _discovery_cache["at"], _discovery_cache["data"] = time.monotonic(), data
    return data


def new_state() -> str:
    _purge_states()
    s = secrets.token_urlsafe(24)
    _states[s] = time.monotonic() + _STATE_TTL
    return s


def check_state(state: Optional[str]) -> bool:
    _purge_states()
    return bool(state) and _states.pop(state, None) is not None


def _purge_states() -> None:
    now = time.monotonic()
    for k in [k for k, exp in _states.items() if exp < now]:
        _states.pop(k, None)


def login_url(state: str) -> str:
    conf = _discover()
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": settings.oidc_redirect_uri,
        "scope": "openid email profile",
        "state": state,
    }
    return conf["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    conf = _discover()
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
    }).encode()
    req = urllib.request.Request(conf["token_endpoint"], data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def userinfo(access_token: str) -> dict:
    conf = _discover()
    req = urllib.request.Request(conf["userinfo_endpoint"],
                                 headers={"Authorization": "Bearer " + access_token})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def email_from_callback(code: str) -> Optional[str]:
    """Échange le code puis renvoie l'e-mail vérifié de l'utilisateur (ou None)."""
    toks = exchange_code(code)
    at = toks.get("access_token")
    if not at:
        return None
    info = userinfo(at)
    email = info.get("email")
    return email.strip().lower() if isinstance(email, str) and email.strip() else None

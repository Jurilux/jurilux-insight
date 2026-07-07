"""Couverture : authentification & compte — inscription, connexion, déconnexion,
changement de mot de passe (succès + toutes les branches d'erreur/validation)."""
from __future__ import annotations

from ._base import *


def _pre_email_pris(banc: Banc, nom: str) -> tuple:
    """Enregistre un e-mail au préalable, pour tester le doublon."""
    c = banc.enregistrer()
    return {}, {"email_pris": c["email"]}


CAS = [
    # --- inscription ---
    CasUsage("register-ok", "Auth — inscription",
             "Inscription valide → {token, user:{email}}.",
             "POST", "/api/auth/register",
             {"anonyme": ok(lambda j: bool(j.get("token")) and "email" in j["user"])},
             corps={"email": "nouveau@test.lu", "password": "password123"}),
    CasUsage("register-doublon", "Auth — inscription",
             "E-mail déjà utilisé → 400.",
             "POST", "/api/auth/register", {"anonyme": refuse(400)},
             corps=lambda c: {"email": c["email_pris"], "password": "password123"},
             preparer=_pre_email_pris),
    CasUsage("register-email-invalide", "Auth — inscription",
             "E-mail mal formé → 400.",
             "POST", "/api/auth/register", {"anonyme": refuse(400)},
             corps={"email": "pas-un-email", "password": "password123"}),
    CasUsage("register-mdp-court", "Auth — inscription",
             "Mot de passe < 8 caractères → 400.",
             "POST", "/api/auth/register", {"anonyme": refuse(400)},
             corps={"email": "court@test.lu", "password": "abc"}),
    CasUsage("register-champ-manquant", "Auth — inscription",
             "Champ password absent → 422 (validation Pydantic).",
             "POST", "/api/auth/register", {"anonyme": refuse(422)},
             corps={"email": "x@test.lu"}),

    # --- connexion ---
    CasUsage("login-mauvais-mdp", "Auth — connexion",
             "Mot de passe incorrect → 401.",
             "POST", "/api/auth/login", {"anonyme": refuse(401)},
             corps=lambda c: {"email": c["email_pris"], "password": "FAUXfaux9"},
             preparer=_pre_email_pris),
    CasUsage("login-email-inconnu", "Auth — connexion",
             "E-mail inconnu → 401.",
             "POST", "/api/auth/login", {"anonyme": refuse(401)},
             corps={"email": "jamais@test.lu", "password": "password123"}),
    CasUsage("login-champ-manquant", "Auth — connexion",
             "Champ manquant → 422.",
             "POST", "/api/auth/login", {"anonyme": refuse(422)},
             corps={"email": "x@test.lu"}),

    # --- déconnexion ---
    CasUsage("logout-connecte", "Auth — déconnexion",
             "Déconnexion d'un compte connecté → {ok:true}.",
             "POST", "/api/auth/logout",
             {p: ok(lambda j: j.get("ok") is True) for p in AUTHENTIFIES}),
    CasUsage("logout-anonyme-idempotent", "Auth — déconnexion",
             "Déconnexion sans jeton : tolérée (idempotente) → {ok:true}.",
             "POST", "/api/auth/logout", {"anonyme": ok(lambda j: j.get("ok") is True)}),

    # --- changement de mot de passe ---
    CasUsage("change-mdp-ok", "Auth — mot de passe",
             "Changement valide (ancien correct) → {ok:true}.",
             "POST", "/api/auth/change-password",
             {p: ok(lambda j: j.get("ok") is True) for p in AUTHENTIFIES},
             corps={"old_password": "password123", "new_password": "nouveauMDP2026"}),
    CasUsage("change-mdp-anonyme", "Auth — mot de passe",
             "Sans authentification → 401.",
             "POST", "/api/auth/change-password", {"anonyme": refuse(401)},
             corps={"old_password": "password123", "new_password": "nouveauMDP2026"}),
    CasUsage("change-mdp-ancien-faux", "Auth — mot de passe",
             "Ancien mot de passe incorrect → 400.",
             "POST", "/api/auth/change-password",
             {"etudiant": refuse(400), "pro": refuse(400)},
             corps={"old_password": "MAUVAIS999", "new_password": "nouveauMDP2026"}),
    CasUsage("change-mdp-nouveau-court", "Auth — mot de passe",
             "Nouveau mot de passe < 8 → 400.",
             "POST", "/api/auth/change-password", {"etudiant": refuse(400)},
             corps={"old_password": "password123", "new_password": "abc"}),
]

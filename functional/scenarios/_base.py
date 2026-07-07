"""Briques communes à tous les modules de scénarios.

Un module de domaine commence par `from ._base import *` et dispose alors de :
  - modèles du moteur : `CasUsage`, `Etape`/`E`, `Parcours`, attentes `ok`/`refuse`/`gracieux` ;
  - listes de profils : `COMPTE`, `AUTHENTIFIES` ;
  - références pour les **stubs par scénario** (branches d'erreur) : `SEARCH`, `RAG`, `VAULT`,
    `MAIN`, `SETTINGS` ; plus `Hit` pour fabriquer des extraits.

Convention : chaque module expose une liste `CAS` (cas d'usage matrice) et/ou `PARCOURS`.
"""
from __future__ import annotations

import app.main as MAIN
from app import rag as RAG
from app import search as SEARCH
from app import vault as VAULT
from app.config import settings as SETTINGS
from app.search import Hit

from ..banc import Banc
from ..engine import Attente, CasUsage, Etape, Parcours, gracieux, ok, refuse

E = Etape
COMPTE = ["anonyme", "etudiant", "pro", "admin"]        # les 4 profils compte
AUTHENTIFIES = ["etudiant", "pro", "admin"]             # profils connectés

__all__ = [
    "Banc", "CasUsage", "Etape", "E", "Parcours", "Attente", "ok", "refuse", "gracieux",
    "SEARCH", "RAG", "VAULT", "MAIN", "SETTINGS", "Hit", "COMPTE", "AUTHENTIFIES",
]

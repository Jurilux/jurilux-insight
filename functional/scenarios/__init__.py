"""Agrège les cas d'usage (matrice) et parcours de tous les modules de domaine.

Chaque module expose `CAS` (cas matrice) et/ou `PARCOURS` (parcours utilisateur). L'import
est **tolérant** : un module en cours d'édition (ou cassé) est ignoré avec un avertissement,
sans faire tomber tout le paquet — pratique pendant le développement en parallèle.
"""
import importlib

_NOMS = ["service", "auth", "ask", "feedback_partage", "insight", "cabinet", "veille",
         "vault", "admin", "socle"]

_MODULES = []
for _n in _NOMS:
    try:
        _MODULES.append(importlib.import_module(f".{_n}", __name__))
    except Exception as _e:  # module en cours d'écriture : on l'ignore sans tout casser
        print(f"[scenarios] module « {_n} » ignoré ({type(_e).__name__}: {_e})")

CAS = [c for mod in _MODULES for c in getattr(mod, "CAS", [])]
PARCOURS = [p for mod in _MODULES for p in getattr(mod, "PARCOURS", [])]

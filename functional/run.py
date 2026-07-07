"""Point d'entrée CLI du moteur de tests fonctionnels.

    python -m functional.run                      # rapport texte, code de sortie 0/1
    python -m functional.run --format markdown
    python -m functional.run --format json
    python -m functional.run --fonctionnalite Vault   # filtre par fonctionnalité (sous-chaîne)
"""
from __future__ import annotations

import argparse
import logging
import sys

from .banc import Banc
from .cas import CAS
from .engine import Moteur, Rapport
from .parcours import PARCOURS


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Moteur de tests fonctionnels Jurilux")
    ap.add_argument("--format", choices=["texte", "markdown", "json"], default="texte")
    ap.add_argument("--mode", choices=["tout", "parcours", "matrice"], default="tout",
                    help="parcours utilisateur, matrice d'autorisation, ou les deux")
    ap.add_argument("--filtre", default=None,
                    help="ne garder que les parcours/cas dont le libellé contient cette sous-chaîne")
    args = ap.parse_args(argv)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # silence le journal des requêtes

    parcours = PARCOURS
    cas = CAS
    if args.filtre:
        f = args.filtre.lower()
        parcours = [p for p in PARCOURS if f in (p.id + p.objectif).lower()]
        cas = [c for c in CAS if f in c.fonctionnalite.lower()]

    moteur = Moteur()
    with Banc() as banc:
        res_parcours = moteur.executer_parcours(banc, parcours) if args.mode in ("tout", "parcours") else []
    with Banc() as banc:
        res_matrice = moteur.executer(banc, cas) if args.mode in ("tout", "matrice") else []

    rp, rm = Rapport(res_parcours), Rapport(res_matrice)
    if args.format == "json":
        import json as _j
        print(_j.dumps({"parcours": _j.loads(rp.json()), "matrice": _j.loads(rm.json())},
                       ensure_ascii=False, indent=2))
    else:
        rendu = "markdown" if args.format == "markdown" else "texte"
        if res_parcours:
            print("═══════════ PARCOURS UTILISATEUR ═══════════\n")
            print(getattr(rp, rendu)())
        if res_matrice:
            print("\n═══════════ MATRICE D'AUTORISATION ═══════════\n")
            print(getattr(rm, rendu)())
    return 1 if (rp.code_sortie() or rm.code_sortie()) else 0


if __name__ == "__main__":
    sys.exit(main())

"""Passerelle CI : moteur de tests fonctionnels (parcours + matrice) 100 % vert.

En cas d'échec, le rapport texte complet est affiché dans l'assertion.
"""
from functional.banc import Banc
from functional.cas import CAS
from functional.engine import Moteur, Rapport
from functional.parcours import PARCOURS


def test_parcours_utilisateur_tout_vert():
    with Banc() as banc:
        resultats = Moteur().executer_parcours(banc, PARCOURS)
    rapport = Rapport(resultats)
    assert rapport.code_sortie() == 0, "\n" + rapport.texte()


def test_matrice_autorisation_tout_vert():
    with Banc() as banc:
        resultats = Moteur().executer(banc, CAS)
    rapport = Rapport(resultats)
    assert rapport.code_sortie() == 0, "\n" + rapport.texte()


def test_moteur_detecte_un_echec():
    """Méta-test : si une attente est fausse, le moteur DOIT le voir (anti faux-vert)."""
    from functional.engine import CasUsage, refuse

    faux = [CasUsage("meta-doit-echouer", "Méta", "un /health public renvoyé comme 401",
                     "GET", "/health", {"anonyme": refuse(401)})]
    with Banc() as banc:
        resultats = Moteur().executer(banc, faux)
    assert Rapport(resultats).code_sortie() == 1

"""Serveur de démonstration E2E : lance l'app RÉELLE (`app.main:app`) avec les services
externes stubés (Meilisearch/Anthropic/Ollama) via `functional.banc`, des comptes et des
données de démo seedés, et un flux `rag.answer_stream` qui émet un vrai « parcours guidé »
(`follow_ups`) + « autre angle » (`suggested_question`).

But : permettre à un navigateur (Chromium/Playwright) de parcourir TOUS les écrans sans
aucune dépendance externe. Rien n'est écrit hors d'une base SQLite jetable.

Lancement :
    python -m functional.e2e_server            # écoute 127.0.0.1:8088

Comptes de démo (mot de passe : password123) :
    etudiant@demo.lu (plan étudiant, quota bientôt épuisé)
    pro@demo.lu      (plan pro, illimité + Vault/cabinet)
    admin@demo.lu    (admin, accès backoffice)
"""
from __future__ import annotations

import os

import uvicorn

import app.main as m
from app import auth, db, rag, search, vault
from app.main import app
from app.schemas import AskResponse, Citation
from functional.banc import HITS_CORPUS, Banc

MDP = "password123"
# Requêtes « hors droit LU » : le stub de recherche ne renvoie AUCUN extrait → refus gracieux
# (permet de tester le parcours « aucun document pertinent »).
_HORS_SUJET = ("martien", "recette", "météo", "football")

# --- réponse RAG de démo : sourcée, avec parcours guidé + autre angle ---
_CITES = [
    Citation(doc_id="csj_ch08_2019_demo1", title="CSJ 8e ch., 12 mars 2019", source_type="jurisprudence"),
    Citation(doc_id="eli-etat-leg-loi-2006-07-31", title="Code du travail, art. L.124-10", source_type="law"),
]
_ANSWER = (
    "En droit du travail luxembourgeois, le licenciement avec effet immédiat suppose une "
    "**faute grave** rendant immédiatement impossible le maintien de la relation de travail "
    "(art. L.124-10 du Code du travail). Les motifs doivent être notifiés de façon précise et "
    "circonstanciée ; à défaut, le licenciement est jugé abusif.\n"
)
_FOLLOW_UPS = [
    "Quel délai l'employeur a-t-il pour notifier les motifs après la découverte des faits ?",
    "Comment la lettre de motivation doit-elle être rédigée pour être valable ?",
    "Quelles indemnités le salarié obtient-il si le licenciement est jugé abusif ?",
    "Sur qui pèse la charge de la preuve de la faute grave devant les juridictions ?",
]
_SUGGESTED = "Le salarié peut-il contester un licenciement pour faute grave devant le tribunal du travail ?"


def _answer_demo(q, hits, temperature=0.0, pedagogical=False, history=None, **kw) -> AskResponse:
    if not hits:  # aucun extrait pertinent → refus gracieux (jamais d'invention)
        return AskResponse(answer=None, citations=[], refused=True, status="ok",
                           prompt_version="demo")
    return AskResponse(answer=_ANSWER, citations=list(_CITES), refused=False, status="ok",
                       suggested_question=_SUGGESTED, follow_ups=list(_FOLLOW_UPS),
                       prompt_version="demo")


def _answer_stream_demo(q, hits, temperature, pedagogical=False, history=None):
    """Même contrat d'événements que `rag.answer_stream` : deltas puis meta (avec follow_ups)."""
    if not hits:
        why = "Aucun document pertinent dans le corpus pour cette question."
        yield {"type": "delta", "text": why}
        yield {"type": "meta", "answer": None, "citations": [], "refused": True, "status": "ok",
               "suggested_question": None, "follow_ups": None, "feedback": {"why": why},
               "prompt_version": "demo"}
        return
    for i in range(0, len(_ANSWER), 60):
        yield {"type": "delta", "text": _ANSWER[i:i + 60]}
    yield {"type": "meta", "answer": _ANSWER,
           "citations": [c.model_dump() for c in _CITES],
           "refused": False, "status": "ok", "suggested_question": _SUGGESTED,
           "follow_ups": list(_FOLLOW_UPS), "feedback": None, "prompt_version": "demo"}


def _compte(banc: Banc, email: str, plan: str, admin: bool = False) -> dict:
    """Crée un compte à e-mail FIXE (identifiants de démo prévisibles) et le renvoie."""
    tok = banc.client.post("/api/auth/register",
                           json={"email": email, "password": MDP}).json()["token"]
    uid = auth.user_for_token(tok)["id"]
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET plan = ?, is_admin = ? WHERE id = ?",
                     (plan, 1 if admin else 0, uid))
    return {"headers": {"Authorization": f"Bearer {tok}"}, "uid": uid, "email": email}


def _post(banc: Banc, path: str, headers: dict, body: dict):
    return banc.client.post(path, json=body, headers=headers)


def seed(banc: Banc) -> dict:
    """Provisionne un jeu de données RÉALISTE multi-cabinets : plusieurs profils, rôles,
    cloison déontologique, isolation Vault, veille, playbook/prompt/clé. Renvoie un index."""
    # ---- comptes individuels ----
    etu = _compte(banc, "etudiant@demo.lu", "student")
    pro = _compte(banc, "pro@demo.lu", "pro")
    _compte(banc, "admin@demo.lu", "student", admin=True)

    # ---- Cabinet A : Étude Dupont & Associés (owner + admin + collaborateur) ----
    dupont_owner = _compte(banc, "dupont.owner@demo.lu", "pro")
    dupont_asso = _compte(banc, "dupont.associe@demo.lu", "pro")
    dupont_collab = _compte(banc, "dupont.collab@demo.lu", "student")
    wid_a = banc.creer_espace(dupont_owner["headers"], "Étude Dupont & Associés")
    banc.ajouter_membre(dupont_owner["headers"], wid_a, dupont_asso["email"], "admin")
    banc.ajouter_membre(dupont_owner["headers"], wid_a, dupont_collab["email"], "member")
    did_open = banc.creer_dossier(dupont_owner["headers"], wid_a, "Dossier Martin c/ SA X")
    did_restr = banc.creer_dossier(dupont_owner["headers"], wid_a, "Affaire Étoile (confidentiel)")
    # cloison déontologique : dossier restreint, accordé à l'associé, PAS au collaborateur
    _post(banc, f"/api/dossiers/{did_restr}/restrict", dupont_owner["headers"], {"restricted": True})
    _post(banc, f"/api/dossiers/{did_restr}/access", dupont_owner["headers"],
          {"email": dupont_asso["email"]})
    banc.creer_alerte(dupont_owner["headers"], "bail commercial résiliation")
    # playbook + prompt partagés au cabinet, et une clé d'API de service
    _post(banc, "/api/playbooks", dupont_owner["headers"], {
        "name": "NDA standard", "workspace_id": wid_a,
        "rules": [{"label": "Clause de confidentialité", "instruction": "Vérifier la présence d'une clause de confidentialité."},
                  {"label": "Loi applicable", "instruction": "Vérifier que la loi luxembourgeoise s'applique."}]})
    _post(banc, "/api/prompts", dupont_owner["headers"],
          {"title": "Résumé d'arrêt", "body": "Résume l'arrêt en 5 points.", "workspace_id": wid_a})
    _post(banc, "/api/keys", dupont_owner["headers"], {"name": "Intégration compta"})

    # ---- Cabinet B : Cabinet Weber (séparé — sert à prouver l'isolation inter-cabinet) ----
    weber_owner = _compte(banc, "weber.owner@demo.lu", "pro")
    wid_b = banc.creer_espace(weber_owner["headers"], "Cabinet Weber")
    banc.creer_dossier(weber_owner["headers"], wid_b, "Dossier Weber interne")

    # ---- Vault : isolation par propriétaire (pro a 2 docs, l'associé Dupont 1 doc) ----
    banc.deposer_doc(pro["headers"], "contrat_bail.txt", b"Le present contrat de bail prevoit un preavis de trois mois.")
    banc.deposer_doc(pro["headers"], "conclusions.txt", b"Conclusions en defense pour Monsieur Martin.")
    banc.deposer_doc(dupont_asso["headers"], "nda_client.txt", b"Accord de confidentialite entre les parties.")

    # ---- historique pour l'étudiant (matière première backoffice) ----
    for q in ["Qu'est-ce qu'une faute grave ?", "Délai de préavis légal ?", "Congé parental : conditions ?"]:
        auth.add_history(etu["uid"], q, "Réponse de démo sourcée.", "ok")

    # ---- permalien public ----
    share_id = banc.creer_partage()

    return {"share_id": share_id, "wid_dupont": wid_a, "wid_weber": wid_b,
            "did_restreint": did_restr, "did_ouvert": did_open}


def main() -> None:
    banc = Banc()
    banc.__enter__()  # installe stubs + base jetable + init_db + insight ; NON restauré (serveur vivant)
    # réponses RAG de démo enrichies (parcours guidé + autre angle), y compris en streaming
    banc._regler(rag, "answer", _answer_demo)
    banc._regler(rag, "answer_stream", _answer_stream_demo)
    # recherche : extraits déterministes, SAUF requêtes hors droit LU → aucun extrait (refus)
    banc._regler(search, "search",
                 lambda q, k, f: [] if any(x in q.lower() for x in _HORS_SUJET) else list(HITS_CORPUS))
    idx = seed(banc)

    host = os.environ.get("E2E_HOST", "127.0.0.1")
    port = int(os.environ.get("E2E_PORT", "8088"))
    print(f"[e2e] app réelle stubée sur http://{host}:{port}")
    print(f"[e2e] comptes (mdp={MDP}) : etudiant@demo.lu · pro@demo.lu · admin@demo.lu ·")
    print(f"[e2e]   Cabinet Dupont : dupont.owner@ · dupont.associe@ · dupont.collab@demo.lu")
    print(f"[e2e]   Cabinet Weber  : weber.owner@demo.lu")
    print(f"[e2e] permalien démo : /r/{idx['share_id']}  · cabinets #{idx['wid_dupont']} (Dupont) #{idx['wid_weber']} (Weber)")
    print(f"[e2e] SHARE_ID={idx['share_id']}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

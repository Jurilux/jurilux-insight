"""Banc d'essai : base SQLite jetable, services externes stubés (Meilisearch/Anthropic),
données de test injectées, et provisionnement des profils.

Tout est restauré à la sortie du gestionnaire de contexte (`with Banc() as banc:`) pour ne
pas polluer les autres tests du même process.
"""
from __future__ import annotations

import tempfile

from fastapi.testclient import TestClient

import app.main as m
from app import alert_runner, auth, db, insight, rag, search, vault
from app.main import app
from app.schemas import AskResponse, Citation
from app.search import Hit

# --------- données de test injectées (corpus public déterministe) ---------
HITS_CORPUS = [
    Hit(chunk_id="c1", doc_id="csj_ch08_2019_demo1", text="La faute grave prive le salarié de son préavis.",
        title="CSJ 8e ch.", year=2019, juridiction_key="csj_ch08", source_type="jurisprudence"),
    Hit(chunk_id="c2", doc_id="eli-etat-leg-loi-2006-07-31", text="Art. L.124-10 du Code du travail.",
        title="Code du travail", year=2006, source_type="law",
        pdf_url="https://legilux.public.lu/x.pdf"),
]
HITS_VAULT = [
    Hit(chunk_id="v1", doc_id="1", text="Extrait de votre document déposé.", title="contrat.pdf"),
]
CORPUS_META = {"decisions": 1200, "texts": 340, "projets": 12, "updated": "2026-06-01",
               "chunks": 5000, "latest_year": 2026, "by_source": {"jurisprudence": 4600, "law": 400}}
INSIGHT_ROWS = [
    # 9e = montant € ; 10e = cabinet ; 11e = articles visés (liste) ; 12e = sens du dispositif.
    ("MAITRE JEAN DUPONT", "Jean Dupont", "csj_ch08_2019_demo1", 2019, "csj_ch08", "A", 1, "Droit du travail", 20000.0, "ÉTUDE WEBER", ["L.124-10"], "confirmation"),
    ("MAITRE JEAN DUPONT", "Jean Dupont", "csj_ch08_2020_demo2", 2020, "csj_ch08", "B", 0, "Bail / logement", 8000.0, "ÉTUDE WEBER", ["1719"], "réformation"),
    ("MAITRE ANNE MARTIN", "Anne Martin", "csj_ch08_2019_demo1", 2019, "csj_ch08", "B", 0, "Droit du travail", 20000.0, None, ["L.124-10"], "confirmation"),
]

# profils « compte » (dimension plan/admin) et leurs libellés d'affichage
PROFILS_COMPTE = ["anonyme", "etudiant", "pro", "admin"]
LIBELLES = {"anonyme": "anonyme", "etudiant": "étudiant", "pro": "pro", "admin": "admin"}


def _reponse_rag(q, hits, temperature=0.0, pedagogical=False, history=None, **kw) -> AskResponse:
    """Remplace `rag.answer` : réponse sourcée déterministe, sans appel Anthropic."""
    cites = [Citation(doc_id=h.doc_id, title=h.title,
                      source_type=h.source_type if h.source_type in ("jurisprudence", "law", "projet_loi") else None)
             for h in hits[:5]]
    return AskResponse(answer="Réponse de test sourcée.", citations=cites,
                       refused=False, status="ok", prompt_version="test")


class Banc:
    """Environnement d'exécution isolé du moteur. Installe les stubs, injecte les données,
    provisionne les comptes ; restaure tout à la sortie."""

    def __init__(self) -> None:
        self.client = TestClient(app)
        self._restaurations: list = []
        self._n = 0  # compteur pour des e-mails uniques

    # ---- installation / restauration ----
    def _regler(self, obj, attr, valeur) -> None:
        self._restaurations.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, valeur)

    def __enter__(self) -> "Banc":
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        # config : base jetable, pas de rate-limit, clé LLM présente, quota étudiant petit
        self._regler(m.settings, "db_path", tmp.name)
        self._regler(m.settings, "rate_limit_per_min", 0)
        self._regler(m.settings, "anthropic_api_key", "sk-test")
        self._regler(m.settings, "student_monthly_quota", 5)
        db.init_db()
        # stubs des services externes
        self._regler(search, "meili_healthy", lambda: True)
        self._regler(search, "corpus_overview", lambda: CORPUS_META)
        self._regler(search, "search", lambda q, k, f: list(HITS_CORPUS))
        self._regler(rag, "answer", _reponse_rag)
        self._regler(vault, "index_chunks", lambda o, d, f, t: 2)
        self._regler(vault, "search_vault", lambda o, q, ids, k: list(HITS_VAULT))
        self._regler(vault, "delete_chunks", lambda o, d: None)
        self._regler(vault, "corpus_search", lambda q, k, f: list(HITS_CORPUS) if "124-10" in q else [])
        self._regler(alert_runner, "check", lambda al: 0)
        self._regler(rag, "resume", lambda texte, sensibilite="confidentiel": "Résumé de test.")
        self._regler(rag, "rediger", lambda instruction, hits, sensibilite="public": {
            "answer": "Document rédigé (test).",
            "citations": [Citation(doc_id="eli-etat-leg-loi-2006-07-31", source_type="law")],
            "refused": False})
        self._regler(rag, "contre_argumentaire",
                     lambda texte, hits, sensibilite="confidentiel": {
                         "answer": "Contre-argumentaire de test.",
                         "citations": [Citation(doc_id="csj_ch08_2019_demo1", source_type="jurisprudence")],
                         "refused": False})
        self._regler(rag, "revue_contrat", lambda texte, rules, sensibilite="confidentiel": {
            "findings": [{"label": r.get("label", ""), "status": "ok", "note": "Conforme."} for r in rules]})
        # données injectées
        insight.record_many(INSIGHT_ROWS)
        return self

    def __exit__(self, *exc) -> None:
        for obj, attr, valeur in reversed(self._restaurations):
            setattr(obj, attr, valeur)
        self._restaurations.clear()

    # ---- provisionnement des profils « compte » ----
    def enregistrer(self, plan: str = "student", admin: bool = False) -> dict:
        """Crée un compte unique et renvoie {headers, uid, email}."""
        self._n += 1
        email = f"u{self._n}@test.lu"
        tok = self.client.post("/api/auth/register",
                               json={"email": email, "password": "password123"}).json()["token"]
        uid = auth.user_for_token(tok)["id"]
        if plan != "student" or admin:
            with db.get_conn() as conn:
                conn.execute("UPDATE users SET plan = ?, is_admin = ? WHERE id = ?",
                             (plan, 1 if admin else 0, uid))
        return {"headers": {"Authorization": f"Bearer {tok}"}, "uid": uid, "email": email}

    def profil(self, nom: str) -> tuple:
        """Renvoie (headers, contexte) pour un profil compte. Contexte : uid/email si connecté."""
        if nom == "anonyme":
            return {}, {}
        compte = self.enregistrer(plan="pro" if nom == "pro" else "student", admin=(nom == "admin"))
        return compte["headers"], {"uid": compte["uid"], "email": compte["email"]}

    # ---- aides de scénario (injection d'entités via l'API, en dogfooding) ----
    def saturer_quota(self, uid: int) -> None:
        """Injecte assez d'historique pour épuiser le quota mensuel étudiant."""
        for i in range(m.settings.student_monthly_quota):
            auth.add_history(uid, f"question {i}", "réponse", "ok")

    def creer_espace(self, headers: dict, nom: str = "Cabinet Test") -> int:
        return self.client.post("/api/workspaces", json={"name": nom}, headers=headers).json()["id"]

    def ajouter_membre(self, headers_owner: dict, wid: int, email: str, role: str) -> None:
        self.client.post(f"/api/workspaces/{wid}/members",
                         json={"email": email, "role": role}, headers=headers_owner)

    def creer_partage(self) -> str:
        return self.client.post("/api/share",
                                json={"question": "Q ?", "answer": "A", "status": "ok"}).json()["id"]

    def creer_dossier(self, headers: dict, wid: int, nom: str = "Dossier Test") -> int:
        return self.client.post(f"/api/workspaces/{wid}/dossiers",
                                json={"name": nom}, headers=headers).json()["id"]

    def creer_alerte(self, headers: dict, query: str = "licenciement") -> int:
        return self.client.post("/api/alerts", json={"query": query}, headers=headers).json()["id"]

    def deposer_doc(self, headers: dict, filename: str, contenu: bytes) -> int:
        r = self.client.post(f"/api/vault/documents?filename={filename}", content=contenu,
                             headers={**headers, "Content-Type": "text/plain"})
        return r.json()["id"]

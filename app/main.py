"""Jurilux API — FastAPI sur 127.0.0.1:8088 (relayé par Caddy).

Endpoints :
  GET  /health   — 200 si l'API tourne ET que Meilisearch répond, sinon 503.
                   (Le front affiche « Connecté » sur res.ok : un health strict
                   corrige le voyant vert indulgent.)
  POST /api/ask  — RAG : Meilisearch -> Claude -> AskResponse.
"""
import json
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import (admin, alert as alert_store, alert_runner, apikeys, audit, auth, config_store,
               db, feedback as fb_store, insight, llm, metrics, oidc, playbooks as pb_store,
               prompts as prompts_store, rag, rgpd, search, share as share_store, vault,
               workspace as ws)
from .config import settings
from .schemas import (AskRequest, AskResponse, DossierCreate, DossierItemAdd, FeedbackIn,
                      MemberAdd, SearchFilters, ShareIn, WorkspaceCreate)

log = logging.getLogger("jurilux")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Jurilux API", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    try:
        db.init_db()
    except Exception:
        log.exception("init DB (espace utilisateur) a échoué")

# Rate-limit /api/ask par IP (fenêtre glissante 60 s, en mémoire process).
# Chaque appel consomme un crédit LLM → garde-fou anti-abus/coût sur endpoint public.
_RL_WINDOW = 60.0
_rl_hits: "defaultdict[str, deque]" = defaultdict(deque)


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_ok(ip: str) -> bool:
    limit = settings.rate_limit_per_min
    if limit <= 0:
        return True
    now = time.time()
    dq = _rl_hits[ip]
    while dq and dq[0] <= now - _RL_WINDOW:
        dq.popleft()
    if len(dq) >= limit:
        return False
    dq.append(now)
    return True


@app.get("/health")
def health(response: Response) -> dict:
    meili_ok = search.meili_healthy()
    llm_ok = bool(settings.anthropic_api_key)
    ok = meili_ok and llm_ok
    if not ok:
        response.status_code = 503
    return {
        "status": "ok" if ok else "degraded",
        "meilisearch": meili_ok,
        "llm_configured": llm_ok,
        "prompt_version": settings.prompt_version,
    }


@app.get("/api/corpus")
def corpus() -> dict:
    """Périmètre du corpus, pour affichage front (« X décisions · Y textes · à jour »)."""
    return search.corpus_overview()


@app.get("/api/metrics")
def get_metrics() -> dict:
    """Métriques d'observabilité (compteurs volatils par process)."""
    return metrics.snapshot()


# ---------- Espace utilisateur ----------
class Credentials(BaseModel):
    email: str
    password: str


def _current_user(authorization: Optional[str], x_api_key: Optional[str] = None) -> Optional[dict]:
    u = auth.user_for_token(auth.token_from_header(authorization))
    return u or apikeys.user_for_key(x_api_key)


def _require_user(authorization: Optional[str]) -> dict:
    user = _current_user(authorization)
    if not user:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return user


def _is_admin(user: dict) -> bool:
    """Admin = flag is_admin en base OU email présent dans l'allowlist ADMIN_EMAILS."""
    return bool(user.get("is_admin")) or (
        (user.get("email") or "").lower() in settings.admin_email_set)


def _require_admin(authorization: Optional[str]) -> dict:
    user = _require_user(authorization)
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Accès réservé aux administrateurs")
    return user


@app.post("/api/auth/register")
def register(creds: Credentials) -> dict:
    try:
        user = auth.create_user(creds.email, creds.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": auth.create_session(user["id"]), "user": {"email": user["email"]}}


@app.post("/api/auth/login")
def login(creds: Credentials) -> dict:
    user = auth.authenticate(creds.email, creds.password)
    if not user:
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect")
    audit.log("auth.login", user)
    return {"token": auth.create_session(user["id"]), "user": {"email": user["email"]}}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)) -> dict:
    auth.delete_session(auth.token_from_header(authorization))
    return {"ok": True}


# ---------- SSO entreprise (OIDC) — optionnel, actif si configuré ----------
@app.get("/api/auth/oidc/enabled")
def oidc_enabled() -> dict:
    """Le front n'affiche le bouton « SSO » que si l'IdP du cabinet est configuré."""
    return {"enabled": oidc.enabled()}


@app.get("/api/auth/oidc/login")
def oidc_login() -> Response:
    if not oidc.enabled():
        raise HTTPException(status_code=404, detail="SSO non configuré")
    url = oidc.login_url(oidc.new_state())
    return Response(status_code=307, headers={"Location": url})


@app.get("/api/auth/oidc/callback")
def oidc_callback(code: Optional[str] = None, state: Optional[str] = None) -> Response:
    if not oidc.enabled():
        raise HTTPException(status_code=404, detail="SSO non configuré")
    if not code or not oidc.check_state(state):
        raise HTTPException(status_code=400, detail="Requête OIDC invalide (code/state)")
    try:
        email = oidc.email_from_callback(code)
    except Exception:
        log.exception("échange OIDC")
        raise HTTPException(status_code=502, detail="L'IdP n'a pas répondu")
    if not email:
        raise HTTPException(status_code=400, detail="Aucun e-mail fourni par l'IdP")
    user = auth.ensure_user(email)
    token = auth.create_session(user["id"])
    audit.log("auth.sso", user)
    # Retour vers le front avec le jeton en fragment (jamais loggé), sinon JSON.
    if settings.frontend_base_url:
        sep = "#" if "#" not in settings.frontend_base_url else "&"
        return Response(status_code=307,
                        headers={"Location": f"{settings.frontend_base_url}{sep}token={token}"})
    return JSONResponse({"token": token, "user": {"email": user["email"]}})


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


@app.post("/api/auth/change-password")
def change_password(body: PasswordChange,
                    authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400,
                            detail="mot de passe trop court (8 caractères minimum)")
    if not auth.change_password(user["id"], body.old_password, body.new_password):
        raise HTTPException(status_code=400, detail="mot de passe actuel incorrect")
    return {"ok": True}


@app.post("/api/feedback")
def submit_feedback(body: FeedbackIn,
                    authorization: Optional[str] = Header(None)) -> dict:
    """Retour utilisateur (👍/👎 + ce qui manquait). Ouvert aux anonymes ; on
    rattache au compte s'il est connecté. Best-effort, ne bloque jamais."""
    user = _current_user(authorization)
    try:
        fb_store.add(user["id"] if user else None, body.question, body.helpful,
                     body.missing, body.status, settings.prompt_version)
    except Exception:
        log.exception("écriture feedback")
    return {"ok": True}


@app.post("/api/share")
def create_share(body: ShareIn, authorization: Optional[str] = Header(None)) -> dict:
    """Crée un permalien partageable pour une réponse (instantané). Ouvert aux anonymes."""
    user = _current_user(authorization)
    cites = [c for c in body.citations if isinstance(c, dict)]
    token = share_store.create(user["id"] if user else None, body.question,
                               body.answer, cites, body.status)
    return {"id": token}


@app.get("/api/share/{share_id}")
def read_share(share_id: str) -> dict:
    data = share_store.get(share_id)
    if not data:
        raise HTTPException(status_code=404, detail="Lien introuvable")
    return data


# ---------- V3 offre cabinet : espaces de travail, membres, dossiers partagés ----------
def _require_ws_role(workspace_id: int, user: dict, roles: tuple) -> str:
    role = ws.membership(workspace_id, user["id"])
    if role is None:
        raise HTTPException(status_code=404, detail="Espace introuvable")
    if role not in roles:
        raise HTTPException(status_code=403, detail="Action réservée aux administrateurs de l'espace")
    return role


@app.post("/api/workspaces")
def create_workspace(body: WorkspaceCreate, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return ws.create_workspace(user["id"], body.name)


@app.get("/api/workspaces")
def list_workspaces(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": ws.list_workspaces(user["id"])}


@app.get("/api/workspaces/{wid}/members")
def list_members(wid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ws.ROLES)  # tout membre peut voir
    return {"items": ws.list_members(wid)}


@app.post("/api/workspaces/{wid}/members")
def add_member(wid: int, body: MemberAdd, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ("owner", "admin"))
    try:
        return ws.add_member(wid, body.email, body.role)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/workspaces/{wid}/members/{uid}")
def remove_member(wid: int, uid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ("owner", "admin"))
    if not ws.remove_member(wid, uid):
        raise HTTPException(status_code=400, detail="Membre introuvable (ou propriétaire, non retirable)")
    return {"ok": True}


@app.get("/api/workspaces/{wid}/dossiers")
def list_dossiers(wid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    role = _require_ws_role(wid, user, ws.ROLES)
    return {"items": ws.list_dossiers(wid, user["id"], role)}


@app.post("/api/workspaces/{wid}/dossiers")
def create_dossier(wid: int, body: DossierCreate, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ws.ROLES)
    return ws.create_dossier(wid, body.name, user["id"])


def _dossier_member(did: int, user: dict) -> str:
    wid = ws.dossier_workspace(did)
    if wid is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    role = _require_ws_role(wid, user, ws.ROLES)
    # Cloison déontologique : un dossier restreint reste invisible aux non-autorisés (404).
    if not ws.can_access_dossier(did, user["id"], role):
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    return role


@app.get("/api/dossiers/{did}/items")
def list_dossier_items(did: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _dossier_member(did, user)
    return {"items": ws.list_items(did)}


@app.post("/api/dossiers/{did}/items")
def add_dossier_item(did: int, body: DossierItemAdd,
                     authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _dossier_member(did, user)
    cites = [c for c in body.citations if isinstance(c, dict)]
    return ws.add_item(did, body.question, body.answer, cites, body.status, user["id"])


def _dossier_gestion(did: int, user: dict) -> int:
    """Vérifie que l'utilisateur est owner/admin de l'espace du dossier ; renvoie wid."""
    wid = ws.dossier_workspace(did)
    if wid is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    _require_ws_role(wid, user, ("owner", "admin"))
    return wid


class RestrictIn(BaseModel):
    restricted: bool


@app.post("/api/dossiers/{did}/restrict")
def restrict_dossier(did: int, body: RestrictIn, authorization: Optional[str] = Header(None)) -> dict:
    """Cloison déontologique : rend un dossier restreint (visible seulement des autorisés)."""
    user = _require_user(authorization)
    _dossier_gestion(did, user)
    ws.set_restricted(did, body.restricted)
    audit.log("dossier.restrict", user, f"dossier={did} restricted={body.restricted}")
    return {"ok": True, "restricted": body.restricted}


class AccessIn(BaseModel):
    email: str = Field(min_length=3)


@app.post("/api/dossiers/{did}/access")
def grant_dossier_access(did: int, body: AccessIn, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    wid = _dossier_gestion(did, user)
    cible = ws.membership_by_email(wid, body.email)
    if not cible:
        raise HTTPException(status_code=400, detail="Cet utilisateur n'est pas membre de l'espace")
    ws.grant_access(did, cible)
    audit.log("dossier.grant", user, f"dossier={did} user={cible}")
    return {"ok": True, "user_id": cible}


@app.delete("/api/dossiers/{did}/access/{uid}")
def revoke_dossier_access(did: int, uid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _dossier_gestion(did, user)
    if not ws.revoke_access(did, uid):
        raise HTTPException(status_code=404, detail="Accès introuvable")
    audit.log("dossier.revoke", user, f"dossier={did} user={uid}")
    return {"ok": True}


class RoleUpdate(BaseModel):
    role: str


@app.post("/api/workspaces/{wid}/members/{uid}/role")
def set_member_role(wid: int, uid: int, body: RoleUpdate,
                    authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ("owner", "admin"))
    if user["id"] == uid:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas changer votre propre rôle")
    try:
        if not ws.set_member_role(wid, uid, body.role):
            raise HTTPException(status_code=404, detail="Membre introuvable (ou propriétaire)")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.delete("/api/workspaces/{wid}")
def delete_workspace(wid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ("owner",))  # propriétaire uniquement
    ws.delete_workspace(wid)
    return {"ok": True}


@app.post("/api/workspaces/{wid}/leave")
def leave_workspace(wid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    role = _require_ws_role(wid, user, ws.ROLES)
    if role == "owner":
        raise HTTPException(status_code=400,
                            detail="Le propriétaire ne peut pas quitter le cabinet ; supprimez-le à la place")
    ws.remove_member(wid, user["id"])
    return {"ok": True}


@app.delete("/api/dossiers/{did}")
def delete_dossier(did: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    wid = ws.dossier_workspace(did)
    if wid is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    _require_ws_role(wid, user, ("owner", "admin"))
    ws.delete_dossier(did)
    return {"ok": True}


# ---------- V3 : alertes « nouvelle jurisprudence sur mes sujets » (veille in-app) ----------
class AlertCreate(BaseModel):
    query: str = Field(min_length=2)
    source_type: Optional[str] = None


@app.post("/api/alerts")
def create_alert(body: AlertCreate, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    al = alert_store.create_alert(user["id"], body.query, body.source_type)
    try:
        al["unseen"] = alert_runner.check(al)  # 1er check : remonte les décisions actuelles du sujet
    except Exception:
        log.exception("check alerte à la création")
    return al


@app.get("/api/alerts")
def list_alerts(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": alert_store.list_alerts(user["id"])}


@app.post("/api/alerts/check-all")
def check_all_alerts(authorization: Optional[str] = Header(None)) -> dict:
    """Rafraîchit toutes les alertes de l'utilisateur (bouton « Vérifier toutes »)."""
    user = _require_user(authorization)
    new = 0
    for al in alert_store.list_alerts(user["id"]):
        try:
            new += alert_runner.check(al)
        except Exception:
            log.exception("check alerte")
    return {"new": new}


@app.post("/api/alerts/{aid}/check")
def check_alert(aid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    al = alert_store.get_alert(aid, user["id"])
    if not al:
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return {"new": alert_runner.check(al)}


@app.get("/api/alerts/{aid}/hits")
def alert_hits(aid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    if not alert_store.get_alert(aid, user["id"]):
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    hits = alert_store.list_hits(aid)
    alert_store.mark_seen(aid)  # ouvrir l'alerte = marquer lu
    return {"items": hits}


@app.delete("/api/alerts/{aid}")
def delete_alert(aid: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    if not alert_store.delete_alert(aid, user["id"]):
        raise HTTPException(status_code=404, detail="Alerte introuvable")
    return {"ok": True}


@app.get("/api/me")
def me(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"user": {"email": user["email"], "plan": user.get("plan", "student"),
                     "is_admin": _is_admin(user)},
            "quota": auth.quota_info(user)}


@app.get("/api/history")
def history(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": auth.list_history(user["id"])}


# ---------- Backoffice admin ----------
class PlanUpdate(BaseModel):
    plan: str


class AdminUpdate(BaseModel):
    is_admin: bool


@app.get("/api/admin/overview")
def admin_overview(authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {
        "metrics": metrics.snapshot(),
        "corpus": search.corpus_overview(),
        "index": search.index_stats(),
        "health": {"meilisearch": search.meili_healthy(),
                   "llm_configured": bool(settings.anthropic_api_key)},
        "users": admin.user_stats(),
        "questions": admin.question_stats(),
        "feedback": fb_store.stats(),
        "prompt_version": settings.prompt_version,
        "model": settings.anthropic_model,
        "hybrid_semantic_ratio": settings.hybrid_semantic_ratio,
    }


@app.get("/api/admin/feedback")
def admin_feedback(authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": fb_store.recent(200), "stats": fb_store.stats()}


@app.get("/api/admin/activity")
def admin_activity(days: int = 14,
                   authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"per_day": admin.questions_per_day(min(max(days, 1), 60))}


# Banc de test : 10 questions de référence couvrant les domaines principaux. Le gate
# qualité du one-pager, version récupération (rapide, sans LLM) — valide aussi le retrieval.
REFERENCE_QUESTIONS = [
    "Dans quels cas un licenciement avec effet immédiat est-il justifié ?",
    "Quel est le préavis légal en cas de licenciement au Luxembourg ?",
    "Une absence injustifiée peut-elle constituer une faute grave ?",
    "Un employeur peut-il imposer des heures supplémentaires ?",
    "Quelles sont les conditions de résiliation d'un bail d'habitation ?",
    "Le bailleur peut-il conserver la garantie locative après le départ du locataire ?",
    "Quelle valeur probante les tribunaux reconnaissent-ils aux échanges d'emails ?",
    "Quel est le délai pour faire appel d'un jugement civil ?",
    "Comment est fixée la pension alimentaire pour un enfant au Luxembourg ?",
    "Quelles sont les conséquences d'une rupture de la période d'essai ?",
]


@app.get("/api/admin/eval")
def admin_eval(authorization: Optional[str] = Header(None)) -> dict:
    """Banc de test récupération : pour chaque question de référence, ce que la recherche
    remonte (droit / jurisprudence). Rapide (pas de LLM). Sert de gate qualité et valide
    l'indexation."""
    _require_admin(authorization)
    results = []
    for q in REFERENCE_QUESTIONS:
        hits = search.search(q, 12, SearchFilters())
        laws = [h.doc_id for h in hits if h.source_type == "law"]
        juris = [h for h in hits if h.source_type == "jurisprudence"]
        results.append({"question": q, "count": len(hits),
                        "has_law": bool(laws), "has_juris": bool(juris), "laws": laws[:3]})
    return {"total": len(results),
            "with_law": sum(1 for r in results if r["has_law"]),
            "with_juris": sum(1 for r in results if r["has_juris"]),
            "results": results}


class ProbeRequest(BaseModel):
    q: str = Field(min_length=1)
    topK: int = Field(default=12, ge=1, le=40)
    filters: SearchFilters = Field(default_factory=SearchFilters)


@app.post("/api/admin/probe")
def admin_probe(body: ProbeRequest,
                authorization: Optional[str] = Header(None)) -> dict:
    """Inspecteur de récupération : montre CE QUE la recherche remonte pour une requête
    (sans LLM, sans logging, sans quota). Sert à diagnostiquer/valider le retrieval."""
    _require_admin(authorization)
    hits = search.search(body.q, body.topK, body.filters)
    return {"count": len(hits), "hits": [
        {"chunk_id": h.chunk_id, "doc_id": h.doc_id, "source_type": h.source_type,
         "title": h.title, "year": h.year, "juridiction_key": h.juridiction_key,
         "snippet": (h.text or "")[:240]} for h in hits]}


@app.get("/api/admin/users")
def admin_list_users(authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": admin.list_users()}


@app.post("/api/admin/users/{user_id}/plan")
def admin_set_plan(user_id: int, body: PlanUpdate,
                   authorization: Optional[str] = Header(None)) -> dict:
    me = _require_admin(authorization)
    if body.plan not in ("student", "pro"):
        raise HTTPException(status_code=400, detail="plan invalide (student|pro)")
    if not admin.set_user_plan(user_id, body.plan):
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    audit.log("admin.set_plan", me, f"user={user_id} plan={body.plan}")
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/admin")
def admin_set_admin(user_id: int, body: AdminUpdate,
                    authorization: Optional[str] = Header(None)) -> dict:
    me_admin = _require_admin(authorization)
    if user_id == me_admin["id"] and not body.is_admin:
        raise HTTPException(status_code=400,
                            detail="Vous ne pouvez pas retirer vos propres droits admin")
    if not admin.set_user_admin(user_id, body.is_admin):
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    audit.log("admin.set_admin", me_admin, f"user={user_id} is_admin={body.is_admin}")
    return {"ok": True}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int,
                      authorization: Optional[str] = Header(None)) -> dict:
    me_admin = _require_admin(authorization)
    if user_id == me_admin["id"]:
        raise HTTPException(status_code=400,
                            detail="Vous ne pouvez pas supprimer votre propre compte")
    if not admin.delete_user(user_id):
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
    audit.log("admin.delete_user", me_admin, f"user={user_id}")
    return {"ok": True}


@app.get("/api/admin/questions")
def admin_questions(limit: int = 100,
                    authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": admin.recent_questions(min(max(limit, 1), 500))}


@app.get("/api/admin/llm")
def admin_llm(authorization: Optional[str] = Header(None)) -> dict:
    """Routage du modèle par sensibilité (public vs confidentiel) — visibilité souveraineté :
    quel fournisseur (Claude / Mistral UE / local) répond à quel type de question."""
    _require_admin(authorization)
    return llm.info()


@app.get("/api/admin/health")
def admin_health(authorization: Optional[str] = Header(None)) -> dict:
    """Observabilité détaillée (IT du cabinet) : dépendances + volumétrie + routage LLM."""
    _require_admin(authorization)
    counts = {}
    try:
        with db.get_conn() as conn:
            for t in ("users", "vault_documents", "audit_log", "api_keys"):
                counts[t] = conn.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
    except Exception:
        log.exception("comptage santé")
    return {
        "meilisearch": search.meili_healthy(),
        "llm_configured": bool(settings.anthropic_api_key),
        "llm_routing": llm.info(),
        "index": search.index_stats(),
        "counts": counts,
        "metrics": metrics.snapshot(),
    }


class ConfigPatch(BaseModel):
    values: dict


@app.get("/api/admin/config")
def admin_get_config(authorization: Optional[str] = Header(None)) -> dict:
    """Réglages runtime non-secrets (modifiables sans redéploiement)."""
    _require_admin(authorization)
    return {"config": config_store.get_all(), "modifiables": list(config_store.CLES_AUTORISEES)}


@app.patch("/api/admin/config")
def admin_patch_config(body: ConfigPatch, authorization: Optional[str] = Header(None)) -> dict:
    """Applique et persiste des réglages runtime (clés hors liste blanche ignorées)."""
    me = _require_admin(authorization)
    appliques = config_store.set_many(body.values)
    audit.log("admin.config", me, ", ".join(appliques))
    return {"applied": appliques}


# ---------- Socle entreprise : audit, rétention/purge, export RGPD, clés d'API, prompts ----------
@app.get("/api/admin/audit")
def admin_audit(limit: int = 200, action: Optional[str] = None,
                authorization: Optional[str] = Header(None)) -> dict:
    """Journal d'audit (souverain, local) : qui/quoi/quand. Réservé aux admins."""
    _require_admin(authorization)
    return {"items": audit.recent(limit, action)}


class PurgeRequest(BaseModel):
    days: int = Field(ge=1, le=3650)


@app.post("/api/admin/purge")
def admin_purge(body: PurgeRequest, authorization: Optional[str] = Header(None)) -> dict:
    """Rétention : purge des données (historique/feedback/partages/audit) au-delà de N jours."""
    me = _require_admin(authorization)
    res = rgpd.purge(body.days)
    audit.log("admin.purge", me, f"days={body.days}")
    return res


@app.get("/api/me/export")
def me_export(authorization: Optional[str] = Header(None)) -> dict:
    """Portabilité RGPD : toutes les données de l'utilisateur, en clair."""
    user = _require_user(authorization)
    audit.log("rgpd.export", user)
    return rgpd.export_user(user["id"])


class ApiKeyCreate(BaseModel):
    name: str = Field(default="clé", max_length=80)


@app.post("/api/keys")
def create_key(body: ApiKeyCreate, authorization: Optional[str] = Header(None)) -> dict:
    """Crée une clé d'API de service (valeur montrée UNE seule fois)."""
    user = _require_user(authorization)
    k = apikeys.create(user["id"], body.name)
    audit.log("apikey.create", user, k["prefix"])
    return k


@app.get("/api/keys")
def list_keys(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": apikeys.list_keys(user["id"])}


@app.delete("/api/keys/{key_id}")
def revoke_key(key_id: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    ok = apikeys.revoke(key_id, user["id"])
    if ok:
        audit.log("apikey.revoke", user, str(key_id))
    return _ok_ou_404(ok, "Clé introuvable")


class PromptIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    workspace_id: Optional[int] = None


@app.post("/api/prompts")
def create_prompt(body: PromptIn, authorization: Optional[str] = Header(None)) -> dict:
    """Bibliothèque de prompts : perso (workspace_id absent) ou partagé au cabinet."""
    user = _require_user(authorization)
    if body.workspace_id is not None:
        _require_ws_role(body.workspace_id, user, ws.ROLES)  # doit être membre de l'espace
    return prompts_store.create(user["id"], body.title, body.body, body.workspace_id)


@app.get("/api/prompts")
def list_prompts(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": prompts_store.visibles(user["id"], _mes_espaces(user))}


@app.delete("/api/prompts/{prompt_id}")
def delete_prompt(prompt_id: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return _ok_ou_404(prompts_store.delete(prompt_id, user["id"]), "Prompt introuvable")


class PlaybookRule(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1)


class PlaybookIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rules: list[PlaybookRule] = Field(min_length=1)
    workspace_id: Optional[int] = None


def _mes_espaces(user: dict) -> list[int]:
    return [w["id"] for w in ws.list_workspaces(user["id"])]


def _ok_ou_404(supprime: bool, message: str) -> dict:
    """Réponse {ok:true} si l'opration a affecté une ligne, sinon 404 (uniformise les DELETE)."""
    if not supprime:
        raise HTTPException(status_code=404, detail=message)
    return {"ok": True}


def _rag_ou_503(label: str, fn, *args, **kwargs):
    """Exécute une génération LLM ; sur panne, loggue et renvoie un 503 uniforme (le contrat
    veut un refus gracieux côté /api/ask ; ces tâches déclenchées explicitement échouent en 503)."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        log.exception(label)
        raise HTTPException(status_code=503,
                            detail="La génération a échoué. Réessayez dans un instant.")


@app.post("/api/playbooks")
def create_playbook(body: PlaybookIn, authorization: Optional[str] = Header(None)) -> dict:
    """Playbook de revue de contrats : règles maison (perso ou partagées au cabinet)."""
    user = _require_user(authorization)
    if body.workspace_id is not None:
        _require_ws_role(body.workspace_id, user, ws.ROLES)
    rules = [r.model_dump() for r in body.rules]
    return pb_store.create(user["id"], body.name, rules, body.workspace_id)


@app.get("/api/playbooks")
def list_playbooks(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": pb_store.visibles(user["id"], _mes_espaces(user))}


@app.delete("/api/playbooks/{playbook_id}")
def delete_playbook(playbook_id: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return _ok_ou_404(pb_store.delete(playbook_id, user["id"]), "Playbook introuvable")


class ContractReview(BaseModel):
    playbook_id: int


@app.post("/api/vault/documents/{doc_id}/review-contract")
def vault_review_contract(doc_id: int, body: ContractReview,
                          authorization: Optional[str] = Header(None)) -> dict:
    """Revue d'un contrat déposé contre un playbook : verdict par règle (ok/issue/missing),
    ancré au texte du document. LLM routé « confidentiel »."""
    user = _require_user(authorization)
    doc = vault.get_document(doc_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")
    pb = pb_store.get(body.playbook_id, user["id"], _mes_espaces(user))
    if not pb:
        raise HTTPException(status_code=404, detail="Playbook introuvable")
    res = _rag_ou_503("revue de contrat", rag.revue_contrat, doc.get("text") or "", pb["rules"])
    findings = res["findings"]
    return {"task": "contract", "playbook": pb["name"], "findings": findings,
            "summary": {"total": len(findings),
                        "ok": sum(1 for f in findings if f["status"] == "ok"),
                        "issue": sum(1 for f in findings if f["status"] == "issue"),
                        "missing": sum(1 for f in findings if f["status"] == "missing")}}


class DraftRequest(BaseModel):
    instruction: str = Field(min_length=1)
    topK: int = Field(default=12, ge=1, le=50)
    filters: SearchFilters = Field(default_factory=SearchFilters)


@app.post("/api/draft")
def draft(body: DraftRequest, authorization: Optional[str] = Header(None)) -> dict:
    """Rédaction assistée sourcée (courrier/conclusions/note) fondée sur le corpus officiel."""
    user = _require_user(authorization)
    try:
        hits = search.search(body.instruction, body.topK, body.filters)
    except Exception:
        log.exception("recherche (draft)")
        hits = []
    res = _rag_ou_503("rédaction", rag.rediger, body.instruction, hits)
    audit.log("draft", user)
    return {"answer": res["answer"], "refused": res["refused"],
            "citations": [c.model_dump() for c in res["citations"]]}


def _quota_message(user: Optional[dict]) -> Optional[str]:
    """Message de refus si le quota mensuel (plan étudiant) est épuisé, sinon None."""
    if not user:
        return None
    qi = auth.quota_info(user)
    if qi["remaining"] is not None and qi["remaining"] <= 0:
        return (f"Quota mensuel atteint ({qi['limit']} questions/mois, plan étudiant). "
                "Il se réinitialise le 1er du mois — ou passez au plan pro.")
    return None


def _save_history(user: Optional[dict], question: str, answer, status) -> None:
    """Enregistre la question dans l'historique si connecté (best-effort, ne lève jamais)."""
    if not user:
        return
    try:
        auth.add_history(user["id"], question, answer, status)
    except Exception:
        log.exception("écriture historique")


def _contextual_query(req: AskRequest) -> str:
    """Requête de recherche enrichie du dernier tour utilisateur (pour les questions de suivi)."""
    if req.history:
        prev = [t.content for t in req.history if t.role == "user" and t.content]
        if prev:
            return (prev[-1] + " " + req.q)[:500]
    return req.q


@app.post("/api/ask", response_model=AskResponse, response_model_exclude_none=False)
def ask(req: AskRequest, request: Request,
        authorization: Optional[str] = Header(None),
        x_api_key: Optional[str] = Header(None)) -> AskResponse:
    t0 = time.time()
    metrics.mark_ask()
    metrics.incr("ask_total")

    if not _rate_ok(_client_ip(request)):
        metrics.incr("ask_rate_limited")
        metrics.incr("ask_refused")
        metrics.record_latency_ms((time.time() - t0) * 1000)
        return rag.refusal(
            "Trop de requêtes en peu de temps. Patientez un instant avant de réessayer."
        )

    user = _current_user(authorization, x_api_key)  # session OU clé d'API de service

    # Quota mensuel du plan étudiant (freemium)
    qmsg = _quota_message(user)
    if qmsg:
        metrics.incr("ask_refused")
        metrics.record_latency_ms((time.time() - t0) * 1000)
        return rag.refusal(qmsg)

    # Recherche nominative d'avocat (Insight) : court-circuite le RAG si un avocat nommé correspond.
    try:
        look = insight.lawyer_lookup(req.q)
    except Exception:
        log.exception("insight lawyer_lookup")
        look = None
    if look:
        resp = AskResponse(answer=look["answer"], citations=look["citations"], refused=False,
                           status="ok", prompt_version=settings.prompt_version)
        metrics.record_latency_ms((time.time() - t0) * 1000)
        _save_history(user, req.q, resp.answer, resp.status)
        return resp

    try:
        t_s = time.time()
        hits = search.search(_contextual_query(req), req.topK, req.filters)
        metrics.record_search_ms((time.time() - t_s) * 1000)
    except Exception:
        log.exception("Meilisearch indisponible")
        metrics.incr("ask_errors")
        resp = rag.refusal("Le moteur de recherche est momentanément indisponible. Réessayez dans un instant.")
    else:
        try:
            t_l = time.time()
            resp = rag.answer(req.q, hits, req.temperature, pedagogical=req.pedagogical, history=req.history)
            metrics.record_llm_ms((time.time() - t_l) * 1000)
        except Exception:
            log.exception("Erreur LLM")
            metrics.incr("ask_errors")
            resp = rag.refusal("La génération de réponse a échoué. Réessayez dans un instant.")

    if getattr(resp, "refused", False):
        metrics.incr("ask_refused")
    metrics.record_latency_ms((time.time() - t0) * 1000)

    # Historique si connecté (best effort, ne casse jamais la réponse)
    _save_history(user, req.q, getattr(resp, "answer", None), getattr(resp, "status", None))
    return resp


# ---------- Insight : profiling des AVOCATS (déploiement client/interne — accessible par défaut) ----------
@app.get("/api/insight/stats")
def insight_stats() -> dict:
    return insight.stats()


@app.get("/api/insight/matters")
def insight_matters() -> dict:
    return {"items": insight.matters()}


@app.get("/api/insight/lawyers")
def insight_lawyers(q: Optional[str] = None, limit: int = 50, sort: str = "cases",
                    matter: Optional[str] = None) -> dict:
    return {"items": insight.list_lawyers(q, limit, sort=sort, matter=matter)}


@app.get("/api/insight/analytics")
def insight_analytics(matter: Optional[str] = None, juridiction: Optional[str] = None) -> dict:
    """Analytics contentieux (public) : volumes + taux de succès estimé par matière /
    juridiction / année. Avocats/parties uniquement (jamais de magistrats)."""
    return insight.analytics(matter, juridiction)


@app.get("/api/insight/lawyers/{key}")
def insight_lawyer(key: str) -> dict:
    prof = insight.get_lawyer(key)
    if not prof:
        raise HTTPException(status_code=404, detail="Avocat introuvable")
    return prof


# ---------- Vault : documents privés de l'utilisateur (dépôt + Q&A + citations) ----------
_VAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 Mo


@app.post("/api/vault/documents")
async def vault_upload(request: Request, filename: str = "document",
                       authorization: Optional[str] = Header(None)) -> dict:
    """Dépôt d'un document (corps brut ; nom via ?filename=). PDF/texte."""
    user = _require_user(authorization)
    # Rejet AVANT de charger le corps en mémoire (garde-fou anti-OOM sur l'en-tête).
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > _VAULT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 25 Mo)")
    data = await request.body()
    if len(data) > _VAULT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux (max 25 Mo)")
    text = vault.extract_text(filename, data)
    doc = vault.create_document(user["id"], filename, request.headers.get("content-type"), text)
    try:
        n = vault.index_chunks(user["id"], doc["id"], filename, text)
        vault.set_status(doc["id"], "ready", n)
        doc["status"], doc["n_chunks"] = "ready", n
    except Exception:
        log.exception("indexation Vault")
        vault.set_status(doc["id"], "error")
        doc["status"] = "error"
    audit.log("vault.upload", user, f"doc={doc['id']} {filename}")
    return doc


@app.get("/api/vault/documents")
def vault_list(authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    return {"items": vault.list_documents(user["id"])}


@app.delete("/api/vault/documents/{doc_id}")
def vault_delete(doc_id: int, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    if not vault.get_document(doc_id, user["id"]):
        raise HTTPException(status_code=404, detail="Document introuvable")
    try:
        vault.delete_chunks(user["id"], doc_id)
    except Exception:
        log.exception("suppression chunks Vault")
    vault.delete_document(doc_id, user["id"])
    audit.log("vault.delete", user, f"doc={doc_id}")
    return {"ok": True}


class VaultAsk(BaseModel):
    q: str = Field(min_length=1)
    doc_ids: Optional[list[int]] = None
    topK: int = Field(default=12, ge=1, le=50)
    # RAG hybride : interroge AUSSI le corpus public (jurisprudence + Legilux) en plus des
    # documents privés. La réponse cite les deux — les citations du Vault n'ont pas de
    # source_type (« votre document »), celles du corpus le portent (source publique).
    include_corpus: bool = False


def _interleave_hits(private: list, public: list) -> list:
    """Entrelace documents privés (prioritaires) et corpus public, en dédupliquant par
    doc_id. Les hits du Vault passent d'abord : la question porte sur les documents de
    l'utilisateur, la jurisprudence vient les éclairer."""
    out, seen = [], set()
    for h in [x for pair in zip(private, public) for x in pair] + private + public:
        if h.doc_id in seen:
            continue
        seen.add(h.doc_id)
        out.append(h)
    return out


@app.post("/api/vault/ask", response_model=AskResponse, response_model_exclude_none=False)
def vault_ask(body: VaultAsk, authorization: Optional[str] = Header(None)) -> AskResponse:
    """Q&A sourcé sur les documents du Vault de l'utilisateur (isolé). Si `include_corpus`,
    croise en une requête les documents privés ET le corpus public officiel (différenciateur
    Jurilux : aucun Vault généraliste n'a la source de vérité du droit luxembourgeois)."""
    user = _require_user(authorization)
    try:
        hits = vault.search_vault(user["id"], body.q, body.doc_ids, body.topK)
    except Exception:
        log.exception("recherche Vault")
        return rag.refusal("Le Vault est momentanément indisponible. Réessayez dans un instant.")
    if body.include_corpus:
        try:
            public = search.search(body.q, body.topK, SearchFilters())
        except Exception:
            log.exception("recherche corpus (Vault hybride)")
            public = []
        hits = _interleave_hits(hits, public)
    if not hits:
        return rag.refusal("Aucun passage pertinent dans vos documents pour cette question.")
    try:
        # Vault = données privées du cabinet → routage LLM « confidentiel » (Mistral UE / local).
        return rag.answer(body.q, hits, 0.0, sensibilite="confidentiel")
    except Exception:
        log.exception("Erreur LLM (Vault)")
        return rag.refusal("La génération de réponse a échoué. Réessayez dans un instant.")


class VaultReview(BaseModel):
    doc_ids: list[int] = Field(min_length=1)


@app.post("/api/vault/review")
def vault_review(body: VaultReview, authorization: Optional[str] = Header(None)) -> dict:
    """Revue tabulaire : 1 document = 1 ligne, colonnes extraites (matière, issue, montants,
    avocats, références) — extraction locale/déterministe via `insight`. Concurrent : Legora."""
    user = _require_user(authorization)
    rows = [{"doc_id": doc["id"], "filename": doc["filename"],
             **vault.extract_structure(doc.get("text") or "")}
            for doc in vault.get_documents(body.doc_ids, user["id"])]
    return {"columns": ["matter", "outcome", "amounts", "lawyers", "references"], "rows": rows}


class VaultAnalyze(BaseModel):
    task: str = Field(pattern="^(citations|extract|summary|counter|timeline)$")


@app.post("/api/vault/documents/{doc_id}/analyze")
def vault_analyze(doc_id: int, body: VaultAnalyze,
                  authorization: Optional[str] = Header(None)) -> dict:
    """Analyse d'un document déposé :
    - `task=citations` : extrait les références et les VÉRIFIE contre le corpus officiel
      (local/déterministe, aucun LLM) — anti-hallucination.
    - `task=extract` : extraction structurée (avocats/côté, matière, issue, montants) via
      le pipeline insight (local/déterministe).
    - `task=summary` : résumé fidèle du document (LLM, routage « confidentiel »).
    - `task=counter` : contre-argumentaire ancré à la jurisprudence LU réelle, citations
      vérifiables (LLM + corpus)."""
    user = _require_user(authorization)
    doc = vault.get_document(doc_id, user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")
    text = doc.get("text") or ""
    if body.task == "extract":
        return {"task": "extract", **vault.extract_structure(text)}
    if body.task == "timeline":
        return {"task": "timeline", "events": vault.extract_timeline(text)}
    if body.task == "summary":
        return {"task": "summary", "summary": _rag_ou_503("résumé Vault", rag.resume, text)}
    if body.task == "counter":
        try:
            hits = search.search(text[:1000], 12, SearchFilters())
        except Exception:
            log.exception("recherche corpus (contre-argumentaire)")
            hits = []
        res = _rag_ou_503("contre-argumentaire Vault", rag.contre_argumentaire, text, hits)
        return {"task": "counter", "answer": res["answer"], "refused": res["refused"],
                "citations": [c.model_dump() for c in res["citations"]]}
    checked = vault.verify_references(vault.extract_references(text))
    return {"task": "citations", "references": checked,
            "verified": sum(1 for r in checked if r["verified"]), "total": len(checked)}


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _sse_refusal(why: str):
    yield _sse({"type": "delta", "text": why})
    yield _sse({"type": "meta", "answer": None, "citations": [], "refused": True,
                "status": "ok", "suggested_question": None, "feedback": {"why": why},
                "prompt_version": settings.prompt_version})


@app.post("/api/ask/stream")
def ask_stream(req: AskRequest, request: Request,
               authorization: Optional[str] = Header(None)) -> StreamingResponse:
    """Version STREAMÉE de /api/ask (SSE) : la réponse s'affiche au fil de la génération
    (~1-2 s au premier token au lieu de ~14 s). Événements : {type:delta,text} puis {type:meta}."""
    t0 = time.time()
    metrics.mark_ask()
    metrics.incr("ask_total")
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

    if not _rate_ok(_client_ip(request)):
        metrics.incr("ask_rate_limited")
        metrics.incr("ask_refused")
        return StreamingResponse(_sse_refusal(
            "Trop de requêtes en peu de temps. Patientez un instant avant de réessayer."),
            media_type="text/event-stream", headers=headers)

    user = _current_user(authorization)
    qmsg = _quota_message(user)
    if qmsg:
        metrics.incr("ask_refused")
        return StreamingResponse(_sse_refusal(qmsg),
                                 media_type="text/event-stream", headers=headers)

    def gen():
        # Recherche nominative d'avocat (Insight) : réponse directe si un avocat nommé correspond.
        try:
            look = insight.lawyer_lookup(req.q)
        except Exception:
            log.exception("insight lawyer_lookup")
            look = None
        if look:
            yield _sse({"type": "delta", "text": look["answer"]})
            yield _sse({"type": "meta", "answer": look["answer"],
                        "citations": [c.model_dump() for c in look["citations"]],
                        "refused": False, "status": "ok", "suggested_question": None,
                        "feedback": None, "prompt_version": settings.prompt_version})
            metrics.record_latency_ms((time.time() - t0) * 1000)
            _save_history(user, req.q, look["answer"], "ok")
            return

        try:
            t_s = time.time()
            hits = search.search(_contextual_query(req), req.topK, req.filters)
            metrics.record_search_ms((time.time() - t_s) * 1000)
        except Exception:
            log.exception("Meilisearch indisponible")
            metrics.incr("ask_errors")
            yield from _sse_refusal("Le moteur de recherche est momentanément indisponible. Réessayez.")
            return

        final = None
        t_l = time.time()
        try:
            for ev in rag.answer_stream(req.q, hits, req.temperature, pedagogical=req.pedagogical, history=req.history):
                if ev.get("type") == "meta":
                    final = ev
                yield _sse(ev)
        except Exception:
            log.exception("Erreur LLM (stream)")
            metrics.incr("ask_errors")
            yield from _sse_refusal("La génération de réponse a échoué. Réessayez dans un instant.")
            return

        metrics.record_llm_ms((time.time() - t_l) * 1000)
        metrics.record_latency_ms((time.time() - t0) * 1000)
        if final and final.get("refused"):
            metrics.incr("ask_refused")
        if final:
            _save_history(user, req.q, final.get("answer"), final.get("status"))

    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)

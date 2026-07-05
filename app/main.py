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
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import (admin, alert as alert_store, alert_runner, auth, db, feedback as fb_store,
               insight, metrics, rag, search, share as share_store, workspace as ws)
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


def _current_user(authorization: Optional[str]) -> Optional[dict]:
    return auth.user_for_token(auth.token_from_header(authorization))


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
    return {"token": auth.create_session(user["id"]), "user": {"email": user["email"]}}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)) -> dict:
    auth.delete_session(auth.token_from_header(authorization))
    return {"ok": True}


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
    _require_ws_role(wid, user, ws.ROLES)
    return {"items": ws.list_dossiers(wid)}


@app.post("/api/workspaces/{wid}/dossiers")
def create_dossier(wid: int, body: DossierCreate, authorization: Optional[str] = Header(None)) -> dict:
    user = _require_user(authorization)
    _require_ws_role(wid, user, ws.ROLES)
    return ws.create_dossier(wid, body.name, user["id"])


def _dossier_member(did: int, user: dict) -> None:
    wid = ws.dossier_workspace(did)
    if wid is None:
        raise HTTPException(status_code=404, detail="Dossier introuvable")
    _require_ws_role(wid, user, ws.ROLES)


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
    _require_admin(authorization)
    if body.plan not in ("student", "pro"):
        raise HTTPException(status_code=400, detail="plan invalide (student|pro)")
    if not admin.set_user_plan(user_id, body.plan):
        raise HTTPException(status_code=404, detail="utilisateur introuvable")
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
    return {"ok": True}


@app.get("/api/admin/questions")
def admin_questions(limit: int = 100,
                    authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": admin.recent_questions(min(max(limit, 1), 500))}


@app.post("/api/ask", response_model=AskResponse, response_model_exclude_none=False)
def ask(req: AskRequest, request: Request,
        authorization: Optional[str] = Header(None)) -> AskResponse:
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

    user = _current_user(authorization)

    # Quota mensuel du plan étudiant (freemium)
    if user:
        qi = auth.quota_info(user)
        if qi["remaining"] is not None and qi["remaining"] <= 0:
            metrics.incr("ask_refused")
            metrics.record_latency_ms((time.time() - t0) * 1000)
            return rag.refusal(
                f"Quota mensuel atteint ({qi['limit']} questions/mois, plan étudiant). "
                "Il se réinitialise le 1er du mois — ou passez au plan pro."
            )

    try:
        t_s = time.time()
        hits = search.search(req.q, req.topK, req.filters)
        metrics.record_search_ms((time.time() - t_s) * 1000)
    except Exception:
        log.exception("Meilisearch indisponible")
        metrics.incr("ask_errors")
        resp = rag.refusal("Le moteur de recherche est momentanément indisponible. Réessayez dans un instant.")
    else:
        try:
            t_l = time.time()
            resp = rag.answer(req.q, hits, req.temperature, pedagogical=req.pedagogical)
            metrics.record_llm_ms((time.time() - t_l) * 1000)
        except Exception:
            log.exception("Erreur LLM")
            metrics.incr("ask_errors")
            resp = rag.refusal("La génération de réponse a échoué. Réessayez dans un instant.")

    if getattr(resp, "refused", False):
        metrics.incr("ask_refused")
    metrics.record_latency_ms((time.time() - t0) * 1000)

    # Historique si connecté (best effort, ne casse jamais la réponse)
    if user:
        try:
            auth.add_history(user["id"], req.q, getattr(resp, "answer", None),
                             getattr(resp, "status", None))
        except Exception:
            log.exception("écriture historique")
    return resp


# ---------- Insight : profiling des AVOCATS (données publiques, usage interne, gate admin) ----------
@app.get("/api/insight/stats")
def insight_stats(authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return insight.stats()


@app.get("/api/insight/matters")
def insight_matters(authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": insight.matters()}


@app.get("/api/insight/lawyers")
def insight_lawyers(q: Optional[str] = None, limit: int = 50, sort: str = "cases",
                    matter: Optional[str] = None, authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    return {"items": insight.list_lawyers(q, limit, sort=sort, matter=matter)}


@app.get("/api/insight/lawyers/{key}")
def insight_lawyer(key: str, authorization: Optional[str] = Header(None)) -> dict:
    _require_admin(authorization)
    prof = insight.get_lawyer(key)
    if not prof:
        raise HTTPException(status_code=404, detail="Avocat introuvable")
    return prof


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
    if user:
        qi = auth.quota_info(user)
        if qi["remaining"] is not None and qi["remaining"] <= 0:
            metrics.incr("ask_refused")
            return StreamingResponse(_sse_refusal(
                f"Quota mensuel atteint ({qi['limit']} questions/mois, plan étudiant). "
                "Il se réinitialise le 1er du mois — ou passez au plan pro."),
                media_type="text/event-stream", headers=headers)

    def gen():
        try:
            t_s = time.time()
            hits = search.search(req.q, req.topK, req.filters)
            metrics.record_search_ms((time.time() - t_s) * 1000)
        except Exception:
            log.exception("Meilisearch indisponible")
            metrics.incr("ask_errors")
            yield from _sse_refusal("Le moteur de recherche est momentanément indisponible. Réessayez.")
            return

        final = None
        t_l = time.time()
        try:
            for ev in rag.answer_stream(req.q, hits, req.temperature, pedagogical=req.pedagogical):
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
        if user and final:
            try:
                auth.add_history(user["id"], req.q, final.get("answer"), final.get("status"))
            except Exception:
                log.exception("écriture historique")

    return StreamingResponse(gen(), media_type="text/event-stream", headers=headers)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)

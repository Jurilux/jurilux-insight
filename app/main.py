"""Jurilux API — FastAPI sur 127.0.0.1:8088 (relayé par Caddy).

Endpoints :
  GET  /health   — 200 si l'API tourne ET que Meilisearch répond, sinon 503.
                   (Le front affiche « Connecté » sur res.ok : un health strict
                   corrige le voyant vert indulgent.)
  POST /api/ask  — RAG : Meilisearch -> Claude -> AskResponse.
"""
import logging
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

from . import admin, auth, db, metrics, rag, search
from .config import settings
from .schemas import AskRequest, AskResponse

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
        "prompt_version": settings.prompt_version,
        "model": settings.anthropic_model,
        "hybrid_semantic_ratio": settings.hybrid_semantic_ratio,
    }


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
        hits = search.search(req.q, req.topK, req.filters)
    except Exception:
        log.exception("Meilisearch indisponible")
        metrics.incr("ask_errors")
        resp = rag.refusal("Le moteur de recherche est momentanément indisponible. Réessayez dans un instant.")
    else:
        try:
            resp = rag.answer(req.q, hits, req.temperature, pedagogical=req.pedagogical)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)

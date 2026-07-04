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

from fastapi import FastAPI, Request, Response

from . import rag, search
from .config import settings
from .schemas import AskRequest, AskResponse

log = logging.getLogger("jurilux")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Jurilux API", version="1.0.0")

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


@app.post("/api/ask", response_model=AskResponse, response_model_exclude_none=False)
def ask(req: AskRequest, request: Request) -> AskResponse:
    if not _rate_ok(_client_ip(request)):
        return rag.refusal(
            "Trop de requêtes en peu de temps. Patientez un instant avant de réessayer."
        )
    try:
        hits = search.search(req.q, req.topK, req.filters)
    except Exception:
        log.exception("Meilisearch indisponible")
        return rag.refusal("Le moteur de recherche est momentanément indisponible. Réessayez dans un instant.")

    try:
        return rag.answer(req.q, hits, req.temperature)
    except Exception:
        log.exception("Erreur LLM")
        return rag.refusal("La génération de réponse a échoué. Réessayez dans un instant.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port)

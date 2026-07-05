"""Métriques légères en mémoire (par process) pour l'observabilité.

Exposées via GET /api/metrics. Volatiles (remises à zéro au redémarrage) —
suffisant pour un suivi de cap ; brancher un vrai TSDB plus tard si besoin.
"""
import time
from collections import deque

_start = time.time()
_counters = {
    "ask_total": 0,
    "ask_refused": 0,
    "ask_errors": 0,
    "ask_rate_limited": 0,
}
_latencies_ms: "deque[float]" = deque(maxlen=200)
_search_ms: "deque[float]" = deque(maxlen=200)   # temps recherche (Meili + embedding requête)
_llm_ms: "deque[float]" = deque(maxlen=200)      # temps génération (Claude)
_last_ask: "float | None" = None


def incr(key: str, n: int = 1) -> None:
    _counters[key] = _counters.get(key, 0) + n


def mark_ask() -> None:
    global _last_ask
    _last_ask = time.time()


def record_latency_ms(ms: float) -> None:
    _latencies_ms.append(ms)


def record_search_ms(ms: float) -> None:
    _search_ms.append(ms)


def record_llm_ms(ms: float) -> None:
    _llm_ms.append(ms)


def _avg(dq: "deque[float]"):
    return round(sum(dq) / len(dq), 1) if dq else None


def snapshot() -> dict:
    total = _counters["ask_total"]
    return {
        "uptime_s": round(time.time() - _start),
        **_counters,
        "refusal_rate": round(_counters["ask_refused"] / total, 3) if total else None,
        "ask_latency_ms_avg": _avg(_latencies_ms),
        "search_ms_avg": _avg(_search_ms),   # décomposition : recherche
        "llm_ms_avg": _avg(_llm_ms),         # décomposition : génération LLM
        "last_ask_ago_s": round(time.time() - _last_ask) if _last_ask else None,
    }

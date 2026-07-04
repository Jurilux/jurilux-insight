"""Génération de réponse RAG via l'API Anthropic.

Le modèle reçoit les chunks trouvés par Meilisearch et doit répondre en JSON
strict. On en tire AskResponse (answer, citations, refused, feedback).
"""
import json
import re
from typing import Optional

import anthropic

from .config import settings
from .schemas import AskResponse, Citation, Feedback
from .search import Hit

SYSTEM_PROMPT = """Tu es Jurilux, assistant juridique spécialisé en droit luxembourgeois.
Tu réponds UNIQUEMENT à partir des extraits fournis (jurisprudence luxembourgeoise et textes de Legilux).

Règles :
- Réponds en français, de façon structurée et sourcée.
- Chaque affirmation juridique doit citer sa source via son doc_id.
- Si les extraits ne permettent pas de répondre, refuse (refused=true) et explique pourquoi dans feedback.why.
- Ne jamais inventer de jurisprudence, d'article de loi ou de référence.
- Rappelle si utile que ceci ne remplace pas un avis d'avocat.

Tu réponds EXCLUSIVEMENT avec un objet JSON valide, sans texte autour, au format :
{
  "answer": "réponse en markdown, ou null si refus",
  "used_doc_ids": ["doc_id des extraits réellement utilisés"],
  "refused": false,
  "status": "ok" | "partial",
  "feedback": {
    "why": "pourquoi cette réponse / ce refus",
    "what_we_see": ["constats tirés des extraits"],
    "limits": "limites de la réponse",
    "how_to_improve": ["comment améliorer la question"]
  }
}
"status": "ok" si les extraits couvrent bien la question, "partial" s'ils ne la couvrent que partiellement."""


def _context_block(hits: list[Hit]) -> str:
    parts = []
    for h in hits[: settings.max_context_chunks]:
        meta = f"doc_id={h.doc_id}"
        if h.title:
            meta += f" | titre={h.title}"
        if h.year:
            meta += f" | année={h.year}"
        if h.juridiction_key:
            meta += f" | juridiction={h.juridiction_key}"
        if h.source_type:
            meta += f" | type={h.source_type}"
        parts.append(f"<extrait {meta}>\n{h.text}\n</extrait>")
    return "\n\n".join(parts)


def _extract_json(raw: str) -> Optional[dict]:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _citation_from_hit(h: Hit) -> Citation:
    snippet = h.text[: settings.snippet_len]
    src = h.source_type if h.source_type in ("jurisprudence", "law", "projet_loi") else None
    return Citation(
        doc_id=h.doc_id, url=h.url, pdf_url=h.pdf_url, year=h.year,
        juridiction_key=h.juridiction_key, content=snippet,
        source_type=src, title=h.title,
    )


def refusal(why: str) -> AskResponse:
    return AskResponse(
        answer=None, citations=[], refused=True, status="ok",
        feedback=Feedback(why=why),
        prompt_version=settings.prompt_version,
    )


def answer(q: str, hits: list[Hit], temperature: float) -> AskResponse:
    if not hits:
        return refusal(
            "Aucun document pertinent trouvé dans le corpus pour cette question "
            "(avec les filtres appliqués, le cas échéant)."
        )

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=temperature,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Extraits du corpus :\n\n{_context_block(hits)}\n\nQuestion : {q}",
        }],
    )
    raw = "".join(b.text for b in msg.content if b.type == "text")
    data = _extract_json(raw)

    if data is None:
        # Le modèle n'a pas rendu de JSON : on renvoie le texte brut en mode dégradé.
        return AskResponse(
            answer=raw or None,
            citations=[_citation_from_hit(h) for h in hits[: settings.max_context_chunks]],
            refused=not raw, status="partial",
            feedback=Feedback(limits="Réponse non structurée (JSON du modèle invalide)."),
            prompt_version=settings.prompt_version,
        )

    fb = data.get("feedback") or {}
    feedback = Feedback(
        why=fb.get("why"), what_we_see=fb.get("what_we_see"),
        limits=fb.get("limits"), how_to_improve=fb.get("how_to_improve"),
    )

    if data.get("refused"):
        resp = refusal("")
        resp.feedback = feedback
        return resp

    used = {str(d) for d in data.get("used_doc_ids") or []}
    seen: set[str] = set()
    citations: list[Citation] = []
    for h in hits:
        if h.doc_id in seen:
            continue
        if not used or h.doc_id in used:
            citations.append(_citation_from_hit(h))
            seen.add(h.doc_id)

    status = data.get("status") if data.get("status") in ("ok", "partial") else "ok"
    return AskResponse(
        answer=data.get("answer"), citations=citations, refused=False,
        status=status, feedback=feedback, prompt_version=settings.prompt_version,
    )

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
from .search import Hit, corpus_overview

SYSTEM_PROMPT = """Tu es Jurilux, assistant juridique spécialisé en droit luxembourgeois.
Pour les questions de DROIT, tu réponds à partir des extraits fournis (jurisprudence luxembourgeoise
et textes de Legilux). Pour les questions sur JURILUX lui-même (l'outil, son corpus, tes capacités),
tu réponds à partir du bloc « À PROPOS DE JURILUX » fourni.

Règles :
- QUESTION MÉTA / CONVERSATIONNELLE (sur Jurilux, son corpus, son périmètre, son ampleur, son
  fonctionnement, tes capacités ; ou « bonjour », « que sais-tu faire », « combien de textes as-tu ») :
  réponds NORMALEMENT et utilement à partir du bloc « À PROPOS DE JURILUX », status="ok", used_doc_ids=[],
  ne refuse JAMAIS et n'exige pas d'extrait juridique. Si l'info précise demandée n'y figure pas
  (ex. répartition par discipline/matière, non suivie), dis-le honnêtement et donne ce que tu sais
  (totaux, composition du corpus, comment explorer une matière). Ne confonds pas « je n'ai pas cette
  statistique » avec un refus juridique.
- Réponds en français, de façon structurée et sourcée.
- Chaque affirmation juridique doit citer sa source via son doc_id.
- Cite le TEXTE lui-même en priorité : quand un extrait de type « law » (loi, code,
  règlement) est pertinent, cite-le directement (dans used_doc_ids) comme source
  première du droit, en plus de la jurisprudence qui l'applique. Ne te contente pas de
  citer une décision qui reproduit un article si l'extrait du texte est fourni.
- PRIVILÉGIE TOUJOURS une réponse utile, même partielle, à un refus. Dès que les extraits
  couvrent NE SERAIT-CE QU'EN PARTIE la question, réponds (status="partial") : expose
  clairement ce que le corpus documente (protections, articles, décisions pertinentes),
  organise-le, et signale honnêtement ce qui n'est pas couvert. Une réponse partielle
  cadrée AIDE l'utilisateur ; un refus le laisse sans rien et le frustre.
  Exemple : à « protections entre collègues », ne refuse pas — présente les protections
  effectivement documentées (harcèlement, sécurité au travail, femmes enceintes, délégués…)
  puis précise que le corpus n'en donne pas une synthèse générale exhaustive.
- Ne refuse (refused=true) QUE dans deux cas : (a) la question est hors du droit
  luxembourgeois, ou (b) AUCUN extrait n'a le moindre rapport avec la question. Sinon,
  réponds — quitte à ce que ce soit partiel. Un extrait de jurisprudence pertinent SUFFIT
  à répondre (partiellement) : ne refuse pas sous prétexte qu'aucun TEXTE de loi de synthèse
  n'est fourni.
- QUESTION TRÈS LARGE (ex. « Quels sont mes droits en tant que salarié ? ») : ne refuse
  JAMAIS. Donne un aperçu structuré des thèmes que les extraits permettent d'aborder
  (ce que le corpus documente : licenciement, harcèlement, préavis, congés, sécurité…),
  puis oriente vers 2-3 angles PRÉCIS via suggested_question et how_to_improve.
- Ne jamais inventer de jurisprudence, d'article de loi ou de référence : n'affirme que ce
  que les extraits soutiennent, et distingue clairement le certain de l'incomplet.
- Rappelle si utile que ceci ne remplace pas un avis d'avocat.

Tu réponds EXCLUSIVEMENT avec un objet JSON valide, sans texte autour, au format :
{
  "answer": "réponse en markdown, ou null si refus",
  "used_doc_ids": ["doc_id des extraits réellement utilisés"],
  "refused": false,
  "status": "ok" | "partial",
  "suggested_question": "une question VOISINE, plus précise ou mieux cadrée, à laquelle tu PEUX répondre avec certitude à partir des extraits fournis ; null si aucune",
  "feedback": {
    "why": "pourquoi cette réponse / ce refus, en une phrase claire et bienveillante",
    "what_we_see": ["constats tirés des extraits"],
    "limits": "limites de la réponse",
    "how_to_improve": ["2 à 3 reformulations concrètes et prêtes à l'emploi de la question, formulées avec le vocabulaire juridique adéquat"]
  }
}
"status": "ok" si les extraits couvrent bien la question ; "partial" dès qu'ils ne la couvrent
que partiellement — et dans ce cas tu RÉPONDS quand même (refused=false), tu ne refuses pas.

IMPORTANT — ne laisse JAMAIS l'utilisateur dans une impasse, que la réponse soit partielle OU refusée :
- "suggested_question" et "how_to_improve" DOIVENT être SPÉCIFIQUES et CIBLÉES — jamais larges.
  Une question précise (« Quel préavis pour un licenciement au Luxembourg ? ») aboutit ; une question
  large (« Quels sont mes droits ? ») échoue. Nomme un thème concret, un article, une situation.
  Ces suggestions, une fois cliquées, DOIVENT donner une vraie réponse : privilégie donc les angles
  que les extraits couvrent effectivement.
- Fournis toujours 1 "suggested_question" (le meilleur angle) + 2 à 3 "how_to_improve" (reformulations cliquables).
- Reste chaleureux et orienté solution : « voici ce que je peux dire », jamais un simple « non »."""

PEDAGOGICAL_SUFFIX = """

MODE PÉDAGOGIQUE (étudiant) : structure la réponse de façon didactique en trois temps —
1) le principe juridique en jeu, 2) le texte applicable, 3) son application par la
jurisprudence. Définis les termes techniques et explique le raisonnement, sans jargon inutile."""


def _about_block() -> str:
    """Faits sur Jurilux + composition du corpus, pour répondre aux questions méta
    (sur l'outil), pas sur le droit lui-même."""
    try:
        c = corpus_overview()
    except Exception:
        c = {}
    dec, txt, prj = c.get("decisions"), c.get("texts"), c.get("projets")
    upd, yr = c.get("updated"), c.get("latest_year")
    return (
        "À PROPOS DE JURILUX (pour répondre aux questions sur l'OUTIL/le CORPUS, pas sur le droit) :\n"
        "- Jurilux : assistant de recherche juridique luxembourgeois. Question en langage naturel → "
        "réponse sourcée croisant jurisprudence (open-data data.public.lu) et textes Legilux, "
        "chaque source vérifiable (lien vers le PDF).\n"
        f"- Corpus indexé : {dec} décisions de jurisprudence ; {txt} textes de loi CONSOLIDÉS "
        "(lois, règlements grand-ducaux, codes — dernière version de chacun) ; "
        f"{prj} projets de loi. À jour au {upd}, dernière année couverte {yr}.\n"
        "- Le corpus n'est PAS catégorisé par discipline/matière : il n'existe pas de compteur par "
        "branche du droit (travail, famille, pénal, civil…). Les textes couvrent l'ensemble de la "
        "législation consolidée. Pour explorer une matière, poser une question juridique dessus ; "
        "des filtres (année, juridiction, type de source) sont disponibles.\n"
        "- Capacités : réponses sourcées avec citations → PDF, mode pédagogique (étudiant), filtres, "
        "espaces de travail/dossiers partagés, alertes de veille. Ne remplace pas un avis d'avocat."
    )


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


def answer(q: str, hits: list[Hit], temperature: float, pedagogical: bool = False) -> AskResponse:
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
        system=SYSTEM_PROMPT + (PEDAGOGICAL_SUFFIX if pedagogical else ""),
        messages=[{
            "role": "user",
            "content": (f"{_about_block()}\n\n"
                        f"=== Extraits du corpus (pour les questions de DROIT) ===\n\n"
                        f"{_context_block(hits)}\n\nQuestion : {q}"),
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

    suggested = data.get("suggested_question")
    suggested = suggested.strip() if isinstance(suggested, str) and suggested.strip() else None

    if data.get("refused"):
        # Refus « doux » : on ne laisse pas l'utilisateur dans une impasse. On garde les
        # pistes (sources les plus proches), la question-pivot et les reformulations.
        pistes: list[Citation] = []
        seen_p: set = set()
        for h in hits:
            if h.doc_id in seen_p:
                continue
            seen_p.add(h.doc_id)
            pistes.append(_citation_from_hit(h))
            if len(pistes) >= 5:
                break
        return AskResponse(
            answer=None, citations=pistes, refused=True, status="ok",
            feedback=feedback, suggested_question=suggested,
            prompt_version=settings.prompt_version,
        )

    used = {str(d) for d in data.get("used_doc_ids") or []}
    seen: set = set()
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
        status=status, feedback=feedback, suggested_question=suggested,
        prompt_version=settings.prompt_version,
    )

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

_INTRO_RULES = """Tu es Jurilux, assistant juridique spécialisé en droit luxembourgeois.
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
- HORS PÉRIMÈTRE (recette de cuisine, météo, code informatique, question personnelle… — rien
  à voir avec le droit luxembourgeois ni avec Jurilux) : refuse (refused=true), explique
  brièvement ton périmètre, et surtout **used_doc_ids=[]** — ne cite AUCUN document (les extraits
  récupérés sont alors hors sujet, ne fais PAS semblant qu'ils sont pertinents). Pas de
  suggested_question ni de how_to_improve dans ce cas.
- QUESTION TRÈS LARGE (ex. « Quels sont mes droits en tant que salarié ? ») : ne refuse
  JAMAIS. Donne un aperçu structuré des thèmes que les extraits permettent d'aborder
  (ce que le corpus documente : licenciement, harcèlement, préavis, congés, sécurité…),
  puis oriente vers 2-3 angles PRÉCIS via suggested_question et how_to_improve.
- Ne jamais inventer de jurisprudence, d'article de loi ou de référence : n'affirme que ce
  que les extraits soutiennent, et distingue clairement le certain de l'incomplet.
- Rappelle si utile que ceci ne remplace pas un avis d'avocat."""


_JSON_FORMAT = """Tu réponds EXCLUSIVEMENT avec un objet JSON valide, sans texte autour, au format :
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

SYSTEM_PROMPT = _INTRO_RULES + "\n\n" + _JSON_FORMAT

# Format streamé : la réponse d'abord (markdown, affichée en direct), puis un JSON de méta compact.
_STREAM_FORMAT = """FORMAT DE RÉPONSE (streaming) — tu réponds en DEUX temps, dans CET ordre EXACT :
1) D'ABORD la réponse elle-même, en markdown, telle que l'utilisateur doit la lire. Si tu refuses,
   écris à la place une courte phrase bienveillante (1-2 lignes) disant que tu n'as pas de réponse
   certaine et pourquoi. N'écris PAS de JSON à cette étape.
2) PUIS, sur une nouvelle ligne, la balise EXACTE §§§META§§§ immédiatement suivie d'un objet JSON
   compact et valide, et RIEN après :
   {"used_doc_ids": ["doc_id réellement utilisés"], "status": "ok"|"partial", "refused": true|false,
    "suggested_question": "question voisine précise, ou null",
    "how_to_improve": ["2 à 3 reformulations précises et cliquables (surtout si partial ou refus)"]}
Rappels : suggested_question et how_to_improve SPÉCIFIQUES et ciblés (jamais larges). Pour une question
méta sur Jurilux : réponds normalement, status="ok", used_doc_ids=[], refused=false. Ne place JAMAIS la
balise §§§META§§§ ailleurs qu'entre la réponse et le JSON final."""

SYSTEM_PROMPT_STREAM = _INTRO_RULES + "\n\n" + _STREAM_FORMAT

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


def _pistes(hits: list[Hit], n: int = 5) -> list[Citation]:
    """Sources les plus proches (dédup par doc_id) — pour un refus doux."""
    out: list[Citation] = []
    seen: set = set()
    for h in hits:
        if h.doc_id in seen:
            continue
        seen.add(h.doc_id)
        out.append(_citation_from_hit(h))
        if len(out) >= n:
            break
    return out


def _citations_used(hits: list[Hit], used: set) -> list[Citation]:
    # On ne cite QUE les documents que le modèle a réellement utilisés. Si used est vide
    # (ex. question hors sujet), on n'affiche AUCUNE source — plutôt que tous les extraits récupérés.
    if not used:
        return []
    out: list[Citation] = []
    seen: set = set()
    for h in hits:
        if h.doc_id in seen:
            continue
        if h.doc_id in used:
            out.append(_citation_from_hit(h))
            seen.add(h.doc_id)
    return out


def _system_blocks(prompt: str, pedagogical: bool) -> list:
    """System en blocs avec cache_control : le préfixe statique (règles) est mis en cache
    (prompt caching Anthropic) → time-to-first-token plus court et coût input réduit."""
    text = prompt + (PEDAGOGICAL_SUFFIX if pedagogical else "")
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _user_content(q: str, hits: list[Hit]) -> str:
    return (f"{_about_block()}\n\n=== Extraits du corpus (pour les questions de DROIT) ===\n\n"
            f"{_context_block(hits)}\n\nQuestion : {q}")


def _build_messages(history, user_content: str) -> list:
    """Messages Claude : tours précédents de la session (contexte conversationnel) + question courante.
    Anthropic exige d'alterner user/assistant en commençant par user — on nettoie l'historique."""
    raw = []
    for turn in (history or [])[-6:]:
        role = getattr(turn, "role", None)
        content = getattr(turn, "content", None)
        if role in ("user", "assistant") and content:
            raw.append({"role": role, "content": content[:1500]})
    clean = []
    for m in raw:
        if not clean:
            if m["role"] == "user":
                clean.append(m)          # doit commencer par user
        elif m["role"] != clean[-1]["role"]:
            clean.append(m)
        else:
            clean[-1] = m                # même rôle consécutif → garde le plus récent
    if clean and clean[-1]["role"] == "user":
        clean.pop()                      # le tour précédant la question courante doit être assistant
    clean.append({"role": "user", "content": user_content})
    return clean


def answer(q: str, hits: list[Hit], temperature: float, pedagogical: bool = False,
           history=None) -> AskResponse:
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
        system=_system_blocks(SYSTEM_PROMPT, pedagogical),
        messages=_build_messages(history, _user_content(q, hits)),
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
        # Refus « doux » (dans le périmètre) : pistes + reformulations. Hors-sujet (aucune piste,
        # aucune reformulation) : aucune source.
        soft = bool(suggested or feedback.how_to_improve)
        return AskResponse(
            answer=None, citations=_pistes(hits) if soft else [], refused=True, status="ok",
            feedback=feedback, suggested_question=suggested,
            prompt_version=settings.prompt_version,
        )

    used = {str(d) for d in data.get("used_doc_ids") or []}
    status = data.get("status") if data.get("status") in ("ok", "partial") else "ok"
    return AskResponse(
        answer=data.get("answer"), citations=_citations_used(hits, used), refused=False,
        status=status, feedback=feedback, suggested_question=suggested,
        prompt_version=settings.prompt_version,
    )


def answer_stream(q: str, hits: list[Hit], temperature: float, pedagogical: bool = False, history=None):
    """Générateur de streaming. Yield des events :
      {"type":"delta","text": ...}  — morceaux de la réponse (markdown), à afficher en direct
      {"type":"meta", ...}          — méta finale (citations, refused, status, suggested_question, feedback)
    La partie texte précède la balise §§§META§§§ ; le JSON qui suit donne la méta."""
    if not hits:
        why = ("Aucun document pertinent trouvé dans le corpus pour cette question "
               "(avec les filtres appliqués, le cas échéant).")
        yield {"type": "delta", "text": why}
        yield {"type": "meta", "answer": None, "citations": [], "refused": True, "status": "ok",
               "suggested_question": None, "feedback": {"why": why},
               "prompt_version": settings.prompt_version}
        return

    DELIM = "§§§META§§§"
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    emitted = ""   # texte déjà émis (avant délimiteur)
    buf = ""       # tout le texte reçu
    meta_raw = ""  # après le délimiteur
    delim_seen = False

    with client.messages.stream(
        model=settings.anthropic_model,
        max_tokens=settings.anthropic_max_tokens,
        temperature=temperature,
        system=_system_blocks(SYSTEM_PROMPT_STREAM, pedagogical),
        messages=_build_messages(history, _user_content(q, hits)),
    ) as stream:
        for chunk in stream.text_stream:
            if delim_seen:
                meta_raw += chunk
                continue
            buf += chunk
            if DELIM in buf:
                before, _, after = buf.partition(DELIM)
                delta = before[len(emitted):]
                if delta:
                    yield {"type": "delta", "text": delta}
                emitted = before
                meta_raw = after
                delim_seen = True
            else:
                # garder une marge (le délimiteur peut être à cheval sur 2 chunks)
                safe = buf[:-len(DELIM)] if len(buf) > len(DELIM) else ""
                delta = safe[len(emitted):]
                if delta:
                    yield {"type": "delta", "text": delta}
                    emitted = safe

    if not delim_seen:
        # le modèle n'a pas produit la balise : tout est réponse
        delta = buf[len(emitted):]
        if delta:
            yield {"type": "delta", "text": delta}
        emitted = buf

    data = _extract_json(meta_raw) or {}
    refused = bool(data.get("refused"))
    status = data.get("status") if data.get("status") in ("ok", "partial") else "ok"
    suggested = data.get("suggested_question")
    suggested = suggested.strip() if isinstance(suggested, str) and suggested.strip() else None
    how = data.get("how_to_improve") if isinstance(data.get("how_to_improve"), list) else None
    text = emitted.strip()

    if refused:
        # Pistes UNIQUEMENT pour un refus « doux » (question dans le périmètre → il y a une
        # reformulation utile). Pour un hors-sujet (pas de piste), aucune source.
        cites = _pistes(hits) if (suggested or how) else []
        feedback = {"why": text or None, "how_to_improve": how}
        final_answer = None
    else:
        used = {str(d) for d in data.get("used_doc_ids") or []}
        cites = _citations_used(hits, used)
        feedback = {"how_to_improve": how} if how else None
        final_answer = text or None

    yield {"type": "meta", "answer": final_answer,
           "citations": [c.model_dump() for c in cites],
           "refused": refused, "status": status, "suggested_question": suggested,
           "feedback": feedback, "prompt_version": settings.prompt_version}

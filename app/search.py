"""Recherche Meilisearch.

Index `chunks` — un document par chunk :
  chunk_id (pk), doc_id, text, title, year, juridiction_key,
  source_type ('jurisprudence'|'law'), url, pdf_url
Filterable : year, juridiction_key, source_type. Searchable : text, title.
"""
import datetime
from dataclasses import dataclass
from typing import Optional

import meilisearch

from .config import settings
from .schemas import SearchFilters


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    text: str
    title: Optional[str] = None
    year: Optional[int] = None
    juridiction_key: Optional[str] = None
    source_type: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None


def _client() -> meilisearch.Client:
    return meilisearch.Client(settings.meili_url, settings.meili_master_key or None)


def ensure_index() -> None:
    """Crée/configure l'index (idempotent). Appelé par l'ingestion."""
    client = _client()
    client.create_index(settings.meili_index, {"primaryKey": "chunk_id"})
    idx = client.index(settings.meili_index)
    idx.update_filterable_attributes(["year", "juridiction_key", "source_type"])
    idx.update_searchable_attributes(["text", "title"])
    idx.update_displayed_attributes(
        ["chunk_id", "doc_id", "text", "title", "year",
         "juridiction_key", "source_type", "url", "pdf_url"]
    )


def meili_healthy() -> bool:
    try:
        return _client().is_healthy()
    except Exception:
        return False


def _filter_expr(f: SearchFilters) -> Optional[str]:
    parts: list[str] = []
    if f.year_min is not None:
        parts.append(f"year >= {int(f.year_min)}")
    if f.year_max is not None:
        parts.append(f"year <= {int(f.year_max)}")
    if f.juridiction_key:
        key = f.juridiction_key.replace('"', "")
        parts.append(f'juridiction_key = "{key}"')
    if f.source_type:
        st = f.source_type.replace('"', "")
        parts.append(f'source_type = "{st}"')
    return " AND ".join(parts) or None


CORPUS_META_INDEX = "corpus_meta"  # 1 doc (id=1) : compteurs au niveau document + fraîcheur


def corpus_overview() -> dict:
    """Périmètre du corpus : nb de décisions/textes (index corpus_meta, maj à
    l'ingestion) + total de chunks et année la plus récente (facettes Meili)."""
    client = _client()
    data: dict = {"decisions": None, "texts": None, "projets": None, "updated": None,
                  "chunks": None, "latest_year": None, "by_source": None}
    try:
        res = client.index(CORPUS_META_INDEX).search("", {"limit": 1})
        hits = res.get("hits") or []
        if hits:
            m = hits[0]
            data["decisions"] = m.get("decisions")
            data["texts"] = m.get("texts")
            data["projets"] = m.get("projets")
            data["updated"] = m.get("updated")
    except Exception:
        pass
    try:
        res = client.index(settings.meili_index).search(
            "", {"limit": 0, "facets": ["source_type", "year"]})
        fd = res.get("facetDistribution") or {}
        by_source = fd.get("source_type") or {}
        data["by_source"] = by_source or None
        # Somme exacte des facettes (estimatedTotalHits est plafonné par Meili).
        data["chunks"] = sum(by_source.values()) or res.get("estimatedTotalHits") or None
        # Année la plus récente, en ignorant les valeurs parasites (> année courante).
        cur = datetime.date.today().year
        years = [int(y) for y in (fd.get("year") or {}) if str(y).isdigit()]
        years = [y for y in years if 1900 <= y <= cur]
        data["latest_year"] = max(years) if years else None
    except Exception:
        pass
    return data


def _hits_from(res: dict) -> list[Hit]:
    out: list[Hit] = []
    for h in res.get("hits", []):
        out.append(Hit(
            chunk_id=str(h.get("chunk_id", "")),
            doc_id=str(h.get("doc_id", "")),
            text=h.get("text") or "",
            title=h.get("title"),
            year=h.get("year"),
            juridiction_key=h.get("juridiction_key"),
            source_type=h.get("source_type"),
            url=h.get("url"),
            pdf_url=h.get("pdf_url"),
        ))
    return out


def _search_one(q: str, limit: int, expr: Optional[str]) -> list[Hit]:
    params: dict = {"limit": limit}
    if expr:
        params["filter"] = expr
    return _hits_from(_client().index(settings.meili_index).search(q, params))


def search(q: str, top_k: int, filters: SearchFilters) -> list[Hit]:
    # Filtre de type explicite (jurisprudence|law|projet_loi) → on le respecte tel quel.
    if filters.source_type:
        return _search_one(q, top_k, _filter_expr(filters))

    # Sinon : recherche FÉDÉRÉE. Le corpus est ~92 % jurisprudence ; une recherche
    # simple ne remonte jamais de textes de loi. On interroge jurisprudence et lois
    # séparément, puis on entrelace (2 jurisprudence : 1 loi) pour GARANTIR la présence
    # des textes dans le contexte envoyé au modèle. Les projets de loi (non en vigueur)
    # ne sont inclus que sur filtre explicite.
    def sub(st: str) -> list[Hit]:
        f = SearchFilters(year_min=filters.year_min, year_max=filters.year_max,
                          juridiction_key=filters.juridiction_key, source_type=st)
        return _search_one(q, top_k, _filter_expr(f))

    juris, laws = sub("jurisprudence"), sub("law")
    # Entrelacement 1:1 : le contexte contient autant de textes que de jurisprudence,
    # pour que la loi applicable soit citable même quand elle est sur-classée par la
    # jurisprudence (recherche par mots-clés). La pertinence fine viendra du sémantique.
    ordered: list[Hit] = []
    ji = li = 0
    while ji < len(juris) or li < len(laws):
        if ji < len(juris):
            ordered.append(juris[ji]); ji += 1
        if li < len(laws):
            ordered.append(laws[li]); li += 1

    merged: list[Hit] = []
    seen: set = set()
    for h in ordered:
        if h.chunk_id in seen:
            continue
        seen.add(h.chunk_id)
        merged.append(h)
        if len(merged) >= top_k:
            break
    return merged

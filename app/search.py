"""Recherche Meilisearch.

Index `chunks` — un document par chunk :
  chunk_id (pk), doc_id, text, title, year, juridiction_key,
  source_type ('jurisprudence'|'law'), url, pdf_url
Filterable : year, juridiction_key, source_type. Searchable : text, title.
"""
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
    return " AND ".join(parts) or None


CORPUS_META_INDEX = "corpus_meta"  # 1 doc (id=1) : compteurs au niveau document + fraîcheur


def corpus_overview() -> dict:
    """Périmètre du corpus : nb de décisions/textes (index corpus_meta, maj à
    l'ingestion) + total de chunks et année la plus récente (facettes Meili)."""
    client = _client()
    data: dict = {"decisions": None, "texts": None, "updated": None,
                  "chunks": None, "latest_year": None}
    try:
        res = client.index(CORPUS_META_INDEX).search("", {"limit": 1})
        hits = res.get("hits") or []
        if hits:
            m = hits[0]
            data["decisions"] = m.get("decisions")
            data["texts"] = m.get("texts")
            data["updated"] = m.get("updated")
    except Exception:
        pass
    try:
        res = client.index(settings.meili_index).search(
            "", {"limit": 0, "facets": ["source_type", "year"]})
        fd = res.get("facetDistribution") or {}
        by_source = fd.get("source_type") or {}
        data["chunks"] = res.get("estimatedTotalHits") or (sum(by_source.values()) or None)
        years = [int(y) for y in (fd.get("year") or {}) if str(y).isdigit()]
        data["latest_year"] = max(years) if years else None
    except Exception:
        pass
    return data


def search(q: str, top_k: int, filters: SearchFilters) -> list[Hit]:
    idx = _client().index(settings.meili_index)
    params: dict = {"limit": top_k}
    expr = _filter_expr(filters)
    if expr:
        params["filter"] = expr
    res = idx.search(q, params)
    hits: list[Hit] = []
    for h in res.get("hits", []):
        hits.append(Hit(
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
    return hits

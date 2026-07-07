"""Vault — documents privés déposés par l'utilisateur (dépôt + Q&A + citations).

Style aligné sur le reste du projet : sqlite3 brut (métadonnées, table `vault_documents`)
et index Meili séparé `vault_chunks` pour les chunks. **Isolation stricte** : toute
recherche filtre sur `owner_id` — un utilisateur n'atteint jamais les documents d'un autre.
"""
import io
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from pypdf import PdfReader

from ingest.chunking import chunk_text
from . import insight
from .db import get_conn, now_iso
from .schemas import SearchFilters
from .search import Hit, _client, search as corpus_search

VAULT_INDEX = "vault_chunks"


# ---------- documents (SQLite) ----------
_COLS = "id, filename, mime, status, n_chunks, created_at"


def create_document(owner_id: int, filename: str, mime: Optional[str], text: str) -> dict:
    ts = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO vault_documents(owner_id, filename, mime, status, n_chunks, text, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (owner_id, filename, mime, "indexing", 0, text, ts))
        doc_id = cur.lastrowid
    return {"id": doc_id, "filename": filename, "mime": mime,
            "status": "indexing", "n_chunks": 0, "created_at": ts}


def set_status(doc_id: int, status: str, n_chunks: int = 0) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE vault_documents SET status = ?, n_chunks = ? WHERE id = ?",
                     (status, n_chunks, doc_id))


def list_documents(owner_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM vault_documents WHERE owner_id = ? ORDER BY id DESC",
            (owner_id,)).fetchall()
    return [dict(r) for r in rows]


def get_document(doc_id: int, owner_id: int) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {_COLS}, text FROM vault_documents WHERE id = ? AND owner_id = ?",
            (doc_id, owner_id)).fetchone()
    return dict(row) if row else None


def get_documents(doc_ids: list[int], owner_id: int) -> list[dict]:
    """Plusieurs documents (avec texte) du propriétaire en UNE requête (revue tabulaire)."""
    ids = [int(d) for d in doc_ids][:50]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT {_COLS}, text FROM vault_documents WHERE owner_id = ? AND id IN ({marks})",
            [owner_id, *ids]).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: int, owner_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM vault_documents WHERE id = ? AND owner_id = ?",
                           (doc_id, owner_id))
    return cur.rowcount > 0


# ---------- extraction de texte ----------
def extract_text(filename: str, data: bytes) -> str:
    if (filename or "").lower().endswith(".pdf"):
        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(pages)).strip()
    return data.decode("utf-8", errors="ignore").strip()


# ---------- index privé Meili ----------
def ensure_vault_index() -> None:
    """Crée/configure l'index privé (idempotent, appelé paresseusement à l'indexation)."""
    client = _client()
    try:
        client.create_index(VAULT_INDEX, {"primaryKey": "chunk_id"})
    except Exception:
        pass
    idx = client.index(VAULT_INDEX)
    idx.update_filterable_attributes(["owner_id", "vault_doc_id"])
    idx.update_searchable_attributes(["text", "filename"])


def index_chunks(owner_id: int, doc_id: int, filename: str, text: str) -> int:
    ensure_vault_index()
    idx = _client().index(VAULT_INDEX)
    docs = [
        {"chunk_id": f"{doc_id}:{i}", "vault_doc_id": doc_id, "owner_id": owner_id,
         "text": piece, "filename": filename}
        for i, piece in enumerate(chunk_text(text))
    ]
    if docs:
        idx.add_documents(docs)
    return len(docs)


def delete_chunks(owner_id: int, doc_id: int) -> None:
    idx = _client().index(VAULT_INDEX)
    idx.delete_documents(filter=f'owner_id = {int(owner_id)} AND vault_doc_id = {int(doc_id)}')


def search_vault(owner_id: int, q: str, doc_ids: Optional[list[int]], top_k: int) -> list[Hit]:
    parts = [f"owner_id = {int(owner_id)}"]  # isolation obligatoire
    if doc_ids:
        ors = " OR ".join(f"vault_doc_id = {int(d)}" for d in doc_ids)
        parts.append(f"({ors})")
    res = _client().index(VAULT_INDEX).search(q, {"limit": top_k, "filter": " AND ".join(parts)})
    # On réutilise Hit : doc_id = id du document Vault, title = nom de fichier.
    return [Hit(chunk_id=str(h.get("chunk_id", "")), doc_id=str(h.get("vault_doc_id", "")),
                text=h.get("text") or "", title=h.get("filename"), source_type=None)
            for h in res.get("hits", [])]


# ---------- vérificateur de citations ancré au corpus (le différenciateur) ----------
_ARTICLE_RE = re.compile(r"\bart(?:icle)?s?\.?\s+((?:[LRD]\.?\s?)?\d[\w\-\.]*)", re.IGNORECASE)
_ELI_RE = re.compile(r"\beli-[a-z0-9\-]+", re.IGNORECASE)
_MAX_REFS = 40  # borne le nb de vérifications corpus (1 recherche Meili par référence)


def extract_references(text: str) -> list[str]:
    """Références juridiques citées (articles, ELI), dédupliquées et **bornées** à
    `_MAX_REFS` (chaque référence coûte une recherche corpus)."""
    refs: list[str] = []
    seen: set[str] = set()

    def _add(r: str) -> None:
        if r.lower() not in seen:
            seen.add(r.lower())
            refs.append(r)

    for m in _ARTICLE_RE.finditer(text or ""):
        _add("article " + re.sub(r"\s+", "", m.group(1)))
    for m in _ELI_RE.finditer(text or ""):
        _add(m.group(0))
    return refs[:_MAX_REFS]


def _verify_one(ref: str) -> dict:
    try:
        hits = corpus_search(ref, 1, SearchFilters())
    except Exception:
        hits = []
    if hits:
        return {"ref": ref, "verified": True,
                "doc_id": hits[0].doc_id, "source_type": hits[0].source_type}
    return {"ref": ref, "verified": False, "doc_id": None, "source_type": None}


def verify_references(refs: list[str]) -> list[dict]:
    """Résout chaque référence contre le corpus PUBLIC (jurisprudence + Legilux).

    Anti-hallucination *sur les documents de l'utilisateur* : Jurilux possède la source
    de vérité pour le droit LU, ce qu'un Vault généraliste n'a pas. Les recherches (jusqu'à
    `_MAX_REFS`) sont menées en parallèle : la latence passe de N×RTT à ~1 RTT.
    """
    if not refs:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(refs))) as pool:
        return list(pool.map(_verify_one, refs))


# ---------- extraction structurée (locale/déterministe, réutilise le pipeline insight) ----------
_NUM = r"\d{1,3}(?:[ .]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?"
_CUR = r"€|EUR|euros?"
_AMOUNT_RE = re.compile(rf"(?:{_CUR})\s*(?:{_NUM})|(?:{_NUM})\s*(?:{_CUR})", re.IGNORECASE)
_MAX_AMOUNTS = 30


def _extract_amounts(text: str) -> list[str]:
    """Montants en euros cités (currency obligatoire → n'attrape ni années ni n° d'article),
    normalisés et dédupliqués."""
    out: list[str] = []
    seen: set[str] = set()
    for m in _AMOUNT_RE.finditer(text or ""):
        s = re.sub(r"\s+", " ", m.group(0)).strip()
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
        if len(out) >= _MAX_AMOUNTS:
            break
    return out


_MOIS = ("janvier février mars avril mai juin juillet août septembre octobre novembre décembre")
_DATE_RE = re.compile(
    r"\b(\d{1,2}(?:er)?\s+(?:" + "|".join(_MOIS.split()) + r")\s+\d{4}"
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{2,4})\b", re.IGNORECASE)
_MAX_EVENEMENTS = 60


def extract_timeline(text: str) -> list[dict]:
    """Chronologie LOCALE et déterministe : dates citées + contexte (phrase). Utile pour
    reconstituer les faits d'un dossier. Indicatif, dédupliqué, borné."""
    flat = re.sub(r"\s+", " ", text or "")
    out: list[dict] = []
    seen: set = set()
    for m in _DATE_RE.finditer(flat):
        deb = max(0, m.start() - 90)
        contexte = flat[deb:m.end() + 90].strip()
        cle = (m.group(1).lower(), contexte[:40])
        if cle in seen:
            continue
        seen.add(cle)
        out.append({"date": m.group(1), "contexte": contexte})
        if len(out) >= _MAX_EVENEMENTS:
            break
    return out


def extract_structure(text: str) -> dict:
    """Extraction structurée **locale et déterministe** (aucun appel LLM) d'un document
    déposé, en réutilisant les heuristiques du pipeline insight : avocats + côté, matière
    dominante, issue estimée, montants et références juridiques. **Indicatif** — les mêmes
    réserves que l'insight avocats s'appliquent (heuristique, pas de certitude)."""
    flat = text or ""
    parsed = insight.parse_chunk(flat)
    lawyers = [{"name": v["display"], "side": v["side"]} for v in parsed["lawyers"].values()]
    counter: Counter = Counter()
    insight.matter_hits(flat, counter)
    return {
        "lawyers": lawyers,
        "matter": counter.most_common(1)[0][0] if counter else None,
        "outcome": parsed["outcome"],   # 'A' (demandeur) | 'B' (défendeur) | None — estimé
        "amounts": _extract_amounts(flat),
        "references": extract_references(flat),
    }

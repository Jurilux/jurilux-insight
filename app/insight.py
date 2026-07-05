"""Insight — profiling des AVOCATS mentionnés dans la jurisprudence (données publiques).

Périmètre VOLONTAIREMENT limité aux avocats (« Maître X ») : pas de magistrats ni de greffiers
(zone la plus sensible RGPD/CNPD — cf. interdiction française du profilage des magistrats).
Extraction locale et déterministe (aucun appel externe). Usage interne.
"""
import re
import unicodedata
from collections import Counter
from typing import List, Optional

from .db import get_conn

# Prénom(s) capitalisé(s) + NOM de famille en MAJUSCULES.
# Ex. « Guy CASTEGNARO », « Jean-Luc GONNER », « Tonia FRIEDERS-SCHEIFER », « Fernando DIAS SOBRAL ».
_FIRST = r"[A-ZÉÈÀÂÎÏÔÜÇ][a-zà-öø-ÿ'’.-]*"
_SURNAME = r"[A-ZÉÈÀÂÎÏÔÜÇ]{2,}(?:[-'’ ][A-ZÉÈÀÂÎÏÔÜÇ]{2,})*"
_NAME_RE = re.compile(r"\bMa[iî]tre\s+(" + _FIRST + r"(?:[-\s]" + _FIRST + r"){0,2}\s+" + _SURNAME + r")")
# Placeholders de pseudonymisation à ignorer (« AVOCAT1. », « PERSONNE DE JUSTICE2. »…).
_PLACEHOLDER_RE = re.compile(r"\d|AVOCAT|PERSONNE|JUSTICE|SOCIET|REQU", re.IGNORECASE)


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def name_key(name: str) -> str:
    """Clé de regroupement : majuscules, sans accents, espaces normalisés (fusionne les variantes OCR)."""
    return re.sub(r"\s+", " ", _strip_accents(name).upper()).strip()


def extract_lawyers(text: str) -> List[str]:
    """Noms d'avocats (« Maître X ») nettoyés et dédupliqués depuis le texte d'une décision."""
    if not text:
        return []
    flat = re.sub(r"\s+", " ", text)  # aplatit les retours-ligne (les noms sont souvent coupés en fin de ligne)
    out = {}
    for m in _NAME_RE.finditer(flat):
        raw = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
        if len(raw) < 4 or _PLACEHOLDER_RE.search(raw):
            continue
        out.setdefault(name_key(raw), raw)  # 1re forme rencontrée par clé
    return list(out.values())


# ---------- accès données ----------
def record_many(rows) -> int:
    """rows: itérable de (name_key, display_name, doc_id, year, juridiction_key). INSERT OR IGNORE."""
    rows = list(rows)
    if not rows:
        return 0
    with get_conn() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO insight_appearances "
            "(name_key, display_name, doc_id, year, juridiction_key) VALUES (?,?,?,?,?)", rows)
        return conn.total_changes - before


def stats() -> dict:
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(DISTINCT name_key) nk, COUNT(*) n FROM insight_appearances").fetchone()
    return {"lawyers": r["nk"] or 0, "appearances": r["n"] or 0}


def list_lawyers(q: Optional[str], limit: int = 50) -> List[dict]:
    """Avocats classés par nombre de décisions (recherche optionnelle sur le nom)."""
    sql = ("SELECT name_key, MAX(display_name) name, COUNT(*) cases, "
           "MIN(year) first_year, MAX(year) last_year "
           "FROM insight_appearances ")
    args: list = []
    if q and q.strip():
        sql += "WHERE name_key LIKE ? "
        args.append("%" + name_key(q) + "%")
    sql += "GROUP BY name_key ORDER BY cases DESC, name LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_lawyer(key: str) -> Optional[dict]:
    """Profil d'un avocat : décisions, répartition par juridiction, période."""
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT display_name, doc_id, year, juridiction_key FROM insight_appearances "
            "WHERE name_key = ? ORDER BY year DESC, doc_id", (key,)).fetchall()]
    if not rows:
        return None
    jur = Counter(r["juridiction_key"] or "?" for r in rows)
    years = [r["year"] for r in rows if r["year"]]
    return {
        "name_key": key,
        "name": max((r["display_name"] for r in rows), key=len),  # forme la plus complète
        "cases_count": len(rows),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "jurisdictions": [{"key": k, "count": c} for k, c in jur.most_common()],
        "cases": rows,
    }

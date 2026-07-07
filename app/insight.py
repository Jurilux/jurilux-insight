"""Insight — profiling des AVOCATS mentionnés dans la jurisprudence (données publiques).

Périmètre VOLONTAIREMENT limité aux avocats (« Maître X ») : pas de magistrats ni de greffiers
(zone la plus sensible RGPD/CNPD). Extraction locale et déterministe (aucun appel externe).

Extraction :
- nom d'avocat (« Maître Prénom NOM »), en filtrant les placeholders de pseudonymisation ;
- côté (A = demandeur/appelant/requérant, B = défendeur/intimé) par proximité des marqueurs de rôle ;
- issue de la décision (heuristique sur le dispositif) → gagné/perdu ESTIMÉ (indicatif, jamais certain).
"""
import re
import unicodedata
from collections import Counter
from typing import List, Optional

from .db import get_conn
from .schemas import Citation

# --- avocats ---
_FIRST = r"[A-ZÉÈÀÂÎÏÔÜÇ][a-zà-öø-ÿ'’.-]*"
_SURNAME = r"[A-ZÉÈÀÂÎÏÔÜÇ]{2,}(?:[-'’ ][A-ZÉÈÀÂÎÏÔÜÇ]{2,})*"
_NAME_RE = re.compile(r"\bMa[iî]tre\s+(" + _FIRST + r"(?:[-\s]" + _FIRST + r"){0,2}\s+" + _SURNAME + r")")
_PLACEHOLDER_RE = re.compile(r"\d|AVOCAT|PERSONNE|JUSTICE|SOCIET|REQU", re.IGNORECASE)

# --- rôles (côté) ---
_ROLE_A = re.compile(r"demand(?:eur|eresse)|appelant|requ[ée]rant|poursuivant", re.IGNORECASE)
_ROLE_B = re.compile(r"d[ée]fend(?:eur|eresse|resse)|intim[ée]", re.IGNORECASE)

# --- issue (dispositif) ---
_DISPO_HINT = re.compile(r"par ces motifs|ainsi (?:fait|jug[ée])|d[ée]boute|condamne|confirme|r[ée]forme|casse",
                         re.IGNORECASE)
_OUT_A = re.compile(r"fait droit|d[ée]clar\w* .{0,8}fond|dit .{0,12}fond[ée]|r[ée]forme|casse et annule|annule le jugement",
                    re.IGNORECASE)
_OUT_B = re.compile(r"d[ée]boute|non fond[ée]|rejette|confirme le jugement|confirme l['’]|pourvoi.{0,30}non fond",
                    re.IGNORECASE)

# --- domaines de droit (matière dominante d'une décision, heuristique par mots-clés) ---
_MATTER_RE = [
    ("Droit du travail", re.compile(r"licenciement|contrat de travail|salari[ée]|pr[ée]avis|d[ée]mission|employeur|tribunal du travail|prud", re.I)),
    ("Bail / logement", re.compile(r"\bbail\b|bailleur|preneur|\bloyer|location|expulsion", re.I)),
    ("Famille", re.compile(r"divorce|garde d.{0,4}enfant|autorit[ée] parentale|pension alimentaire|[ée]poux|filiation", re.I)),
    ("Successions", re.compile(r"succession|h[ée]riti|\blegs\b|testament|indivision", re.I)),
    ("Sociétés / commercial", re.compile(r"soci[ée]t[ée]|g[ée]rant|actionnaire|faillite|liquidation|fonds de commerce", re.I)),
    ("Responsabilité civile", re.compile(r"responsabilit[ée]|dommage|pr[ée]judice|indemnisation", re.I)),
    ("Assurances", re.compile(r"assurance|assureur|sinistre", re.I)),
    ("Immobilier / construction", re.compile(r"immobili|copropri[ée]t[ée]|servitude|usufruit|construction", re.I)),
    ("Pénal", re.compile(r"p[ée]nal|pr[ée]venu|infraction|d[ée]lit\b|correctionnel", re.I)),
    ("Fiscal / administratif", re.compile(r"fiscal|imp[ôo]t|\btaxe|administratif|contribution", re.I)),
]
_DOCID_MATTER = [("TRAVAIL", "Droit du travail"), ("BAIL", "Bail / logement")]


def matter_hits(text: str, counter) -> None:
    """Accumule les occurrences de mots-clés par domaine dans le Counter fourni."""
    for name, rx in _MATTER_RE:
        n = len(rx.findall(text or ""))
        if n:
            counter[name] += n


def matter_from_docid(doc_id: str) -> Optional[str]:
    """Domaine sûr déduit du doc_id pour les chambres spécialisées (JPLTRAVAIL, JPLBAIL…)."""
    up = (doc_id or "").upper()
    for tag, name in _DOCID_MATTER:
        if tag in up:
            return name
    return None


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(name).upper()).strip()


def extract_lawyers(text: str) -> List[str]:
    """Noms d'avocats nettoyés et dédupliqués (compat / tests)."""
    return [v["display"] for v in parse_chunk(text)["lawyers"].values()]


def _side_before(flat: str, pos: int, window: int = 320) -> Optional[str]:
    """Côté de l'avocat = marqueur de rôle le plus proche AVANT sa mention."""
    seg = flat[max(0, pos - window):pos]
    a = list(_ROLE_A.finditer(seg))
    b = list(_ROLE_B.finditer(seg))
    la = a[-1].start() if a else -1
    lb = b[-1].start() if b else -1
    if la < 0 and lb < 0:
        return None
    return "A" if la > lb else "B"


def parse_chunk(text: str) -> dict:
    """Extrait d'un fragment de décision : {lawyers: {key: {display, side}}, outcome: 'A'|'B'|None}."""
    flat = re.sub(r"\s+", " ", text or "")
    lawyers: dict = {}
    for m in _NAME_RE.finditer(flat):
        raw = re.sub(r"\s+", " ", m.group(1)).strip(" .,-")
        if len(raw) < 4 or _PLACEHOLDER_RE.search(raw):
            continue
        k = name_key(raw)
        side = _side_before(flat, m.start())
        cur = lawyers.get(k)
        if cur is None:
            lawyers[k] = {"display": raw, "side": side}
        elif cur["side"] is None and side:  # complète le côté si trouvé plus loin
            cur["side"] = side
    outcome = None
    if _DISPO_HINT.search(flat):
        a, b = bool(_OUT_A.search(flat)), bool(_OUT_B.search(flat))
        outcome = "A" if a and not b else "B" if b and not a else None
    return {"lawyers": lawyers, "outcome": outcome}


# ---------- accès données ----------
def record_many(rows) -> int:
    """rows: (name_key, display_name, doc_id, year, juridiction_key, side, won, matter). INSERT OR IGNORE."""
    rows = list(rows)
    if not rows:
        return 0
    with get_conn() as conn:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO insight_appearances "
            "(name_key, display_name, doc_id, year, juridiction_key, side, won, matter) VALUES (?,?,?,?,?,?,?,?)", rows)
        return conn.total_changes - before


def stats() -> dict:
    with get_conn() as conn:
        r = conn.execute("SELECT COUNT(DISTINCT name_key) nk, COUNT(*) n FROM insight_appearances").fetchone()
    return {"lawyers": r["nk"] or 0, "appearances": r["n"] or 0}


def matters() -> List[dict]:
    """Domaines disponibles (pour le filtre), classés par volume."""
    with get_conn() as conn:
        rows = conn.execute("SELECT matter, COUNT(*) n FROM insight_appearances "
                            "WHERE matter IS NOT NULL GROUP BY matter ORDER BY n DESC").fetchall()
    return [{"name": r["matter"], "count": r["n"]} for r in rows]


def _taux(won: int, decided: int) -> Optional[float]:
    return round(won / decided, 3) if decided else None


def analytics(matter: Optional[str] = None, juridiction: Optional[str] = None) -> dict:
    """Analytics contentieux (données PUBLIQUES de jurisprudence, avocats/parties uniquement —
    jamais de magistrats). Volumes et taux de succès ESTIMÉ (indicatif) par matière, par
    juridiction et par année. `decided` = apparitions dont l'issue est estimable.

    NB : pas de montants ici — l'index insight ne les extrait pas encore (extension future de
    `insight_build.py`). Filtres optionnels : `matter`, `juridiction`."""
    where, args = [], []
    if matter:
        where.append("matter = ?"); args.append(matter)
    if juridiction:
        where.append("juridiction_key = ?"); args.append(juridiction)
    clause = ("WHERE " + " AND ".join(where) + " ") if where else ""

    # Les 4 agrégations sont indépendantes → une seule connexion partagée (au lieu de 4).
    def _agg(conn, dim: str) -> list:
        rows = conn.execute(
            f"SELECT {dim} AS cle, COUNT(*) cases, "
            "SUM(CASE WHEN won IN (0,1) THEN 1 ELSE 0 END) decided, "
            "SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) won "
            f"FROM insight_appearances {clause}"
            f"GROUP BY {dim} HAVING cle IS NOT NULL ORDER BY cases DESC", args).fetchall()
        return [{"cle": r["cle"], "cases": r["cases"], "decided": r["decided"],
                 "won": r["won"], "win_rate": _taux(r["won"], r["decided"])} for r in rows]

    with get_conn() as conn:
        g = conn.execute(
            "SELECT COUNT(*) cases, "
            "SUM(CASE WHEN won IN (0,1) THEN 1 ELSE 0 END) decided, "
            "SUM(CASE WHEN won = 1 THEN 1 ELSE 0 END) won, "
            "COUNT(DISTINCT name_key) lawyers "
            f"FROM insight_appearances {clause}", args).fetchone()
        return {
            "overall": {"cases": g["cases"] or 0, "decided": g["decided"] or 0,
                        "won": g["won"] or 0, "win_rate": _taux(g["won"] or 0, g["decided"] or 0),
                        "lawyers": g["lawyers"] or 0},
            "by_matter": _agg(conn, "matter"),
            "by_juridiction": _agg(conn, "juridiction_key"),
            "by_year": _agg(conn, "year"),
        }


def list_lawyers(q: Optional[str], limit: int = 50, sort: str = "cases",
                 matter: Optional[str] = None) -> List[dict]:
    """Avocats filtrés (recherche, matière) et triés (cases | recent | winrate)."""
    where: list = []
    args: list = []
    if q and q.strip():
        where.append("name_key LIKE ?")
        args.append("%" + name_key(q) + "%")
    if matter:
        where.append("matter = ?")   # ne compte que les décisions de ce domaine → top du domaine
        args.append(matter)
    sql = ("SELECT name_key, MAX(display_name) name, COUNT(*) cases, "
           "MIN(year) first_year, MAX(year) last_year, "
           "SUM(CASE WHEN won=1 THEN 1 ELSE 0 END) won, "
           "SUM(CASE WHEN won IN (0,1) THEN 1 ELSE 0 END) decided "
           "FROM insight_appearances ")
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "GROUP BY name_key "
    if sort == "winrate":
        sql += "HAVING decided >= 10 ORDER BY (won * 1.0 / decided) DESC, cases DESC "
    elif sort == "recent":
        sql += "ORDER BY last_year DESC, cases DESC "
    else:
        sql += "ORDER BY cases DESC, name "
    sql += "LIMIT ?"
    args.append(max(1, min(limit, 200)))
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]


def get_lawyer(key: str) -> Optional[dict]:
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT display_name, doc_id, year, juridiction_key, side, won, matter FROM insight_appearances "
            "WHERE name_key = ? ORDER BY year DESC, doc_id", (key,)).fetchall()]
    if not rows:
        return None
    years = [r["year"] for r in rows if r["year"]]
    won = sum(1 for r in rows if r["won"] == 1)
    lost = sum(1 for r in rows if r["won"] == 0)
    mts = Counter(r["matter"] for r in rows if r["matter"])
    return {
        "name_key": key,
        "name": max((r["display_name"] for r in rows), key=len),
        "cases_count": len(rows),
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "as_demandeur": sum(1 for r in rows if r["side"] == "A"),
        "as_defendeur": sum(1 for r in rows if r["side"] == "B"),
        "won": won, "lost": lost, "decided": won + lost,   # « decided » = issue estimable
        "matters": [{"name": k, "count": c} for k, c in mts.most_common()],
        "cocounsel": _cocounsel(conn_key=key),
        "cases": rows,
    }


def _cocounsel(conn_key: str, limit: int = 12) -> List[dict]:
    """Avocats co-cités dans les mêmes décisions (adversaires ou co-conseils selon les côtés)."""
    sql = (
        "SELECT b.name_key, MAX(b.display_name) name, COUNT(*) n, "
        " SUM(CASE WHEN a.side IS NOT NULL AND b.side IS NOT NULL AND a.side<>b.side THEN 1 ELSE 0 END) opp, "
        " SUM(CASE WHEN a.side IS NOT NULL AND b.side IS NOT NULL AND a.side =b.side THEN 1 ELSE 0 END) same "
        "FROM insight_appearances a JOIN insight_appearances b "
        "  ON a.doc_id = b.doc_id AND b.name_key <> a.name_key "
        "WHERE a.name_key = ? GROUP BY b.name_key ORDER BY n DESC, name LIMIT ?")
    with get_conn() as conn:
        rows = conn.execute(sql, (conn_key, limit)).fetchall()
    out = []
    for r in rows:
        rel = "adversaire" if r["opp"] > r["same"] else "co-conseil" if r["same"] > r["opp"] else "confrère"
        out.append({"name_key": r["name_key"], "name": r["name"], "count": r["n"], "relation": rel})
    return out


# ---------- dashboard B2B : vue d'ensemble, benchmark d'avocats, export ----------
# Surface principale du produit : ces fonctions agrègent les données PUBLIQUES de jurisprudence
# (avocats/parties uniquement, JAMAIS de magistrats) pour alimenter les tableaux de bord.
def overview() -> dict:
    """KPIs d'en-tête du dashboard (accueil analytics). Volumétrie globale + têtes de liste
    matières / juridictions, sur une seule requête agrégée (via analytics)."""
    a = analytics()
    ov = a["overall"]
    years = [y["cle"] for y in a["by_year"] if y["cle"] is not None]
    return {
        "lawyers": ov["lawyers"],
        "cases": ov["cases"],
        "decided": ov["decided"],
        "won": ov["won"],
        "win_rate": ov["win_rate"],
        "first_year": min(years) if years else None,
        "last_year": max(years) if years else None,
        "top_matters": a["by_matter"][:6],
        "top_juridictions": a["by_juridiction"][:6],
    }


def compare(keys: List[str]) -> dict:
    """Benchmark côte à côte de plusieurs avocats (2 à 6 profils). Chaque colonne est un
    profil condensé : volume, taux de succès ESTIMÉ (indicatif), répartition demandeur/
    défendeur, période et top matières. `keys` = name_key (issus de list_lawyers)."""
    profiles = []
    seen: set = set()
    for k in keys[:6]:
        key = name_key(k)
        if key in seen:
            continue
        seen.add(key)
        p = get_lawyer(key)
        if not p:
            continue
        profiles.append({
            "name_key": p["name_key"], "name": p["name"],
            "cases": p["cases_count"],
            "won": p["won"], "lost": p["lost"], "decided": p["decided"],
            "win_rate": _taux(p["won"], p["decided"]),
            "as_demandeur": p["as_demandeur"], "as_defendeur": p["as_defendeur"],
            "first_year": p["first_year"], "last_year": p["last_year"],
            "matters": p["matters"][:4],
        })
    return {"profiles": profiles}


_RGPD_KINDS = {"acces", "rectification", "opposition"}


def record_rgpd_request(name: str, kind: str, email: Optional[str] = None,
                        message: Optional[str] = None) -> dict:
    """Enregistre une demande RGPD/CNPD (droit d'accès/rectification/opposition) sur le
    profilage d'un avocat nommé. Le profilage portant sur des personnes, ce canal est requis."""
    name = (name or "").strip()
    kind = (kind or "").strip().lower()
    if len(name) < 2:
        raise ValueError("nom de l'avocat requis")
    if kind not in _RGPD_KINDS:
        raise ValueError("type de demande invalide (acces | rectification | opposition)")
    from .db import now_iso  # horodatage commun du module db
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO insight_rgpd_requests(name, kind, email, message, status, created_at) "
            "VALUES (?,?,?,?, 'ouverte', ?)",
            (name, kind, (email or "").strip() or None, (message or "").strip() or None, now_iso()))
        return {"id": cur.lastrowid, "name": name, "kind": kind, "status": "ouverte"}


def list_rgpd_requests(limit: int = 200) -> List[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, kind, email, message, status, created_at "
            "FROM insight_rgpd_requests ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [dict(r) for r in rows]


def export_lawyers_csv(q: Optional[str] = None, limit: int = 200, sort: str = "cases",
                       matter: Optional[str] = None) -> str:
    """Export CSV de la liste d'avocats (mêmes filtres/tri que list_lawyers). Taux de succès
    ESTIMÉ recalculé par ligne. Pour l'export B2B (tableur cabinet/assureur)."""
    import csv
    import io
    rows = list_lawyers(q, limit, sort=sort, matter=matter)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name_key", "avocat", "decisions", "gagnees_estim", "estimables",
                "taux_succes_estim", "premiere_annee", "derniere_annee"])
    for r in rows:
        decided = r.get("decided") or 0
        won = r.get("won") or 0
        wr = _taux(won, decided)
        w.writerow([r["name_key"], r["name"], r["cases"], won, decided,
                    "" if wr is None else wr, r["first_year"], r["last_year"]])
    return buf.getvalue()


# ---------- recherche nominative d'avocat depuis une question en langage naturel ----------
_LAWYER_HINT = re.compile(r"avocate?|ma[iî]tre|barreau|\bconseil\b|plaid|plaideur|plaidoirie", re.IGNORECASE)
_Q_STRIP = re.compile(
    r"\b(donne|donnez|moi|nous|quels?|quelles?|textes?|d[ée]cisions?|jurisprudences?|affaires?|"
    r"arr[êe]ts?|jugements?|dossiers?|mentionn\w*|cit\w*|impliqu\w*|concern\w*|trouve\w*|liste\w*|"
    r"montre\w*|plaid\w*|d[ée]fend\w*|repr[ée]sent\w*|intervi\w*|par|de|des|du|la|le|les|un|une|sur|"
    r"pour|avec|est|qui|où|ou|a|ont|dans|corpus|avocate?|ma[iî]tre|conseil)\b|[’']", re.IGNORECASE)


def _candidate_name(q: str) -> str:
    s = _Q_STRIP.sub(" ", q or "")
    s = re.sub(r"\b[lL]\b", " ", s)              # « l' » résiduel
    s = re.sub(r"[^\wÀ-ÿ\s-]", " ", s)           # retire la ponctuation (?, ., etc.)
    return re.sub(r"\s+", " ", s).strip()


def lawyer_lookup(q: str):
    """Si la question cherche un avocat NOMMÉ présent dans l'index, renvoie {answer, citations}.
    Sinon None (→ recherche juridique normale). Ne route que sur une vraie correspondance."""
    if not _LAWYER_HINT.search(q or ""):
        return None
    cand = _candidate_name(q)
    if len(cand) < 3:
        return None
    rows = list_lawyers(cand, 6)
    if not rows:
        return None
    # Plusieurs avocats distincts correspondent → demander de préciser.
    if len(rows) > 1 and rows[1]["cases"] >= max(2, int(rows[0]["cases"] * 0.4)):
        names = " · ".join(f"{r['name']} ({r['cases']})" for r in rows[:6])
        return {"answer": f"Plusieurs avocats correspondent à « {cand} » : {names}.\n\n"
                          "Précisez le nom complet pour voir la liste de leurs décisions.",
                "citations": []}
    prof = get_lawyer(rows[0]["name_key"])
    if not prof:
        return None
    n = prof["cases_count"]
    period = ""
    if prof["first_year"] and prof["last_year"]:
        period = (f" ({prof['first_year']})" if prof["first_year"] == prof["last_year"]
                  else f" ({prof['first_year']}–{prof['last_year']})")
    md = f"**Maître {prof['name']}** apparaît dans **{n} décision{'s' if n > 1 else ''}** du corpus{period}."
    if prof["matters"]:
        md += "\n\nDomaines : " + ", ".join(f"{m['name']} ({m['count']})" for m in prof["matters"][:4]) + "."
    shown = prof["cases"][:25]
    md += (f"\n\nVoici {'les' if n <= 25 else 'les 25 décisions les plus récentes'} — "
           "chaque source ouvre le PDF de la décision.")
    cites = [Citation(doc_id=c["doc_id"], source_type="jurisprudence",
                      year=c["year"], juridiction_key=c["juridiction_key"], content="") for c in shown]
    return {"answer": md, "citations": cites}

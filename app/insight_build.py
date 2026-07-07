"""Construit l'index Insight (avocats) : scanne l'index Meili `chunks`, agrège par décision
(avocats + côté + issue du dispositif), calcule le gagné/perdu ESTIMÉ, remplit insight_appearances.

Lancé après un refresh du corpus :  docker compose run --rm api python -m app.insight_build
"""
import json
import urllib.request
from collections import Counter

from . import db, insight
from .config import settings

BATCH = 2000


def _fetch(offset: int, limit: int) -> list:
    url = (f"{settings.meili_url}/indexes/{settings.meili_index}/documents"
           f"?limit={limit}&offset={offset}&fields=doc_id,text,year,juridiction_key,source_type")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + (settings.meili_master_key or "")})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read()).get("results", [])


def run() -> dict:
    db.init_db()
    # NB : on ne vide PAS la table maintenant. L'ancien index reste servi pendant tout le scan ;
    # le remplacement (DELETE + INSERT) se fait en une seule transaction à la fin (gap ~ secondes).
    acc: dict = {}  # doc_id -> {year, jur, lawyers:{key:{display,side}}, outcome}
    offset = total = 0
    while True:
        docs = _fetch(offset, BATCH)
        if not docs:
            break
        for d in docs:
            if d.get("source_type") != "jurisprudence" or not d.get("doc_id"):
                continue
            doc_id = d["doc_id"]
            parsed = insight.parse_chunk(d.get("text") or "")
            e = acc.get(doc_id)
            if e is None:
                e = acc[doc_id] = {"year": d.get("year"), "jur": d.get("juridiction_key"),
                                   "lawyers": {}, "outcome": None, "matter": Counter(), "amount": None}
            for k, v in parsed["lawyers"].items():
                cur = e["lawyers"].get(k)
                if cur is None:
                    e["lawyers"][k] = {"display": v["display"], "side": v["side"]}
                elif cur["side"] is None and v["side"]:
                    cur["side"] = v["side"]
            if parsed["outcome"] and not e["outcome"]:
                e["outcome"] = parsed["outcome"]
            insight.matter_hits(d.get("text") or "", e["matter"])
            # Montant € estimé de la décision : on garde le plus grand vu sur ses chunks.
            amt = insight.extract_amount(d.get("text") or "")
            if amt is not None and (e["amount"] is None or amt > e["amount"]):
                e["amount"] = amt
        total += len(docs)
        offset += BATCH
        if offset % 100000 == 0:
            print(f"  {offset} chunks scannés, {len(acc)} décisions", flush=True)

    rows = []
    for doc_id, e in acc.items():
        matter = insight.matter_from_docid(doc_id) or (e["matter"].most_common(1)[0][0] if e["matter"] else None)
        for k, v in e["lawyers"].items():
            won = None
            if v["side"] and e["outcome"]:
                won = 1 if v["side"] == e["outcome"] else 0
            rows.append((k, v["display"], doc_id, e["year"], e["jur"], v["side"], won, matter, e["amount"]))

    # Remplacement atomique : l'ancien index disparaît puis réapparaît en une transaction.
    with db.get_conn() as conn:
        conn.execute("DELETE FROM insight_appearances")
        conn.executemany(
            "INSERT OR IGNORE INTO insight_appearances "
            "(name_key, display_name, doc_id, year, juridiction_key, side, won, matter, amount) "
            "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        inserted = conn.total_changes
    out = insight.stats()
    print(f"terminé : {total} chunks, {len(acc)} décisions → {out['lawyers']} avocats, "
          f"{out['appearances']} apparitions ({inserted} insérées)")
    return out


if __name__ == "__main__":
    run()

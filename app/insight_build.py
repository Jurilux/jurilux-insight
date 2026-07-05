"""Construit l'index Insight (avocats) : scanne l'index Meili `chunks`, extrait les avocats
de chaque décision de jurisprudence, remplit la table insight_appearances.

Lancé ponctuellement (après un rafraîchissement du corpus) :
    docker compose run --rm api python -m app.insight_build
"""
import json
import urllib.request

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
    with db.get_conn() as conn:  # reconstruction complète (idempotent)
        conn.execute("DELETE FROM insight_appearances")
    offset = total = inserted = 0
    seen = set()  # (name_key, doc_id) déjà vus (un avocat peut apparaître dans plusieurs chunks d'une décision)
    while True:
        docs = _fetch(offset, BATCH)
        if not docs:
            break
        rows = []
        for d in docs:
            if d.get("source_type") != "jurisprudence" or not d.get("doc_id"):
                continue
            doc_id = d["doc_id"]
            for name in insight.extract_lawyers(d.get("text") or ""):
                k = insight.name_key(name)
                if (k, doc_id) in seen:
                    continue
                seen.add((k, doc_id))
                rows.append((k, name, doc_id, d.get("year"), d.get("juridiction_key")))
        inserted += insight.record_many(rows)
        total += len(docs)
        offset += BATCH
        if offset % 50000 == 0:
            print(f"  {offset} chunks scannés, {inserted} apparitions enregistrées", flush=True)
    out = insight.stats()
    print(f"terminé : {total} chunks scannés → {out['lawyers']} avocats, {out['appearances']} apparitions")
    return out


if __name__ == "__main__":
    run()

"""Indexation des projets/propositions de loi (Chambre des Députés).

Source : dataset data.public.lu « Liste des projets et propositions de lois »
(CC-Zero) — CSV : NATURE, LAW_NUMBER, LAW_TYPE, LAW_DEPOSIT_DATE, LAW_EVACUATION_DATE,
LAW_STATUS, LAW_TITLE, LAW_CONTENT, LAW_AUTHORS.

Indexe un document Meili par texte parlementaire (source_type='projet_loi'), pour que
le corpus couvre aussi le travail législatif en cours/passé. Les textes intégraux
restent sur chd.lu (url du dossier).

Usage (dans le conteneur) : python -m ingest.index_chd
"""
import csv
import hashlib
import io
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.search import _client, ensure_index  # noqa: E402

CSV_URL = ("https://download.data.public.lu/resources/"
           "la-liste-des-projets-propositions-de-lois/20240715-143833/112-texte-loi.csv")
UA = {"User-Agent": "Mozilla/5.0"}
LEGISLATIVE = re.compile(r"loi|reglement|constitution", re.IGNORECASE)


def main() -> None:
    print("Téléchargement du CSV CHD…")
    req = urllib.request.Request(CSV_URL, headers=UA)
    with urllib.request.urlopen(req, timeout=120) as r:
        text = r.read().decode("utf-8-sig", errors="replace")

    ensure_index()
    idx = _client().index(settings.meili_index)

    docs, n = [], 0
    for row in csv.DictReader(io.StringIO(text)):
        ltype = (row.get("LAW_TYPE") or "").strip()
        if not LEGISLATIVE.search(ltype):
            continue  # on garde les textes législatifs, pas les débats/rapports
        num = (row.get("LAW_NUMBER") or "").strip()
        title = (row.get("LAW_TITLE") or "").strip()
        if not num or not title:
            continue
        deposit = (row.get("LAW_DEPOSIT_DATE") or "").strip()
        status = (row.get("LAW_STATUS") or "").strip()
        authors = (row.get("LAW_AUTHORS") or "").strip()
        content = (row.get("LAW_CONTENT") or "").strip()
        ym = re.search(r"(19|20)\d{2}", deposit)
        year = int(ym.group(0)) if ym else None

        body = (f"{title}. Type : {ltype}. Déposé le {deposit or '?'}. "
                f"Statut : {status or '?'}. Auteur(s) : {authors or '?'}. {content}")
        doc_id = f"chd-{num}"
        docs.append({
            "chunk_id": hashlib.sha1(doc_id.encode()).hexdigest()[:16],
            "doc_id": doc_id,
            "text": body,
            "title": title,
            "year": year,
            "juridiction_key": ltype,
            "source_type": "projet_loi",
            "url": f"https://www.chd.lu/fr/dossier/{num}",
            "pdf_url": None,
        })
        n += 1
        if len(docs) >= 1000:
            idx.add_documents(docs); docs = []
            print(f"… {n} textes indexés")
    if docs:
        idx.add_documents(docs)
    print(f"Terminé : {n} projets/propositions de loi envoyés à Meilisearch.")


if __name__ == "__main__":
    main()

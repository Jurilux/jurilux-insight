"""Indexation d'un dossier de PDFs dans Meilisearch.

Usage :
  python -m ingest.index_pdfs /data/pdfs                      # jurisprudence
  python -m ingest.index_pdfs /data/laws --source-type law \
         --pdf-base-url https://legilux.public.lu/filestore/eli/...

Conventions doc_id :
- jurisprudence : nom du fichier sans .pdf ; le front construit /docs/<doc_id>.pdf,
  donc les PDFs DOIVENT rester servis depuis /data/pdfs par Caddy.
- law : nom de fichier ELI (ex. eli-etat-leg-loi-2018-07-28-a630-consolide-20250101-fr-pdf.pdf) ;
  pdf_url absolue requise (--pdf-base-url ou metadata.jsonl).

Métadonnées :
- year / juridiction_key sont déduits du nom de fichier si possible
  (motifs : <juridiction>_..._<YYYY>... ; ex. csj_ch03_2019_12345.pdf).
- Un fichier metadata.jsonl optionnel dans le dossier peut tout surcharger :
  {"file": "x.pdf", "doc_id": "...", "year": 2019, "juridiction_key": "csj_ch03",
   "title": "...", "url": "...", "pdf_url": "..."}
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.search import _client, ensure_index  # noqa: E402
from ingest.chunking import chunk_text, pdf_to_text  # noqa: E402

KNOWN_JURIDICTIONS = re.compile(
    r"^(cassation|csj_ch\d{2}|csj_conseil|csj)", re.IGNORECASE
)
YEAR_RE = re.compile(r"(19|20)\d{2}")


def infer_meta(filename: str) -> dict:
    stem = filename.removesuffix(".pdf")
    meta: dict = {"doc_id": stem}
    m = KNOWN_JURIDICTIONS.match(stem)
    if m:
        meta["juridiction_key"] = m.group(1).lower()
    y = YEAR_RE.search(stem)
    if y:
        meta["year"] = int(y.group(0))
    return meta


def load_sidecar(folder: Path) -> dict[str, dict]:
    sidecar = folder / "metadata.jsonl"
    out: dict[str, dict] = {}
    if sidecar.exists():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out[row["file"]] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--source-type", choices=["jurisprudence", "law"], default="jurisprudence")
    ap.add_argument("--pdf-base-url", default=None,
                    help="Base URL pour pdf_url absolue (source-type=law)")
    ap.add_argument("--batch-size", type=int, default=1000)
    args = ap.parse_args()

    ensure_index()
    idx = _client().index(settings.meili_index)
    sidecar = load_sidecar(args.folder)

    docs: list[dict] = []
    n_files = n_chunks = 0
    pdfs = sorted(args.folder.glob("*.pdf"))
    if not pdfs:
        print(f"Aucun PDF dans {args.folder}", file=sys.stderr)
        sys.exit(1)

    for pdf in pdfs:
        meta = infer_meta(pdf.name)
        meta.update(sidecar.get(pdf.name, {}))
        meta.pop("file", None)
        if args.source_type == "law" and "pdf_url" not in meta and args.pdf_base_url:
            meta["pdf_url"] = args.pdf_base_url.rstrip("/") + "/" + pdf.name

        try:
            text = pdf_to_text(pdf)
        except Exception as e:  # PDF corrompu : on continue
            print(f"SKIP {pdf.name}: {e}", file=sys.stderr)
            continue

        for i, piece in enumerate(chunk_text(text)):
            cid = hashlib.sha1(f"{meta['doc_id']}:{i}".encode()).hexdigest()[:16]
            docs.append({
                "chunk_id": cid,
                "text": piece,
                "source_type": args.source_type,
                **meta,
            })
            n_chunks += 1
        n_files += 1

        if len(docs) >= args.batch_size:
            idx.add_documents(docs)
            docs = []
            print(f"... {n_files} fichiers, {n_chunks} chunks indexés")

    if docs:
        idx.add_documents(docs)
    print(f"Terminé : {n_files} fichiers, {n_chunks} chunks envoyés à Meilisearch "
          f"(indexation asynchrone, suivre avec /tasks).")


if __name__ == "__main__":
    main()

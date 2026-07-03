"""Téléchargement de textes Legilux (best effort).

Le corpus d'origine est perdu ; ce script reconstruit la partie « lois » à partir
d'une liste d'URLs ELI Legilux (filestore), une par ligne, ex. :
  https://legilux.public.lu/filestore/eli/etat/leg/loi/2018/07/28/a630/consolide/20250101/fr/pdf/eli-etat-leg-loi-2018-07-28-a630-consolide-20250101-fr-pdf.pdf

Usage :
  python -m ingest.fetch_legilux urls.txt /data/laws
  puis : python -m ingest.index_pdfs /data/laws --source-type law

Le nom de fichier ELI est conservé tel quel : c'est le doc_id attendu par le
front (lawTitle() le parse), et pdf_url est réécrite vers l'URL d'origine via
metadata.jsonl généré ici.

NOTE : la liste d'URLs reste à constituer (codes usuels : travail, civil,
pénal, NCPC, sociétés...). data.legilux.public.lu (SPARQL) peut aider à
l'énumérer — à valider.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    urls_file, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    sidecar = out_dir / "metadata.jsonl"

    urls = [u.strip() for u in urls_file.read_text().splitlines() if u.strip() and not u.startswith("#")]
    with sidecar.open("a", encoding="utf-8") as meta:
        for url in urls:
            name = url.rsplit("/", 1)[-1]
            dest = out_dir / name
            if dest.exists():
                print(f"déjà présent : {name}")
                continue
            try:
                # Legilux (Apache/WAF) renvoie 403 sur un User-Agent non navigateur.
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
                    "Accept": "application/pdf,*/*",
                })
                with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:
                    f.write(r.read())
                meta.write(json.dumps({"file": name, "pdf_url": url, "url": url}) + "\n")
                print(f"OK {name}")
                time.sleep(1)  # politesse
            except Exception as e:
                print(f"ECHEC {url}: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()

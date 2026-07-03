"""Téléchargement de jurisprudence depuis le portail open-data data.public.lu.

Les décisions (pseudonymisées RGPD, licence ouverte, org « Administration
judiciaire ») sont publiées en ZIP par année, un dataset par juridiction/chambre.
Ce script télécharge les zips d'un ou plusieurs datasets et extrait les PDFs
dans un dossier, avec un nom de fichier ASCII propre = doc_id attendu par le front
(/docs/<doc_id>.pdf servi par Caddy depuis /data/pdfs).

Usage :
  python -m ingest.fetch_jurisprudence /data/pdfs cour-de-cassation-1 [autres-slugs...]
  python -m ingest.fetch_jurisprudence /data/pdfs --min-year 2015 cour-de-cassation-1
  puis : python -m ingest.index_pdfs /data/pdfs        # source-type=jurisprudence

stdlib uniquement (urllib/zipfile) — se lance sur l'hôte sans dépendances.
"""
import io
import json
import os
import re
import sys
import urllib.request
import zipfile

UA = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "*/*",
}
API = "https://data.public.lu/api/1/datasets/{}/"


def sanitize(basename: str) -> str:
    name = os.path.basename(basename)
    name = name.replace("_pseudonymisé-accessible", "").replace("_pseudonymise-accessible", "")
    name = name.encode("ascii", "ignore").decode("ascii")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name


def fetch(url: str, timeout: int = 240) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def main() -> None:
    args = sys.argv[1:]
    min_year = 0
    if "--min-year" in args:
        i = args.index("--min-year")
        min_year = int(args[i + 1])
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        sys.exit(1)
    out_dir, slugs = args[0], args[1:]
    os.makedirs(out_dir, exist_ok=True)

    total = 0
    for slug in slugs:
        try:
            meta = json.loads(fetch(API.format(slug), timeout=60))
        except Exception as e:
            print(f"SKIP dataset {slug}: {e}", file=sys.stderr)
            continue
        n_slug = 0
        for r in meta.get("resources", []):
            title = r.get("title", "") or ""
            m = re.search(r"(19|20)\d{2}", title)
            if m and int(m.group(0)) < min_year:
                continue
            try:
                z = zipfile.ZipFile(io.BytesIO(fetch(r.get("url"))))
            except Exception as e:
                print(f"  SKIP zip {slug}/{title}: {e}", file=sys.stderr)
                continue
            for name in z.namelist():
                if not name.lower().endswith(".pdf"):
                    continue
                with open(os.path.join(out_dir, sanitize(name)), "wb") as f:
                    f.write(z.read(name))
                n_slug += 1
        total += n_slug
        print(f"OK {slug} : {n_slug} PDFs")
    print(f"TOTAL : {total} PDFs extraits dans {out_dir}")


if __name__ == "__main__":
    main()

"""Corpus législatif Legilux exhaustif (consolidé) via le SPARQL JOLux.

Récupère toutes les URLs de PDF FR de la législation CONSOLIDÉE (lois, RGD) et des
CODES, ne garde que la version la plus récente de chaque texte (dédup par ELI de base),
télécharge les PDF dans out_dir et écrit metadata.jsonl (pdf_url = URL Legilux d'origine).
Puis : python -m ingest.index_pdfs <out_dir> --source-type law

SPARQL : POST https://data.legilux.public.lu/sparqlendpoint (JOLux : Manifestation
-> jolux:isExemplifiedBy -> URL fichier). stdlib only.

Usage : python3 ingest/fetch_legilux_full.py /data/laws
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"}
EP = "https://data.legilux.public.lu/sparqlendpoint"
JOLUX = "http://data.legilux.public.lu/resource/ontology/jolux#"


def sparql(where: str) -> list:
    q = (f"PREFIX jolux: <{JOLUX}> SELECT DISTINCT ?u WHERE {{ "
         f"?m jolux:isExemplifiedBy ?u FILTER({where}) }}")
    data = urllib.parse.urlencode({"query": q}).encode()
    req = urllib.request.Request(
        EP, data=data, method="POST",
        headers={**UA, "Accept": "application/sparql-results+json",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=120) as r:
        res = json.loads(r.read())
    return [b["u"]["value"] for b in res["results"]["bindings"]]


def base_date(url: str):
    """(clé de base sans la date, date AAAAMMJJ) pour dédupliquer sur la dernière version."""
    p = url.split("/")
    if "consolide" in p:
        i = p.index("consolide")
        return "/".join(p[:i]), p[i + 1]
    if "code" in p:
        i = p.index("code")
        return "/".join(p[:i + 2]), p[i + 2] if i + 2 < len(p) else "0"
    return url, "0"


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "/data/laws"
    os.makedirs(out_dir, exist_ok=True)

    print("SPARQL : énumération des PDF consolidés + codes…")
    urls = set(sparql('CONTAINS(STR(?u),"/consolide/") && CONTAINS(STR(?u),"/fr/pdf")'))
    urls |= set(sparql('CONTAINS(STR(?u),"/leg/code/") && CONTAINS(STR(?u),"/fr/pdf")'))
    print(f"{len(urls)} URLs brutes")

    # dédup : garder la date max par texte de base
    latest: dict = {}
    for u in urls:
        base, d = base_date(u)
        if base not in latest or d > latest[base][0]:
            latest[base] = (d, u)
    chosen = [v[1] for v in latest.values()]
    print(f"{len(chosen)} textes après dédup (dernière version de chaque)")

    sidecar = os.path.join(out_dir, "metadata.jsonl")
    n = 0
    with open(sidecar, "a", encoding="utf-8") as meta:
        for u in chosen:
            name = u.rsplit("/", 1)[-1]
            dest = os.path.join(out_dir, name)
            if os.path.exists(dest):
                continue
            try:
                req = urllib.request.Request(u, headers={**UA, "Accept": "application/pdf,*/*"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    blob = r.read()
                if not blob.startswith(b"%PDF"):
                    print(f"SKIP non-PDF {name}", file=sys.stderr); continue
                with open(dest, "wb") as f:
                    f.write(blob)
                meta.write(json.dumps({"file": name, "pdf_url": u, "url": u}) + "\n")
                n += 1
                if n % 100 == 0:
                    print(f"… {n} PDF téléchargés")
            except Exception as e:
                print(f"ECHEC {u}: {e}", file=sys.stderr)
    print(f"TOTAL : {n} PDF téléchargés dans {out_dir}")


if __name__ == "__main__":
    main()

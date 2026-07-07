"""Sonde d'extraction — identifie les DONNÉES EXTRACTIBLES d'une décision et mesure leur
COUVERTURE sur un échantillon. Sert à décider quoi ajouter à l'index insight / à l'export.

Deux modes :
  python -m ingest.extraction_probe                 # échantillon intégré (format LU réel)
  python -m ingest.extraction_probe --meili [N]     # échantillon RÉEL : N chunks du corpus Meili

Pour chaque champ candidat : % de décisions où il est extrait déterministiquement (regex/heuristique,
zéro LLM), + 2 exemples. Aucun magistrat/greffier n'est ciblé (RGPD/CNPD).

Décisions LU pseudonymisées (data.public.lu) : identités masquées en « PERSONNE1) », « SOCIÉTÉ2) » —
ce qui est extractible ce sont les STRUCTURES (rôles, montants, articles, dispositif), pas les identités
des parties (déjà anonymisées à la source).
"""
import json
import re
import sys
import urllib.request

from app import insight
from app.config import settings

# ---------- champs candidats déjà en production (via app.insight) ----------
def f_lawyers(t):   return list(insight.parse_chunk(t)["lawyers"].keys()) or None
def f_side(t):      return next((v["side"] for v in insight.parse_chunk(t)["lawyers"].values() if v["side"]), None)
def f_outcome(t):   return insight.parse_chunk(t)["outcome"]
def f_firm(t):      return next((v.get("firm") for v in insight.parse_chunk(t)["lawyers"].values() if v.get("firm")), None)
def f_amount(t):    return insight.extract_amount(t)
def f_matter(t):
    from collections import Counter
    c = Counter(); insight.matter_hits(t, c); return c.most_common(1)[0][0] if c else None

# ---------- champs candidats NOUVEAUX (à évaluer pour ajout à l'index / l'export) ----------
_CASE = re.compile(r"\bCAS-\d{4}-\d{5}\b|\b(?:n[°o]|num[ée]ro)\s*\d{2,6}(?:/\d{2,4})?\s+du\s+r[ôo]le\b", re.I)
_HEARING = re.compile(r"audience publique\s+(?:du|de)\s+(\d{1,2}\s+\w+\s+\d{4})", re.I)
_DECTYPE = re.compile(r"\b(arr[êe]t|jugement|ordonnance)\b", re.I)
_APPEAL = re.compile(r"\b(confirme|r[ée]forme|casse|rejette le pourvoi|d[ée]clare irrecevable)\b", re.I)
_ARTICLE = re.compile(r"\barticle[s]?\s+[LR]?\.?\s*\d+[\d\-.]*", re.I)
_PARTY = re.compile(r"\b(?:PERSONNE|SOCI[EÉ]T[EÉ]|ADMINISTRATION)\s*\d+\b")
_DEPENS = re.compile(r"\bd[ée]pens\b|\bfrais et d[ée]pens\b", re.I)
_EXEC = re.compile(r"ex[ée]cution provisoire", re.I)
_APPELANT = re.compile(r"\b(appelant|intim[ée]|demandeur|d[ée]fendeur|requ[ée]rant)\b", re.I)

def f_case(t):     m = _CASE.search(t);    return m.group(0) if m else None
def f_hearing(t):  m = _HEARING.search(t); return m.group(1) if m else None
def f_dectype(t):  m = _DECTYPE.search(t); return m.group(1).lower() if m else None
def f_appeal(t):   m = _APPEAL.search(t);  return m.group(1).lower() if m else None
def f_articles(t): a = _ARTICLE.findall(t); return a or None
def f_parties(t):  p = _PARTY.findall(t);  return p or None
def f_depens(t):   return True if _DEPENS.search(t) else None
def f_exec(t):     return True if _EXEC.search(t) else None
def f_roles(t):    r = _APPELANT.findall(t); return r or None

CHAMPS = [
    ("avocats (prod)", f_lawyers), ("côté A/B (prod)", f_side), ("issue estimée (prod)", f_outcome),
    ("matière (prod)", f_matter), ("montant € (prod)", f_amount), ("cabinet (prod)", f_firm),
    ("— nouveaux —", None),
    ("n° d'affaire / rôle", f_case), ("date d'audience", f_hearing), ("type (arrêt/jugt)", f_dectype),
    ("sens (confirme/casse…)", f_appeal), ("articles visés", f_articles), ("rôles des parties", f_roles),
    ("parties pseudonymisées", f_parties), ("dépens", f_depens), ("exécution provisoire", f_exec),
]

# Échantillon intégré — extraits ANONYMISÉS au format réel des décisions luxembourgeoises.
SAMPLE = [
    "Numéro CAS-2023-00116 du registre. Audience publique du 14 mars 2024. La Cour de cassation, "
    "statuant sur le pourvoi de PERSONNE1) contre SOCIÉTÉ2), assistée de Maître Jean DUPONT, de l'Étude "
    "WEBER & ASSOCIÉS, avocat à la Cour. Vu les articles L.124-10 du Code du travail et 1134 du Code civil. "
    "PAR CES MOTIFS, la Cour rejette le pourvoi et condamne PERSONNE1) aux frais et dépens.",
    "Numéro 45123 du rôle. Tribunal du travail. Entre PERSONNE1), demandeur, représenté par Maître Anne "
    "MARTIN, avocat à la Cour, et SOCIÉTÉ2), défenderesse. Le tribunal condamne la défenderesse à payer au "
    "demandeur la somme de 12.345,67 € à titre d'indemnité, avec exécution provisoire, et aux dépens.",
    "Audience publique du 2 février 2023. La Cour d'appel, huitième chambre, dans l'affaire entre "
    "PERSONNE1), appelant, assisté de Maître Lex THIELEN du cabinet SCHMIT, et PERSONNE2), intimé. "
    "PAR CES MOTIFS, confirme le jugement entrepris. Vu l'article 579 du Nouveau Code de procédure civile.",
    "Ordonnance de référé. PERSONNE1) contre ADMINISTRATION3). Le juge dit la demande non fondée et "
    "déboute PERSONNE1). Condamne le requérant aux dépens de l'instance.",
    "Numéro CAS-2022-00089 du registre. La Cour casse et annule l'arrêt attaqué. Maître Guy CASTEGNARO, "
    "avocat à la Cour, pour la partie demanderesse. Condamne SOCIÉTÉ2) à payer 250.000 € de dommages-intérêts.",
    "Jugement du tribunal d'arrondissement. En matière de bail à loyer, entre PERSONNE1), bailleur, et "
    "PERSONNE2), preneur. Prononce la résiliation du bail et condamne le preneur à payer 3.600 € d'arriérés "
    "de loyer. Réforme partiellement la décision de première instance.",
]


def _meili_sample(n: int) -> list:
    url = (f"{settings.meili_url}/indexes/{settings.meili_index}/documents"
           f"?limit={n}&fields=text,source_type")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + (settings.meili_master_key or "")})
    with urllib.request.urlopen(req, timeout=60) as r:
        docs = json.loads(r.read()).get("results", [])
    return [d.get("text", "") for d in docs if d.get("source_type") == "jurisprudence" and d.get("text")]


def probe(texts: list) -> None:
    n = len(texts)
    print(f"Échantillon : {n} extraits de jurisprudence.\n")
    print(f"{'CHAMP':<28}{'COUVERTURE':>12}   EXEMPLES")
    print("-" * 78)
    for label, fn in CHAMPS:
        if fn is None:
            print(f"\n{label}")
            continue
        hits, examples = 0, []
        for t in texts:
            v = fn(t)
            if v:
                hits += 1
                if len(examples) < 2:
                    s = v if isinstance(v, (str, bool)) else (v[0] if isinstance(v, list) else v)
                    examples.append(str(s)[:34])
        pct = round(100 * hits / n) if n else 0
        bar = "█" * (pct // 8)
        print(f"{label:<28}{hits:>3}/{n} {pct:>3}% {bar:<13} {' · '.join(examples)}")


def main() -> None:
    if "--meili" in sys.argv:
        i = sys.argv.index("--meili")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 500
        try:
            texts = _meili_sample(n)
            print(f"[corpus RÉEL : {len(texts)} chunks depuis Meili]\n")
        except Exception as e:
            print(f"Meili indisponible ({e}) → échantillon intégré.\n")
            texts = SAMPLE
    else:
        texts = SAMPLE
    probe(texts)


if __name__ == "__main__":
    main()

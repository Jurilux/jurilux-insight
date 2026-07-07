"""Cœur du moteur : modèle de cas d'usage, exécution multi-profils, évaluation, rapport.

Un **cas d'usage** décrit une interaction (méthode + chemin + corps) et, pour **chaque
profil**, l'attente (succès 2xx, refus d'autorisation avec code précis, ou refus gracieux
quota). Le moteur exécute cas × profils, compare l'obtenu à l'attendu, et le rapport agrège
un verdict **par fonctionnalité**.
"""
from __future__ import annotations

import contextlib
import json as _json
from dataclasses import dataclass, field
from typing import Callable, Optional

from .banc import LIBELLES, Banc


@contextlib.contextmanager
def _appliquer_stubs(stubs):
    """Applique une liste de (objet, attribut, valeur) le temps d'une requête, puis restaure.
    Permet de tester les branches d'erreur (aucun résultat, Meili en panne, fichier trop
    gros, rate-limit…) sans polluer le reste du run."""
    sauvegarde = []
    try:
        for obj, attr, val in (stubs or []):
            sauvegarde.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, val)
        yield
    finally:
        for obj, attr, val in reversed(sauvegarde):
            setattr(obj, attr, val)


# --------- attentes (critères de succès par profil) ---------
@dataclass
class Attente:
    genre: str                                   # "ok" | "refuse" | "gracieux"
    code: Optional[int] = None                   # code HTTP attendu pour "refuse"
    verif: Optional[Callable[[dict], bool]] = None  # prédicat sur le JSON pour "ok"
    libelle: str = ""


def ok(verif: Optional[Callable[[dict], bool]] = None, libelle: str = "2xx") -> Attente:
    return Attente("ok", None, verif, libelle)


def refuse(code: int) -> Attente:
    return Attente("refuse", code, None, f"HTTP {code}")


def gracieux() -> Attente:
    """200 mais réponse refusée (ex. quota épuisé, aucun extrait) — jamais un 500."""
    return Attente("gracieux", 200, None, "refus gracieux")


# --------- cas d'usage ---------
@dataclass
class CasUsage:
    id: str
    fonctionnalite: str
    description: str                             # traçabilité vers la doc (contrat d'API)
    methode: str
    chemin: str
    profils: dict                               # nom_profil -> Attente
    corps: Optional[object] = None              # dict, ou callable(ctx)->dict
    contenu: Optional[bytes] = None             # corps brut (upload Vault)
    entetes: Optional[dict] = None              # en-têtes supplémentaires (ex. Content-Type)
    # preparer(banc, nom_profil) -> (headers, ctx). Par défaut : profil compte du banc.
    preparer: Optional[Callable[[Banc, str], tuple]] = None
    stubs: Optional[list] = None                # (objet, attr, valeur) le temps de la requête


@dataclass
class Resultat:
    cas: str
    fonctionnalite: str
    profil: str
    attendu: str
    obtenu: str
    ok: bool
    detail: str = ""


# --------- parcours utilisateur (séquence d'étapes enchaînées, état partagé) ---------
@dataclass
class Etape:
    """Une étape d'un parcours : une action + son attente. Peut agir sous un acteur donné
    (clé d'en-têtes dans le contexte) et **capturer** des valeurs de la réponse pour les
    étapes suivantes (`capture(json, ctx)`)."""
    libelle: str
    methode: str
    chemin: str
    attente: Attente
    corps: Optional[object] = None
    contenu: Optional[bytes] = None
    entetes: Optional[dict] = None
    acteur: str = "headers"                       # clé d'en-têtes dans le contexte
    role: str = ""                                # libellé d'affichage de l'acteur
    capture: Optional[Callable[[dict, dict], None]] = None
    stubs: Optional[list] = None                  # (objet, attr, valeur) le temps de l'étape


@dataclass
class Parcours:
    """Un parcours utilisateur réaliste : un objectif clair et une séquence d'étapes qui
    partagent un contexte (état transmis d'une étape à l'autre)."""
    id: str
    objectif: str
    profil: str                                   # profil compte de départ
    etapes: list
    preparer: Optional[Callable] = None           # (banc) -> ctx ; défaut : profil compte


# --------- exécution ---------
class Moteur:
    def executer(self, banc: Banc, cas_list: list) -> list:
        resultats: list = []
        for cas in cas_list:
            for nom, attente in cas.profils.items():
                resultats.append(self._executer_un(banc, cas, nom, attente))
        return resultats

    def _executer_un(self, banc: Banc, cas: CasUsage, nom: str, attente: Attente) -> Resultat:
        try:
            headers, ctx = (cas.preparer(banc, nom) if cas.preparer else banc.profil(nom))
        except Exception as e:  # échec de provisionnement = échec du cas
            return Resultat(cas.id, cas.fonctionnalite, nom, attente.libelle,
                            "préparation KO", False, f"{type(e).__name__}: {e}")
        try:
            reponse = self._envoyer(banc, cas, headers, ctx)
        except Exception as e:
            return Resultat(cas.id, cas.fonctionnalite, nom, attente.libelle,
                            "requête KO", False, f"{type(e).__name__}: {e}")
        return self._evaluer(cas, nom, attente, reponse)

    def _envoyer(self, banc: Banc, cas: CasUsage, headers: dict, ctx: dict):
        with _appliquer_stubs(cas.stubs):
            chemin = cas.chemin.format(**ctx) if "{" in cas.chemin else cas.chemin
            if cas.contenu is not None:
                return banc.client.request(cas.methode, chemin, content=cas.contenu,
                                           headers={**headers, **(cas.entetes or {})})
            corps = cas.corps(ctx) if callable(cas.corps) else cas.corps
            if corps is not None:
                return banc.client.request(cas.methode, chemin, json=corps, headers=headers)
            return banc.client.request(cas.methode, chemin, headers=headers)

    def _evaluer(self, cas: CasUsage, nom: str, attente: Attente, reponse) -> Resultat:
        ok_v, obtenu, detail = _verdict(attente, reponse)
        return Resultat(cas.id, cas.fonctionnalite, nom, attente.libelle, obtenu, ok_v, detail)

    # ---- parcours ----
    def executer_parcours(self, banc: Banc, parcours_list: list) -> list:
        resultats: list = []
        for p in parcours_list:
            fonc = f"{p.id} · {p.objectif}"
            try:
                ctx = p.preparer(banc) if p.preparer else self._ctx_defaut(banc, p.profil)
            except Exception as e:
                resultats.append(Resultat("(montage)", fonc, LIBELLES.get(p.profil, p.profil),
                                          "préparation OK", "préparation KO", False,
                                          f"{type(e).__name__}: {e}"))
                continue
            for et in p.etapes:
                resultats.append(self._executer_etape(banc, p, fonc, ctx, et))
        return resultats

    def _ctx_defaut(self, banc: Banc, profil: str) -> dict:
        headers, ctx = banc.profil(profil)
        return {"headers": headers, **ctx}

    def _executer_etape(self, banc: Banc, p: Parcours, fonc: str, ctx: dict, et: Etape) -> Resultat:
        role = et.role or LIBELLES.get(p.profil, p.profil)
        headers = ctx.get(et.acteur, {})
        try:
            with _appliquer_stubs(et.stubs):
                chemin = et.chemin.format(**ctx) if "{" in et.chemin else et.chemin
                if et.contenu is not None:
                    reponse = banc.client.request(et.methode, chemin, content=et.contenu,
                                                  headers={**headers, **(et.entetes or {})})
                else:
                    corps = et.corps(ctx) if callable(et.corps) else et.corps
                    reponse = (banc.client.request(et.methode, chemin, json=corps, headers=headers)
                               if corps is not None
                               else banc.client.request(et.methode, chemin, headers=headers))
        except Exception as e:
            return Resultat(et.libelle, fonc, role, et.attente.libelle, "requête KO", False,
                            f"{type(e).__name__}: {e}")
        ok_v, obtenu, detail = _verdict(et.attente, reponse)
        if ok_v and et.capture is not None:  # transmet l'état aux étapes suivantes
            try:
                et.capture(reponse.json(), ctx)
            except Exception as e:
                return Resultat(et.libelle, fonc, role, et.attente.libelle, obtenu, False,
                                f"capture: {type(e).__name__}: {e}")
        return Resultat(et.libelle, fonc, role, et.attente.libelle, obtenu, ok_v, detail)


def _verdict(attente: Attente, reponse) -> tuple:
    """Compare une réponse à une attente → (ok, obtenu, detail). Partagé matrice/parcours."""
    code = reponse.status_code
    try:
        corps = reponse.json()
    except Exception:
        corps = None
    obtenu = f"HTTP {code}"

    if attente.genre == "refuse":
        return code == attente.code, obtenu, ("" if code == attente.code else f"attendu {attente.code}")

    if attente.genre == "gracieux":
        reussi = code == 200 and isinstance(corps, dict) and corps.get("refused") is True
        return reussi, obtenu + (" refusé" if isinstance(corps, dict) and corps.get("refused") else ""), \
            ("" if reussi else "attendu 200 + refused=true")

    # genre "ok"
    if not (200 <= code < 300):
        return False, obtenu, f"corps: {_court(corps)}"
    if attente.verif is not None:
        try:
            if not bool(attente.verif(corps)):
                return False, obtenu + " (vérif KO)", f"corps: {_court(corps)}"
        except Exception as e:
            return False, obtenu, f"vérif: {type(e).__name__}"
    return True, obtenu, ""


def _court(corps) -> str:
    s = _json.dumps(corps, ensure_ascii=False) if corps is not None else "∅"
    return s if len(s) <= 120 else s[:117] + "…"


# --------- rapport ---------
class Rapport:
    def __init__(self, resultats: list) -> None:
        self.resultats = resultats

    def echecs(self) -> list:
        return [r for r in self.resultats if not r.ok]

    def code_sortie(self) -> int:
        return 1 if self.echecs() else 0

    def par_fonctionnalite(self) -> dict:
        """fonctionnalite -> {total, ok, reussie, cas:[...]}."""
        agg: dict = {}
        for r in self.resultats:
            f = agg.setdefault(r.fonctionnalite, {"total": 0, "ok": 0, "resultats": []})
            f["total"] += 1
            f["ok"] += 1 if r.ok else 0
            f["resultats"].append(r)
        for f in agg.values():
            f["reussie"] = f["ok"] == f["total"]
        return agg

    def texte(self) -> str:
        agg = self.par_fonctionnalite()
        lignes = [f"MOTEUR DE TESTS FONCTIONNELS — {len(self.resultats)} assertions "
                  f"({len(agg)} fonctionnalités)", ""]
        for fonc, data in agg.items():
            etat = "✅ RÉUSSIE" if data["reussie"] else "❌ ÉCHEC"
            lignes.append(f"{etat}  {fonc}  ({data['ok']}/{data['total']})")
            # une ligne par cas, avec le verdict de chaque profil
            par_cas: dict = {}
            for r in data["resultats"]:
                par_cas.setdefault(r.cas, []).append(r)
            for cas_id, rs in par_cas.items():
                cellules = []
                for r in rs:
                    marque = "✓" if r.ok else "✗"
                    lib = LIBELLES.get(r.profil, r.profil)
                    detail = "" if r.ok else f"[{r.obtenu}≠{r.attendu}]"
                    cellules.append(f"{lib}:{r.attendu}{marque}{detail}")
                lignes.append(f"    · {cas_id:<28} " + "  ".join(cellules))
        ech = self.echecs()
        lignes += ["", f"Résultat : {len(self.resultats) - len(ech)}/{len(self.resultats)} "
                       f"assertions vertes — {len(ech)} échec(s)"]
        if ech:
            lignes.append("Échecs :")
            for r in ech:
                lignes.append(f"  ✗ {r.fonctionnalite} / {r.cas} / {LIBELLES.get(r.profil, r.profil)} "
                              f"— attendu {r.attendu}, obtenu {r.obtenu}. {r.detail}")
        return "\n".join(lignes)

    def markdown(self) -> str:
        agg = self.par_fonctionnalite()
        out = [f"# Rapport — moteur de tests fonctionnels", "",
               f"**{len(self.resultats) - len(self.echecs())}/{len(self.resultats)}** assertions vertes.", ""]
        for fonc, data in agg.items():
            etat = "✅" if data["reussie"] else "❌"
            out.append(f"## {etat} {fonc} ({data['ok']}/{data['total']})")
            out.append("")
            out.append("| Cas | Profil | Attendu | Obtenu | OK |")
            out.append("|---|---|---|---|:--:|")
            for r in data["resultats"]:
                out.append(f"| {r.cas} | {LIBELLES.get(r.profil, r.profil)} | {r.attendu} "
                           f"| {r.obtenu} | {'✓' if r.ok else '✗'} |")
            out.append("")
        return "\n".join(out)

    def json(self) -> str:
        agg = self.par_fonctionnalite()
        data = {
            "total": len(self.resultats),
            "verts": len(self.resultats) - len(self.echecs()),
            "code_sortie": self.code_sortie(),
            "fonctionnalites": {
                fonc: {"reussie": d["reussie"], "ok": d["ok"], "total": d["total"]}
                for fonc, d in agg.items()
            },
            "resultats": [
                {"fonctionnalite": r.fonctionnalite, "cas": r.cas, "profil": r.profil,
                 "attendu": r.attendu, "obtenu": r.obtenu, "ok": r.ok, "detail": r.detail}
                for r in self.resultats
            ],
        }
        return _json.dumps(data, ensure_ascii=False, indent=2)

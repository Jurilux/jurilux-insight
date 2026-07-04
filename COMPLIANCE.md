# Conformité — licéité & RGPD (risque n°1)

> État : **à valider par un juriste / la CNPD avant tout lancement payant.** Ce document
> cadre les faits techniques et les questions ouvertes ; il ne constitue pas un avis juridique.

## 1. Sources et licences

| Source | Contenu | Accès | Licence |
|---|---|---|---|
| **data.public.lu** (org *Administration judiciaire*) | Jurisprudence : Cassation, CSJ, tribunaux d'arrondissement, justices de paix — **PDF pseudonymisés (RGPD)** | Open-data, API udata, sans anti-bot | Ouverte (type CC BY / BY-ND — **à confirmer par dataset**) |
| **Legilux** (`legilux.public.lu/filestore`) | Textes de loi consolidés (codes) | HTTP (UA navigateur) | Réutilisation de l'information du secteur public |

Nota : `justice.public.lu` est protégé (anti-bot) et **n'est pas** la source utilisée — on
consomme exclusivement les jeux de données **publiés en open-data** par la Justice.

## 2. Ce qui est déjà en place (réduit le risque)

- **Pseudonymisation à la source** : les décisions sont publiées pseudonymisées par la Justice
  (conformité RGPD assumée côté producteur). On ne ré-identifie pas, on ne croise pas.
- **Non-altération** : les PDF sont servis tels quels (`/docs/<doc_id>.pdf`) ; le texte extrait
  sert uniquement à la recherche/synthèse, la source d'origine reste vérifiable en un clic.
- **Anti-hallucination** : refus explicite plutôt qu'invention → pas de « faux droit » attribué.
- **Disclaimer** : « ne constitue pas un avis juridique » présent côté front.
- **Souveraineté** : hébergement UE (OVH), same-origin, pas de transfert des requêtes hors UE
  côté données (le LLM Anthropic reçoit la question + extraits — voir §5).

## 3. Questions ouvertes à trancher (gate)

1. **Licence exacte de chaque dataset** : confirmer BY vs BY-ND (redistribution commerciale ?
   œuvre dérivée ? la synthèse générée est-elle une « modification » au sens de la licence ?).
2. **Réutilisation commerciale** : un SaaS payant qui restitue extraits + synthèse est-il couvert
   par la licence open-data / la loi PSI ? Conditions d'attribution précises.
3. **RGPD / CNPD** : rôle (responsable de traitement ?) pour l'indexation de décisions
   pseudonymisées ; base légale ; DPA avec Anthropic (sous-traitant) ; registre des traitements.
4. **Requêtes utilisateurs** : une question peut contenir des données personnelles → politique de
   rétention/anonymisation des logs de requêtes (voir §5).

## 4. Obligations produit qui en découlent (à implémenter)

- [x] **Attribution** visible : source (Justice / data.public.lu, Legilux) + mention licence.
- [x] **Périmètre du corpus** affiché (une réponse juste sur un corpus troué reste trompeuse).
- [ ] **CGU** + politique de confidentialité (rétention des requêtes, droits RGPD, contact DPO).
- [ ] **Signalement** d'erreur de pseudonymisation (canal de contact, comme le prévoit la Justice).
- [ ] Registre des traitements + DPA Anthropic signé avant ouverture payante.

## 5. Flux de données (pour l'analyse RGPD)

```
Utilisateur ──question──> Caddy (VPS OVH, UE) ──> FastAPI
   FastAPI ──recherche──> Meilisearch (VPS, UE)        [corpus local]
   FastAPI ──question + extraits──> API Anthropic       [sous-traitant LLM]
   FastAPI ──réponse + citations──> Utilisateur
```
- À décider : durée de conservation des questions (défaut recommandé : ne pas logger le contenu,
  ou rétention courte + purge) ; localisation du traitement Anthropic ; DPA.

## 6. Prochaines actions

1. Récupérer la licence exacte par dataset via l'API (`license`/`license_id`) et l'archiver.
2. Consultation juriste / CNPD sur §3.
3. Publier CGU + politique de confidentialité avant d'activer les comptes/facturation (V2).

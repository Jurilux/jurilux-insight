# POSITIONING — Jurilux

Positionnement produit **précis**, dérivé d'une veille concurrentielle sourcée (juillet 2026).
Document de référence stratégique — à confronter au réel et à mettre à jour, comme `CLAUDE.md`
l'est pour l'architecture.

> ⚠️ La souveraineté (hébergement UE + modèle européen) est une **condition d'entrée**, pas un
> différenciateur : le concurrent direct (Alizé) l'a déjà. Le positionnement s'ancre sur ce
> qu'Alizé n'offre pas et que Jurilux a **déjà livré** : **vérifiabilité radicale + insight
> contentieux + option air-gap**.

## 1. Cible prioritaire
**Cabinets d'avocats luxembourgeois — petits et moyens.**
Rationnel : Alizé cible explicitement les **2-3 plus gros cabinets + l'État** (500-600
professionnels visés). Ça **laisse le flanc** des petits/moyens cabinets, plus sensibles au
**coût**, au **contrôle des données** (secret professionnel) et à la **transparence des
sources** — exactement les points forts de Jurilux. Extensions naturelles ensuite :
juristes d'entreprise, administrations.

## 2. Le concurrent qui compte : Alizé (LU/BE)
Jumeau direct, plus avancé sur le terrain SaaS (état mi-2026, sourcé §7) :
- Couverture **UE + Luxembourg + Belgique**, base large (~1 M docs annoncés en 2025).
- **Analyse/intégration de la documentation interne** du client.
- **App mobile (iOS) live** ; **offre entreprise**.
- **Choix de modèle Mistral / Gemini** selon la confidentialité (Alizé 3.0).
- Cible affichée : **gros cabinets + État**.

Ce qu'Alizé n'a **pas annoncé** : vérificateur de citations ancré au corpus officiel,
analytics/insight avocats, option **on-premise air-gap**.

## 3. Énoncé de positionnement
> **Pour les cabinets d'avocats luxembourgeois** qui ne peuvent tolérer ni fuite de données
> ni réponse inventée, **Jurilux** est l'assistant juridique **souverain à vérité vérifiable** :
> chaque affirmation est ancrée et **contrôlée contre le corpus officiel luxembourgeois**
> (jurisprudence + Legilux), sur un **stack 100 % européen** (OVH Luxembourg + modèle
> européen), avec une **intelligence du contentieux** (insight avocats) et une **option
> 100 % sur site** pour les dossiers les plus sensibles.
>
> **Contrairement à Alizé**, la souveraineté n'est pas qu'un hébergement : c'est la
> **vérifiabilité radicale** des réponses, l'**analytics du contentieux**, et le **choix de
> l'air-gap**.

## 4. Les trois piliers de message

### Pilier 1 — « Zéro invention, sources contrôlées » (le plus différenciant)
Le **vérificateur de citations ancré au corpus authentique** : Jurilux détient la source de
vérité du droit LU (jurisprudence + Legilux indexés) et **vérifie** chaque référence citée
contre elle — drapeau si une citation n'existe pas / est mal citée. Aucun Vault généraliste
(Harvey/Lexis/Legora) ne peut le faire pour le droit luxembourgeois. **Déjà livré**
(`/api/vault/documents/{id}/analyze?task=citations`, RAG « refus > invention »).

### Pilier 2 — « Souverain, jusqu'à l'air-gap »
Par défaut : **OVH Luxembourg + modèle européen** (Mistral) — aligné sur la trajectoire de
l'État LU (partenariat gouvernement × Mistral, campagne **AI4LUX** mars 2026). Pour les
dossiers ultra-sensibles : **option 100 % sur site (air-gap)**, que Jurilux peut offrir
(stack `docker-compose` en loopback, quasi-appliance) et **qu'Alizé, SaaS cloud, ne peut
pas**. La souveraineté par **construction**, pas par clause contractuelle.

### Pilier 3 — « Comprendre le contentieux luxembourgeois »
**Insight avocats** : qui plaide quoi, tendances par matière, réseaux de confrères, issues
estimées — sur **données publiques** de jurisprudence, extraction **locale et déterministe**
(jamais de magistrats/greffiers : garde-fou RGPD/CNPD). **Déjà livré** (`/api/insight/*`).
Absent de l'offre affichée d'Alizé.

## 5. Preuves (le positionnement s'appuie sur du livré, pas des promesses)
| Pilier | Preuve livrée | Endpoint |
|---|---|---|
| 1 · Vérité vérifiable | Vérificateur de citations, RAG refus>invention | `/api/vault/.../analyze?task=citations`, `/api/ask` |
| 1 · Vérité vérifiable | RAG hybride privé + corpus public en une requête | `/api/vault/ask` (`include_corpus`) |
| 3 · Contentieux | Insight avocats (profils, réseaux, matières) | `/api/insight/*` |
| 3 · Contentieux | Extraction structurée d'un doc déposé (local) | `/api/vault/.../analyze?task=extract` |
| 2 · Souverain | Hébergement OVH UE, loopback, aucun réentraînement | (infra) |
| — · Parité hygiène | Veille/alertes, cabinet/dossiers, historique, partage | `/api/alerts`, `/api/workspaces`, `/api/history`, `/api/share` |

## 6. Grille de différenciation (mi-2026)
| Levier | Jurilux | Alizé | Verdict |
|---|:--:|:--:|---|
| Stack européen (OVH + Mistral) | ✅ (à finir) | ✅ | Parité — **hygiène**, à combler |
| Vérificateur de citations ancré au corpus officiel | ✅ | non annoncé | **Différenciateur fort** |
| Insight / analytics contentieux | ✅ | non annoncé | **Différenciateur fort** |
| Option on-premise / air-gap | possible | ❌ (SaaS) | **Différenciateur fort** |
| Réponse sourcée → PDF vérifiable | ✅ | ✅ | Parité |
| Couverture Belgique | ❌ | ✅ | Retard (non prioritaire pour la cible) |
| App mobile | ❌ | ✅ | Retard (à évaluer) |
| Analyse documentation interne | ✅ (Vault) | ✅ | Parité |

## 7. Implications roadmap (ordonnées par ce qu'exige le positionnement)
1. **Combler l'hygiène souveraine** — intégrer **Mistral (UE)** en plus de Claude, via un
   **routeur de modèle par sensibilité**. Sans ça, le Pilier 2 est contredit par le
   sous-traitant US actuel. *(Prérequis du discours.)*
2. **Capitaliser sur le déjà-livré** — mettre en avant Pilier 1 (citation-checker) et
   Pilier 3 (insight) dans le produit et le marketing : ce sont des **armes**, pas du
   rattrapage.
3. **Packager l'option on-premise** — formaliser le stack en **appliance cabinet** (le
   loopback docker-compose y est presque). Argument RGPD/CNPD par construction.
4. **Différenciateur #5 Vault (à venir)** — contre-argumentaire sourcé sur la jurisprudence
   LU réelle : renforce le Pilier 1.
5. **À évaluer (parité, non prioritaire cible)** — couverture Belgique, app mobile.

## 8. Garde-fous produit (non négociables)
- **Refus > invention** (conformité, `COMPLIANCE.md`).
- **Insight : avocats/parties uniquement, jamais les magistrats** (RGPD/CNPD ; cf. aussi
  l'interdiction française art. 33 loi 23/03/2019 sur le profilage des juges).
- **Souveraineté par construction** (données en UE, aucun réentraînement sur données client).

## 9. Sources (veille juillet 2026)
- Alizé — site & fonctionnalités : https://alize.lu/
- Alizé — app iOS (mobile live) : https://apps.apple.com/lu/app/aliz%C3%A9-ai/id6759582248
- Alizé 3.0 (modes Mistral/Gemini, mobile, offre entreprise, cible gros cabinets + État) :
  https://www.lessentiel.lu/fr/story/au-luxembourg-l-outil-de-recherche-alize-veut-passer-a-la-vitesse-superieure-103508927
- Gouvernement du Luxembourg × Mistral AI : https://mistral.ai/fr/customers/government-of-luxembourg/
- Campagne nationale AI4LUX (mars 2026) :
  https://me.gouvernement.lu/fr/actualites.gouvernement2024+fr+actualites+toutes_actualites+communiques+2026+03-mars+04-frieden-ai4lux.html
- Panorama global (Harvey, Legora, CoCounsel, Lexis+, vLex, Noxtua) : cf. veille détaillée
  interne (recherche préservée).

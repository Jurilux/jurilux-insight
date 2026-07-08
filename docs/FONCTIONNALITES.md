# Jurilux Insight — documentation fonctionnelle

Produit **B2B d'intelligence contentieux** pour le droit luxembourgeois : des tableaux de bord
d'analyse de la jurisprudence, centrés sur le **profilage d'avocats**, à partir de **données
publiques**. Destiné aux cabinets, assureurs et directions juridiques.

Frontend : `jurilux-insight-web` (accès réservé, mur d'authentification). Backend : `jurilux-insight`.

## À qui ça sert & pourquoi
- **Cabinets** : se benchmarker, préparer un dossier en connaissant la partie adverse, estimer
  durée et enjeu d'un contentieux par matière et juridiction.
- **Assureurs / directions juridiques** : cartographier le risque contentieux (volumes, taux de
  succès estimés, montants et délais médians), choisir un conseil.

## Fonctionnalités (onglets)

| Onglet | Ce que l'utilisateur fait |
|---|---|
| **Vue d'ensemble** | KPIs globaux (avocats profilés, décisions, taux de succès estimé, **montant médian**, **délai médian**) ; répartition **par matière** et **par juridiction** (volume, taux, montant, délai) ; évolution par année ; textes de loi les plus cités. |
| **Avocats** | Recherche, tri (volume / récence / taux estimé), filtre par matière. Fiche profil : décisions (avec lien PDF), côté demandeur/défendeur, issue estimée, matières, **réseau de confrères** (adversaires/co-conseils). **Export CSV**. |
| **Cabinets** | Cabinets explicitement nommés dans les décisions : avocats rattachés, volumes, taux estimé. |
| **Comparateur** | Benchmark **côte à côte de 2 à 6 avocats** (volume, taux, répartition, période, matières). |
| **Recherche** | Recherche jurisprudentielle (Q&A sourcé sur le corpus) — accessoire de sourçage. |
| **Veille** | Alertes sur une matière / un sujet : suivre les nouvelles décisions. |
| **Rapport** | Rapport d'intelligence contentieux **imprimable / PDF** (synthèse, par matière, par juridiction, avocats, cabinets) — livrable client. |
| **Méthodologie** | Transparence : comment les chiffres sont produits, leurs limites, et le formulaire d'**exercice des droits RGPD** (opposition / rectification / accès). |

## Garanties produit (non négociables)
- **Avocats et parties uniquement** — **jamais de magistrats ni de greffiers** (art. 33 / CNPD).
- **Jurimétrie, pas justice prédictive** : statistiques descriptives sur des décisions passées ;
  aucune prédiction ni garantie d'issue.
- **Taux, montants, délais = estimés** (heuristique déterministe), avec marqueur de
  **significativité** (échantillon < 10 → non fiable) et transparence méthodologique.
- **Données publiques** de jurisprudence luxembourgeoise ; extraction locale et déterministe.

## Fraîcheur des données
Les tableaux de bord se remplissent au **build Insight** (extraction), relancé à chaque refresh
mensuel du corpus. Avant le premier build, les vues affichent un état vide gracieux.

Détail technique de l'extraction : voir **`docs/EXTRACTION.md`**.

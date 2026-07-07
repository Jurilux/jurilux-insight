"""Moteur de tests fonctionnels Jurilux.

Définit des **cas d'usage** dérivés de la documentation (contrat d'API de `CLAUDE.md`),
les exécute pour **chaque profil** (anonyme / étudiant / pro / admin, et rôles d'espace),
en **injectant des données de test** (corpus, insight, documents), puis **évalue le succès
par fonctionnalité**.

Aligné sur l'archi de `main` : stdlib + FastAPI TestClient, aucune dépendance nouvelle,
aucun service externe (Meilisearch/Anthropic stubés dans le banc d'essai).

Usage :
    python -m functional.run                 # rapport texte + code de sortie
    python -m functional.run --format markdown
    python -m functional.run --fonctionnalite Vault
"""

# Déploiement air-gap / souverain (A9) + chiffrement au repos (A11)

Recette pour faire tourner Jurilux **100 % sur site, sans aucun appel externe** — le mode
que ni Alizé ni les SaaS globaux n'offrent (cf. `POSITIONING.md`, Pilier 2).

## A9 — Mode air-gap (LLM local)

Le routeur de modèle (`app/llm.py`) supporte déjà le fournisseur `local` (Ollama). Le
service `ollama` est présent dans `docker-compose.yml`. Pour couper tout appel externe :

1. **Router tout le LLM en local** — dans `/opt/jurilux-api/.env` :
   ```
   LLM_PROVIDER_PUBLIC=local
   LLM_PROVIDER_CONFIDENTIAL=local
   OLLAMA_URL=http://ollama:11434
   LOCAL_MODEL=llama3.1        # ou un modèle FR/juridique tiré localement
   ```
   (Ou : `public=mistral` UE + `confidentiel=local` pour un compromis souveraineté/qualité.)

2. **Tirer le modèle une fois** (avec réseau, avant coupure) :
   ```
   docker compose exec ollama ollama pull llama3.1
   ```

3. **Couper l'egress** au niveau réseau (pare-feu / réseau Docker interne) : plus aucun
   trafic sortant. Vérifs :
   - `/api/admin/health` → `llm_routing` doit montrer `local` sur les deux sensibilités ;
   - la recherche hybride sémantique reste locale (embeddings Ollama) si activée ;
   - laisser `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` vides.

> Rappel : `/health` renvoie 503 si `ANTHROPIC_API_KEY` manque — en air-gap, adapter la
> sonde (le LLM local n'a pas besoin de cette clé). À traiter avant bascule (voir CLAUDE.md).

## A11 — Chiffrement au repos

Le chiffrement se fait au niveau **infrastructure** (recommandation souveraine) :

1. **Volume chiffré** (LUKS) pour `/var/lib/jurilux` (base SQLite) et le volume Meili :
   ```
   cryptsetup luksFormat /dev/sdX && cryptsetup open /dev/sdX jurilux_data
   mkfs.ext4 /dev/mapper/jurilux_data   # monter sur le chemin des volumes Docker
   ```
2. **Sauvegardes chiffrées** : chiffrer la sortie de `scripts/backup.sh` (ex. `age`/`gpg`)
   avant tout stockage hors de la machine.
3. **Secrets** hors image : `/opt/jurilux-api/.env` (600, propriétaire `deploy`), jamais
   commité (`.gitignore`), jamais dans les logs.

> Le chiffrement applicatif de la base (SQLCipher) est possible mais ajoute une dépendance
> lourde ; le chiffrement de volume couvre le besoin sans toucher au code.

## Résidence des données
En air-gap, les requêtes et les documents du Vault **ne quittent jamais la machine** :
souveraineté *par construction*, pas par clause contractuelle.

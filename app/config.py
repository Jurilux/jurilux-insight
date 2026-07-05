"""Configuration par variables d'environnement (.env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Meilisearch
    meili_url: str = "http://127.0.0.1:7700"
    meili_master_key: str = ""
    meili_index: str = "chunks"

    # LLM (Anthropic)
    anthropic_api_key: str = ""
    openai_api_key: str = ""  # pour embedder la requête une fois (recherche fédérée hybride)
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_max_tokens: int = 2048

    # Divers
    prompt_version: str = "rebuild-2026-07-v2"  # v2 : biais anti-refus (réponse partielle privilégiée)
    max_context_chunks: int = 16  # chunks max injectés dans le prompt
    hybrid_semantic_ratio: float = 0.0  # 0 = mots-clés seuls ; >0 active la recherche hybride (sémantique)
    snippet_len: int = 400
    rate_limit_per_min: int = 20  # requêtes /api/ask par IP et par minute (0 = illimité)

    host: str = "127.0.0.1"
    port: int = 8088

    # Backoffice admin : emails autorisés (séparés par des virgules) — amorce le 1er
    # admin sans passer par la base. Un compte peut aussi être promu via is_admin en base.
    admin_emails: str = ""

    @property
    def admin_email_set(self) -> set:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    # Espace utilisateur (SQLite)
    db_path: str = "/var/lib/jurilux/jurilux.db"  # volume persistant (docker-compose)
    session_days: int = 30
    student_monthly_quota: int = 30  # questions/mois pour le plan étudiant (freemium)


settings = Settings()

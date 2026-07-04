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
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_max_tokens: int = 2048

    # Divers
    prompt_version: str = "rebuild-2026-07-v1"
    max_context_chunks: int = 12  # chunks max injectés dans le prompt
    snippet_len: int = 400
    rate_limit_per_min: int = 20  # requêtes /api/ask par IP et par minute (0 = illimité)

    host: str = "127.0.0.1"
    port: int = 8088

    # Espace utilisateur (SQLite)
    db_path: str = "/var/lib/jurilux/jurilux.db"  # volume persistant (docker-compose)
    session_days: int = 30
    student_monthly_quota: int = 30  # questions/mois pour le plan étudiant (freemium)


settings = Settings()

"""
backend/app/config.py

Application settings loaded from environment variables.
All settings have safe defaults so the backend starts locally without setup.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="backend/.env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────
    app_env: str = "development"
    app_version: str = "dev"
    secret_key: str = "changeme"
    allowed_origins: list[str] = ["http://localhost:3000"]

    # ── Database (future) ──────────────────────────────────────────────────
    database_url: str = ""

    # ── Supabase (future) ─────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # ── Object storage (future) ────────────────────────────────────────────
    storage_endpoint: str = ""
    storage_access_key_id: str = ""
    storage_secret_access_key: str = ""
    storage_bucket: str = "ai-resume-tailor"

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: str = ""

    # ── Stripe (future) ───────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()

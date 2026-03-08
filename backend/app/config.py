"""
backend/app/config.py

Application settings loaded from environment variables.
All settings have safe defaults so the backend starts locally without setup.
"""

import json
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
    # Stored as a raw string; use .cors_origins for the parsed list.
    # Accepts either a comma-separated string or a JSON array:
    #   ALLOWED_ORIGINS=http://localhost:3000
    #   ALLOWED_ORIGINS=http://a.com,http://b.com
    #   ALLOWED_ORIGINS=["http://a.com","http://b.com"]
    allowed_origins: str = "http://localhost:3000"

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = ""

    # ── Supabase (Auth) ───────────────────────────────────────────────────
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # ── Object storage ────────────────────────────────────────────────────
    storage_endpoint: str = ""
    storage_access_key_id: str = ""
    storage_secret_access_key: str = ""
    storage_bucket: str = "ai-resume-tailor"

    # ── OpenAI ────────────────────────────────────────────────────────────
    openai_api_key: str = ""

    # ── Stripe ────────────────────────────────────────────────────────────
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """Parse ALLOWED_ORIGINS into a list (JSON array or comma-separated)."""
        v = self.allowed_origins.strip()
        if v.startswith("["):
            return json.loads(v)
        return [origin.strip() for origin in v.split(",") if origin.strip()]

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

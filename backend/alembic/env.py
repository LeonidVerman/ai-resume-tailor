"""
backend/alembic/env.py

Alembic migration environment.
Reads DATABASE_URL from application settings and imports all models
so that autogenerate can detect the full schema.
"""

import sys
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ── Ensure repo root is on sys.path so `backend.*` imports resolve ──────────
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ── Import Base and all models (required for autogenerate) ──────────────────
from backend.app.db.base import Base  # noqa: E402

# Import every model module so their tables are registered on Base.metadata.
import backend.app.db.models.user  # noqa: F401, E402
import backend.app.db.models.candidate_profile  # noqa: F401, E402
import backend.app.db.models.structured_resume  # noqa: F401, E402
import backend.app.db.models.job_description  # noqa: F401, E402
import backend.app.db.models.generation_run  # noqa: F401, E402
import backend.app.db.models.tailored_document  # noqa: F401, E402
import backend.app.db.models.evaluation_run  # noqa: F401, E402
import backend.app.db.models.billing  # noqa: F401, E402
import backend.app.db.models.monthly_usage  # noqa: F401, E402
import backend.app.db.models.admin_config  # noqa: F401, E402
import backend.app.db.models.benchmark_run  # noqa: F401, E402
import backend.app.db.models.benchmark_run_position  # noqa: F401, E402
import backend.app.db.models.legal  # noqa: F401, E402
import backend.app.db.models.candidate_profile_resume_draft  # noqa: F401, E402

# ── Alembic config ──────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_database_url() -> str:
    """Read DATABASE_URL from application settings."""
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    try:
        from backend.app.config import get_settings
        return get_settings().database_url
    except Exception:
        return os.environ.get("DATABASE_URL", "")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (generates SQL without a live DB)."""
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    url = _get_database_url()
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Configure it in backend/.env before running migrations."
        )

    cfg = config.get_section(config.config_ini_section) or {}
    cfg["sqlalchemy.url"] = url

    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

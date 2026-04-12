"""
backend/app/db/session.py

Database engine and session factory.

The engine is created lazily so that the backend imports cleanly
even when DATABASE_URL is not yet configured.
"""

import functools
from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def _make_engine(database_url: str) -> Any:
    """Create a synchronous SQLAlchemy engine.

    Pool settings are tuned for Supabase's Transaction-mode pooler (port 6543):
      - pool_size=3 / max_overflow=2  → max 5 server connections per process
      - prepare_threshold=None        → disables psycopg server-side prepared
        statements, which don't survive across Transaction-mode connections
    These settings are safe for Session mode too (port 5432), just conservative.
    """
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not configured. "
            "Set it in backend/.env before using the database."
        )
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=3,
        max_overflow=2,
        connect_args={"prepare_threshold": None},
    )


def _make_session_factory(database_url: str) -> sessionmaker:
    engine = _make_engine(database_url)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_engine(database_url: str = ""):
    """Return a new engine for the given URL (used by Alembic env.py)."""
    return _make_engine(database_url)


@functools.lru_cache(maxsize=4)
def get_session_factory(database_url: str) -> sessionmaker:
    """Return a cached session factory bound to the given database URL.

    The engine (and its connection pool) is created once per unique URL and
    reused across all requests.  Previously a new engine was instantiated on
    every call, creating and discarding a connection pool on every HTTP
    request — a significant performance problem under Supabase's
    Transaction-mode PgBouncer.
    """
    return _make_session_factory(database_url)


def get_db(database_url: str) -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session.

    Usage::

        @router.get("/example")
        def example(db: Session = Depends(lambda: get_db(settings.database_url))):
            ...
    """
    factory = get_session_factory(database_url)
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_connection(database_url: str) -> bool:
    """Return True if the database is reachable, False otherwise."""
    try:
        engine = _make_engine(database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False

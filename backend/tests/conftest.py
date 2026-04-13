"""
backend/tests/conftest.py

Shared pytest fixtures for the SaaS backend test suite.

SQLite compatibility
--------------------
The production DB uses PostgreSQL-specific types (JSONB, UUID).
We patch the SQLite type compiler BEFORE any model imports so that
create_all() succeeds against an in-memory SQLite database.

Isolation strategy
------------------
Each test function runs inside a transaction that is rolled back after
the test, giving a clean slate without recreating the schema each time.
"""

# ── SQLite type patches — must come before model imports ──────────────────
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"
# SQLite only auto-increments INTEGER PRIMARY KEY (exact token); BIGINT doesn't get the
# rowid-alias treatment.  Compile BigInteger as INTEGER so autoincrement works in tests.
SQLiteTypeCompiler.visit_BIGINT = lambda self, type_, **kw: "INTEGER"

# ── Standard imports ──────────────────────────────────────────────────────
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.constants import PLAN_FREE, PLAN_STARTER, ROLE_ADMIN, ROLE_USER
from backend.app.db.base import Base
import backend.app.db.models.billing  # noqa: F401
import backend.app.db.models.monthly_usage  # noqa: F401
import backend.app.db.models.stripe_checkout_purchase  # noqa: F401
import backend.app.db.models.candidate_profile  # noqa: F401
import backend.app.db.models.evaluation_run  # noqa: F401
import backend.app.db.models.generation_run  # noqa: F401
import backend.app.db.models.job_description  # noqa: F401
import backend.app.db.models.structured_resume  # noqa: F401
import backend.app.db.models.tailored_document  # noqa: F401
import backend.app.db.models.user  # noqa: F401
import backend.app.db.models.legal  # noqa: F401
import backend.app.db.models.candidate_profile_resume_draft  # noqa: F401
from backend.app.db.models.billing import Billing
from backend.app.db.models.monthly_usage import MonthlyUsage
from backend.app.db.models.user import User
from backend.app.db.models.legal import LegalDocument, UserLegalStatus
from backend.app.dependencies import get_current_user, get_db_session, require_admin
from backend.app.main import app


# ── Database engine (session-scoped) ──────────────────────────────────────


@pytest.fixture(scope="session")
def engine():
    """In-memory SQLite engine shared across the entire test session."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    # Enable FK support in SQLite
    @event.listens_for(eng, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)


@pytest.fixture(scope="session")
def session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ── Per-test DB session with rollback isolation ────────────────────────────


@pytest.fixture()
def db(engine, session_factory):
    """
    Yield a DB session that is rolled back after each test.

    Uses a savepoint so nested commits from service code don't persist.
    """
    connection = engine.connect()
    transaction = connection.begin()
    db_session = session_factory(bind=connection)

    # SQLite doesn't support real savepoints in all configs; use the
    # connection-level transaction instead.
    yield db_session

    db_session.close()
    transaction.rollback()
    connection.close()


# ── User fixtures ──────────────────────────────────────────────────────────


_LEGAL_DOCS_SEEDED = False


def _seed_legal_docs(db: Session) -> tuple[LegalDocument, LegalDocument]:
    """Ensure legal_documents rows exist; idempotent within a session."""
    global _LEGAL_DOCS_SEEDED
    from datetime import datetime, timezone
    from sqlalchemy import select

    terms = db.execute(
        select(LegalDocument).where(LegalDocument.doc_type == "terms_of_service")
    ).scalars().first()
    if terms is None:
        terms = LegalDocument(
            doc_type="terms_of_service",
            version="v1.0",
            title="Terms of Service",
            file_path="legal/terms/v1.0.md",
            content_sha256="a" * 64,
            effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="active",
            requires_reaccept=True,
        )
        db.add(terms)

    privacy = db.execute(
        select(LegalDocument).where(LegalDocument.doc_type == "privacy_notice")
    ).scalars().first()
    if privacy is None:
        privacy = LegalDocument(
            doc_type="privacy_notice",
            version="v1.0",
            title="Privacy Notice",
            file_path="legal/privacy/v1.0.md",
            content_sha256="b" * 64,
            effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="active",
            requires_reaccept=True,
        )
        db.add(privacy)

    db.flush()
    return terms, privacy


def _make_user(db: Session, email: str, role: str = ROLE_USER) -> User:
    from datetime import datetime, timezone

    u = User(
        id=str(uuid.uuid4()),
        email=email,
        plan_type=PLAN_FREE,
        role=role,
    )
    db.add(u)
    db.flush()

    # Seed legal docs and mark user as compliant so generation tests pass.
    terms, privacy = _seed_legal_docs(db)
    now = datetime.now(timezone.utc)
    status = UserLegalStatus(
        user_id=u.id,
        accepted_terms_document_id=terms.id,
        accepted_privacy_document_id=privacy.id,
        accepted_terms_at=now,
        accepted_privacy_at=now,
    )
    db.add(status)
    db.flush()

    return u


@pytest.fixture()
def user(db: Session) -> User:
    return _make_user(db, "user@example.com", ROLE_USER)


@pytest.fixture()
def admin_user(db: Session) -> User:
    return _make_user(db, "admin@example.com", ROLE_ADMIN)


# ── TestClient fixtures with dependency overrides ─────────────────────────


@pytest.fixture()
def client(db: Session, user: User) -> TestClient:
    """TestClient authenticated as a regular user."""

    def _override_db():
        yield db

    def _override_user():
        return user

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = _override_user

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def admin_client(db: Session, admin_user: User) -> TestClient:
    """TestClient authenticated as an admin user."""

    def _override_db():
        yield db

    def _override_user():
        return admin_user

    def _override_admin():
        return admin_user

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[require_admin] = _override_admin

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def anon_client() -> TestClient:
    """TestClient with NO auth overrides (tests 401 behaviour)."""
    app.dependency_overrides.clear()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── Helper: store JSON in TEXT column (SQLite JSONB compat) ──────────────


def dumps(obj) -> str:
    """Serialize dict to JSON string for SQLite TEXT columns."""
    return json.dumps(obj)


# ── Billing helpers ─────────────────────────────────────────────────────────


def make_billing(
    db: Session,
    user: User,
    plan_type: str = PLAN_FREE,
    subscription_status: str | None = None,
    extra_credits: int = 0,
    monthly_limit_override: int | None = None,
) -> Billing:
    """Create a Billing row for a user in the test DB."""
    b = Billing(
        user_id=user.id,
        plan_type=plan_type,
        subscription_status=subscription_status,
        extra_credits=extra_credits,
        monthly_limit_override=monthly_limit_override,
    )
    db.add(b)
    db.flush()
    return b

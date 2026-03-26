"""
backend/tests/unit/test_soft_delete.py

Tests for soft-delete behavior on StructuredResume and JobDescription.

Covers:
  - Soft delete sets delete_flg=True (DB row preserved)
  - Soft-deleted records excluded from list queries
  - delete() on both repositories is a soft delete (no hard removal)
  - GenerationRunSummary includes company_name / role_title
  - History title fallback logic
"""

from __future__ import annotations

import uuid
import pytest
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uuid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Unit tests — repository.delete() sets delete_flg, does NOT hard-delete
# ---------------------------------------------------------------------------

class TestStructuredResumeRepositoryUnit:
    """Unit tests using a minimal fake session."""

    def _repo(self, session):
        from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
        return StructuredResumeRepository(session)

    def test_delete_sets_flag(self):
        flushed = []
        session = SimpleNamespace(flush=lambda: flushed.append(True))

        resume = SimpleNamespace(id="r1", user_id="u1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(resume)

        assert resume.delete_flg is True
        assert flushed  # flush was called

    def test_delete_does_not_call_session_delete(self):
        """Hard-delete must NOT be called."""
        hard_deleted = []
        session = SimpleNamespace(
            flush=lambda: None,
            delete=lambda obj: hard_deleted.append(obj),
        )
        resume = SimpleNamespace(id="r1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(resume)

        assert hard_deleted == []

    def test_delete_idempotent(self):
        session = SimpleNamespace(flush=lambda: None)
        resume = SimpleNamespace(id="r1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(resume)
        repo.delete(resume)
        assert resume.delete_flg is True


class TestJobDescriptionRepositoryUnit:
    def _repo(self, session):
        from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
        return JobDescriptionRepository(session)

    def test_delete_sets_flag(self):
        flushed = []
        session = SimpleNamespace(flush=lambda: flushed.append(True))
        jd = SimpleNamespace(id="j1", user_id="u1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(jd)

        assert jd.delete_flg is True
        assert flushed

    def test_delete_does_not_call_session_delete(self):
        hard_deleted = []
        session = SimpleNamespace(
            flush=lambda: None,
            delete=lambda obj: hard_deleted.append(obj),
        )
        jd = SimpleNamespace(id="j1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(jd)
        assert hard_deleted == []

    def test_delete_idempotent(self):
        session = SimpleNamespace(flush=lambda: None)
        jd = SimpleNamespace(id="j1", delete_flg=False)
        repo = self._repo(session)
        repo.delete(jd)
        repo.delete(jd)
        assert jd.delete_flg is True


# ---------------------------------------------------------------------------
# Integration tests — SQLite-backed DB to verify WHERE clause filtering
# ---------------------------------------------------------------------------

@pytest.fixture
def sqlite_session():
    """Real SQLAlchemy Session on SQLite.

    JSONB is PostgreSQL-specific; we patch SQLite's type compiler to handle it
    as JSON so create_all() works in the test environment.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    from backend.app.db.base import Base

    if not hasattr(SQLiteTypeCompiler, "visit_JSONB"):
        SQLiteTypeCompiler.visit_JSONB = SQLiteTypeCompiler.visit_JSON
    # Render BigInteger as INTEGER so SQLite treats it as a rowid-alias autoincrement PK.
    SQLiteTypeCompiler.visit_BIGINT = lambda self, type_, **kw: "INTEGER"

    engine = create_engine("sqlite:///:memory:")

    # Import ALL models so SQLAlchemy can resolve cross-model relationships.
    import backend.app.db.models.user  # noqa
    import backend.app.db.models.structured_resume  # noqa
    import backend.app.db.models.job_description  # noqa
    import backend.app.db.models.generation_run  # noqa
    import backend.app.db.models.tailored_document  # noqa
    import backend.app.db.models.evaluation_run  # noqa
    import backend.app.db.models.candidate_profile  # noqa
    import backend.app.db.models.billing  # noqa

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    engine.dispose()


def _insert_user_sql(session, user_id="u1"):
    """Insert a minimal user row via raw SQL to avoid ORM FK complexity."""
    import sqlalchemy as sa
    session.execute(sa.text(
        "INSERT INTO users (id, email, plan_type, role, free_generations_used, is_active) "
        "VALUES (:id, :email, 'free', 'user', 0, 1)"
    ), {"id": user_id, "email": f"{user_id}@example.com"})
    session.flush()


class TestSoftDeleteFiltering:
    """Integration tests verifying the WHERE clause behavior via SQLite."""

    def test_resume_list_excludes_deleted(self, sqlite_session):
        from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
        uid = _uuid()
        _insert_user_sql(sqlite_session, uid)
        repo = StructuredResumeRepository(sqlite_session)

        r1 = repo.create(id=_uuid(), user_id=uid, resume_jsonb={"name": "Alice"},
                         source_file_url=None, input_conversion_warning=None)
        r2 = repo.create(id=_uuid(), user_id=uid, resume_jsonb={"name": "Bob"},
                         source_file_url=None, input_conversion_warning=None)

        repo.delete(r1)

        listing = repo.list_by_user_id(uid)
        ids = [r.id for r in listing]
        assert r1.id not in ids
        assert r2.id in ids

    def test_resume_row_preserved_after_soft_delete(self, sqlite_session):
        from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
        uid = _uuid()
        _insert_user_sql(sqlite_session, uid)
        repo = StructuredResumeRepository(sqlite_session)

        r = repo.create(id=_uuid(), user_id=uid, resume_jsonb={"name": "Carol"},
                        source_file_url=None, input_conversion_warning=None)
        rid = r.id
        repo.delete(r)

        found = repo.get_by_id(rid)
        assert found is not None
        assert found.delete_flg is True

    def test_get_latest_excludes_deleted(self, sqlite_session):
        from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
        uid = _uuid()
        _insert_user_sql(sqlite_session, uid)
        repo = StructuredResumeRepository(sqlite_session)

        r1 = repo.create(id=_uuid(), user_id=uid, resume_jsonb={"name": "Dave"},
                         source_file_url=None, input_conversion_warning=None)
        r2 = repo.create(id=_uuid(), user_id=uid, resume_jsonb={"name": "Eve"},
                         source_file_url=None, input_conversion_warning=None)

        # Soft-delete r2 (most recent); r1 should become latest.
        repo.delete(r2)

        latest = repo.get_latest_by_user_id(uid)
        assert latest is not None
        assert latest.id == r1.id

    def test_jd_list_excludes_deleted(self, sqlite_session):
        from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
        uid = _uuid()
        _insert_user_sql(sqlite_session, uid)
        repo = JobDescriptionRepository(sqlite_session)

        j1 = repo.create(user_id=uid, source_url=None,
                         source_type="manual", raw_text="JD one", metadata_jsonb=None)
        j2 = repo.create(user_id=uid, source_url=None,
                         source_type="manual", raw_text="JD two", metadata_jsonb=None)

        repo.delete(j1)

        listing = repo.list_by_user_id(uid)
        ids = [j.id for j in listing]
        assert j1.id not in ids
        assert j2.id in ids

    def test_jd_row_preserved_after_soft_delete(self, sqlite_session):
        from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
        uid = _uuid()
        _insert_user_sql(sqlite_session, uid)
        repo = JobDescriptionRepository(sqlite_session)

        j = repo.create(user_id=uid, source_url=None,
                        source_type="manual", raw_text="JD", metadata_jsonb=None)
        jid = j.id
        repo.delete(j)

        found = repo.get_by_id(jid)
        assert found is not None
        assert found.delete_flg is True


# ---------------------------------------------------------------------------
# History title display logic
# ---------------------------------------------------------------------------

class TestHistoryTitleLogic:
    """Unit tests for the company/role title display logic (Python mirror of
    the frontend JSX expression)."""

    def _title(self, company_name, role_title, run_id="aaaabbbb-cccc"):
        if company_name and role_title:
            return f"{company_name} / {role_title}"
        if company_name:
            return company_name
        if role_title:
            return role_title
        return run_id[:8] + "…"

    def test_both_present(self):
        assert self._title("Dayforce", "Principal AI Engineer") == "Dayforce / Principal AI Engineer"

    def test_only_company(self):
        assert self._title("impact.com", None) == "impact.com"

    def test_only_role(self):
        assert self._title(None, "Software Engineering Manager") == "Software Engineering Manager"

    def test_neither_falls_back_to_uuid(self):
        result = self._title(None, None, run_id="abcd1234-xxxx")
        assert result == "abcd1234…"

    def test_empty_strings_treated_as_missing(self):
        result = self._title("", "", run_id="abcd1234-xxxx")
        assert result == "abcd1234…"


# ---------------------------------------------------------------------------
# GenerationRunSummary schema
# ---------------------------------------------------------------------------

class TestGenerationRunSummarySchema:
    def test_summary_accepts_company_role(self):
        from backend.app.schemas.generation import GenerationRunSummary
        from datetime import datetime, timezone

        s = GenerationRunSummary(
            id="run-1", status="succeeded", run_type="two_phase",
            model_name="gpt-4o", started_at=datetime.now(tz=timezone.utc),
            company_name="Dayforce", role_title="Principal AI Engineer",
        )
        assert s.company_name == "Dayforce"
        assert s.role_title == "Principal AI Engineer"

    def test_summary_defaults_to_none(self):
        from backend.app.schemas.generation import GenerationRunSummary
        from datetime import datetime, timezone

        s = GenerationRunSummary(
            id="run-2", status="failed", run_type="two_phase",
            model_name="gpt-4o", started_at=datetime.now(tz=timezone.utc),
        )
        assert s.company_name is None
        assert s.role_title is None

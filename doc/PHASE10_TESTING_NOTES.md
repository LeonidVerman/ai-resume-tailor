# Phase 10 — Backend Testing Notes

## Overview

Phase 10 adds a structured automated test suite for the SaaS backend using
pytest. 112 tests pass covering unit tests, API endpoint tests, and an
integration happy-path test. Coverage across tested paths is 55%.

---

## Test structure

```
backend/tests/
├── __init__.py
├── conftest.py                       — shared fixtures
├── unit/
│   ├── test_schemas.py               — Pydantic schema validation
│   ├── test_resume_parser_service.py — text extraction + heuristic parse
│   ├── test_usage_policy_service.py  — free-tier quota enforcement
│   └── test_generation_service.py    — orchestration (pipeline mocked)
├── api/
│   ├── test_candidate_profile_api.py — GET/POST/PUT /candidate-profile
│   ├── test_resume_api.py            — upload/list/get /resumes
│   ├── test_job_description_api.py   — scrape/manual/list/get/delete /job-descriptions
│   ├── test_generation_api.py        — create/list/get /generations
│   └── test_admin_api.py             — /admin/system-stats + /admin/evaluate-run
└── integration/
    └── test_generation_flow.py       — full happy-path flow
```

---

## Key design decisions

### SQLite in-memory with PostgreSQL type patches

The production DB uses PostgreSQL-specific types (`JSONB`, `UUID`). We patch
the SQLite type compiler **before any model imports**:

```python
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "VARCHAR(36)"
```

This allows `Base.metadata.create_all()` to succeed against SQLite.

### Transaction rollback isolation

Each test function runs inside a connection-level transaction that is rolled
back after the test, giving a clean slate without recreating the schema:

```python
@pytest.fixture()
def db(engine, session_factory):
    connection = engine.connect()
    transaction = connection.begin()
    db_session = session_factory(bind=connection)
    yield db_session
    db_session.close()
    transaction.rollback()
    connection.close()
```

### Dependency overrides for auth

FastAPI's `dependency_overrides` is used to inject a test DB session and
a pre-created user, bypassing the real `X-User-Id` header lookup:

```python
app.dependency_overrides[get_db_session] = lambda: (yield db)
app.dependency_overrides[get_current_user] = lambda: user
```

### LLM pipeline mocking

`GenerationService._run_pipeline` is patched at the method level so no
LLM calls are made. Config values (`PHASE2_MODEL`) are patched at
`tailor.config.*` because they are imported inside function bodies, not
at module level.

---

## Bug fixes applied during Phase 10

Two model-vs-service discrepancies were fixed:

1. **`JobDescription.parsed_metadata_jsonb` → `metadata_jsonb`**
   Added `source_type` column. The normalizer service used `metadata_jsonb`
   and `source_type`, but the model had `parsed_metadata_jsonb` and no
   `source_type` column.

2. **`EvaluationRun` individual score columns → `scores_jsonb`**
   The evaluation service stores scores as a JSONB dict, but the model had
   individual `Numeric` columns per dimension. Replaced with a single
   `scores_jsonb: JSONB` column to match service expectations.

---

## Coverage

```
pytest --cov=backend/app --cov-report=term-missing
TOTAL   55%  (112 tests, 1890 statements, 855 missed)
```

High-coverage modules (>80%):
- All schemas — 100%
- `stats_service`, `usage_policy_service`, `logging`, `main` — 100%
- `job_normalizer_service` — 92%
- `resume_parser_service` — 88%
- `generation_run_repository` — 95%

Low-coverage modules (intentionally deferred):
- `auth_service`, `storage_service`, `rendering_service` — 0% (not integrated yet)
- `billing_service` — 30% (Stripe integration deferred)
- `generation_service._run_pipeline` — pipeline mocked in all tests

---

## Running tests

```bash
# From repo root:
cd backend
python -m pytest tests/ -q

# With coverage:
python -m pytest tests/ --cov=app --cov-report=term-missing

# Specific module:
python -m pytest tests/api/test_candidate_profile_api.py -v
```

---

## Deferred items

- Auth endpoint tests (`GET /auth/me`) — requires user in DB by UUID
- Billing webhook tests — requires Stripe fixture/mock
- Document download tests — requires `TailoredDocument` with JSONB content
- Async job execution tests — deferred until background worker added
- Full `EvaluationService` integration — requires non-mocked assess pipeline

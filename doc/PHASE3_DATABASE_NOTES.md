# Phase 3 Database Notes

**Date:** 2026-03-07
**Tasks covered:** Phase 3, Tasks 13–24 from `IMPLEMENTATION_TASK_PLAN.md`

---

## Models added

| Model | Table | File |
|---|---|---|
| User | users | `backend/app/db/models/user.py` |
| CandidateProfile | candidate_profiles | `backend/app/db/models/candidate_profile.py` |
| StructuredResume | structured_resumes | `backend/app/db/models/structured_resume.py` |
| JobDescription | job_descriptions | `backend/app/db/models/job_description.py` |
| GenerationRun | generation_runs | `backend/app/db/models/generation_run.py` |
| TailoredDocument | tailored_documents | `backend/app/db/models/tailored_document.py` |
| EvaluationRun | evaluation_runs | `backend/app/db/models/evaluation_run.py` |
| Billing | billing | `backend/app/db/models/billing.py` |

---

## Schema key decisions

### ID strategy
UUIDs stored as `VARCHAR` (Postgres `UUID` dialect type, `as_uuid=False`).
Consistent with Supabase conventions and avoids integer sequence conflicts.

### JSONB usage
Flexible structured data columns use `postgresql.JSONB`:
- `candidate_profiles.profile_jsonb`
- `structured_resumes.resume_jsonb`
- `job_descriptions.parsed_metadata_jsonb`
- `generation_runs.input_snapshot_jsonb`, `parsed_output_jsonb`
- `tailored_documents.resume_jsonb`, `cover_letter_jsonb`

### Timestamps
- Append-only tables (`users`, `structured_resumes`, `job_descriptions`,
  `generation_runs`, `tailored_documents`, `evaluation_runs`) use `CreatedAtMixin`
  (only `created_at`).
- Mutable tables (`candidate_profiles`, `billing`) use `TimestampMixin`
  (`created_at` + `updated_at`).

### Relationships
- `User` → all other entities via `user_id` FK with `ondelete=CASCADE`
- `GenerationRun` → `TailoredDocument` (one-to-many, cascade delete)
- `GenerationRun` → `EvaluationRun` (one-to-many, cascade delete)
- `GenerationRun` → `JobDescription` (nullable FK, `ondelete=SET NULL`)
- `Billing` is unique per user (`unique=True` on `user_id`)

### Billing simplification
`Billing` table is intentionally small — Stripe is source of truth.
Only app-relevant state is stored: `stripe_customer_id`, `stripe_subscription_id`,
`subscription_status`, `plan_type`, `current_period_end`.

---

## Migration status

Initial migration: `backend/alembic/versions/a1b2c3d4e5f6_initial_schema.py`
- Hand-written (no live DB required to generate)
- Covers all 8 MVP tables with indexes and FK constraints
- Run with: `python -m alembic -c backend/alembic.ini upgrade head`
- Requires `DATABASE_URL` set in `backend/.env`

---

## Repository layer

Thin CRUD repositories under `backend/app/db/repositories/`:

| Repository | Entity |
|---|---|
| `UserRepository` | User |
| `CandidateProfileRepository` | CandidateProfile |
| `StructuredResumeRepository` | StructuredResume |
| `JobDescriptionRepository` | JobDescription |
| `GenerationRunRepository` | GenerationRun |
| `TailoredDocumentRepository` | TailoredDocument |
| `EvaluationRunRepository` | EvaluationRun |
| `BillingRepository` | Billing |

All repositories:
- accept a `Session` in `__init__`
- use `flush()` after writes (caller controls commit)
- contain no business logic

---

## What was intentionally deferred

- Async SQLAlchemy engine (acceptable in Phase 3; can be added in Phase 5/6)
- Alembic autogenerate against live DB (needs Postgres running)
- Repository injection as FastAPI dependencies (Phase 6)
- Any CRUD APIs (Phase 8)
- Auth integration (Phase 6)
- Billing logic (Phase 14)

---

## Compatibility

- CLI generator (`python -m tailor`) untouched
- All 469 generator tests pass
- Backend health endpoint (`GET /api/v1/health`) still works
- DB layer imports cleanly without a running database

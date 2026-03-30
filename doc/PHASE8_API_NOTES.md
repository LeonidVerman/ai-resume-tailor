# Phase 8 — API Endpoints Notes

## Overview

Phase 8 wires the FastAPI HTTP layer. All endpoint handlers are thin: they
validate input via Pydantic, delegate to Phase 6 service classes, and return
typed responses. No business logic lives in route handlers.

---

## Endpoints implemented

| Router file            | Prefix                | Methods + paths                                              |
|------------------------|-----------------------|--------------------------------------------------------------|
| `auth.py`              | `/auth`               | GET /me, GET /status                                         |
| `candidate_profile.py` | `/candidate-profile`  | GET, POST, PUT                                              |
| `resume.py`            | `/resumes`            | POST /upload, GET, GET /{id}                                |
| `job_description.py`   | `/job-descriptions`   | POST /scrape, POST /manual, GET, GET /{id}, DELETE /{id}    |
| `generation.py`        | `/generations`        | POST, GET, GET /{id}                                        |
| `documents.py`         | `/documents`          | GET /{id}, GET /{id}/download                               |
| `billing.py`           | `/billing`            | GET /status, POST /create-checkout-session                  |
| `webhooks.py`          | `/webhooks`           | POST /stripe                                                |
| `admin.py`             | `/admin`              | POST /evaluate-run, GET /system-stats                       |

Total: 24 registered routes (including `/health`).

---

## Key design decisions

### Auth (dev-mode bypass)
Full Supabase JWT integration is deferred. All protected endpoints accept an
`X-User-Id` header that maps directly to `users.id`. The `get_current_user`
dependency looks up the user in the DB; 401 if missing, 404 if not found.
Admin endpoints use `require_admin` which additionally checks `user.role == 'admin'`.

### Thin handlers
Route files contain no direct DB queries or LLM calls. All logic is in services
(`backend/app/services/`). Handler bodies are typically 1–3 lines.

### JSONB text storage
The generator produces plain strings. `TailoredDocument` has JSONB columns, no
plain text columns. Resume and cover letter are stored as `{"text": "..."}` and
retrieved the same way in the `/download` endpoint.

### Stripe webhooks
Signature validation is performed when `STRIPE_WEBHOOK_SECRET` is set; the
handler falls back to raw JSON parsing in dev/test mode. Unknown event types
return `200 {"received": true}` without processing (avoids Stripe retry storms).

### Multipart upload
`POST /resumes/upload` uses FastAPI `UploadFile`. Requires `python-multipart`
installed (`pip install python-multipart`). File size limit: 10 MB enforced
in the handler.

### Billing
`GET /billing/status` works with no Stripe config (returns free-plan info).
`POST /billing/create-checkout-session` returns 503 when `STRIPE_SECRET_KEY`
is not set.

### Evaluation (admin)
`POST /admin/evaluate-run` requires `status='succeeded'` on the run. Delegates
to `EvaluationService` which calls `tailor.assess.score_single()` if available;
falls back to null scores so evaluation never blocks.

---

## Limitations and deferred work

- **Auth**: `X-User-Id` dev bypass only — replace with Supabase JWT in Phase 9+.
- **Generation**: Runs synchronously in the request thread (30–90 s typical).
  Background worker (Celery / FastAPI BackgroundTasks) is a future enhancement.
- **Document download**: Returns plain text from JSONB. When S3 storage is wired,
  replace with signed URL redirect.
- **Resume DOCX upload**: Currently parses text for storage. Binary DOCX storage
  to S3 is deferred (requires `StorageService` wired with S3 credentials).
- **python-multipart**: Must be installed separately; not in base requirements.

---

## Files created / modified

**Created:**
- `backend/app/api/auth.py`
- `backend/app/api/candidate_profile.py`
- `backend/app/api/resume.py`
- `backend/app/api/job_description.py`
- `backend/app/api/generation.py`
- `backend/app/api/documents.py`
- `backend/app/api/billing.py`
- `backend/app/api/webhooks.py`
- `backend/app/api/admin.py`
- `doc/PHASE8_API_NOTES.md` (this file)

**Modified:**
- `backend/app/api/router.py` — all 9 new routers registered
- `backend/app/dependencies.py` — full rewrite with `DbDep`, `CurrentUserDep`, `AdminDep`, `SettingsDep`
- `backend/app/services/generation_service.py` — bug fix: wrong column names corrected

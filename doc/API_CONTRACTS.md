# API Contracts

> **Status:** Up to date as of milestone 0.8.8.BETA.
> All endpoints are implemented and operational.

---

## Conventions

- All endpoints are prefixed with `/api/v1/`
- Authentication: `Authorization: Bearer <JWT>` header (Supabase JWT in production)
- Request and response bodies are JSON unless noted (multipart for file uploads)
- Errors return `{ "detail": "..." }` (FastAPI default)
- Timestamps are ISO 8601 UTC strings

---

## Auth

### POST /auth/login
Login with email and password (requires `AUTH_MODE=supabase`).

**Request:**
```json
{ "email": "user@example.com", "password": "password123" }
```

**Response 200:**
```json
{
  "authenticated": true,
  "user": {
    "id": "<uuid>",
    "email": "user@example.com",
    "role": "user",
    "is_admin": false,
    "plan_type": "free"
  },
  "session": {
    "access_token": "<JWT>",
    "refresh_token": "<token>",
    "token_type": "bearer",
    "expires_in": 3600
  }
}
```

### POST /auth/register
Register a new account via Supabase. Same response shape as `/auth/login`.

### POST /auth/logout
Invalidate session. Returns 204.

### GET /auth/me
Returns full identity + onboarding + legal state for the authenticated user.

**Response 200:**
```json
{
  "user_id": "<uuid>",
  "email": "user@example.com",
  "role": "user",
  "is_admin": false,
  "plan_type": "free",
  "onboarding_completed": true,
  "legal_accepted": true
}
```

### GET /auth/status
Returns `{ "auth_mode": "supabase", "status": "ok" }` (no auth required).

### POST /auth/forgot-password
Send password reset email. Returns 204. Body: `{ "email": "...", "redirect_to": "..." }`.

### POST /auth/reset-password
Reset password with recovery token. Returns 204.
Body: `{ "access_token": "<recovery-token>", "new_password": "..." }`.

### POST /auth/refresh
Exchange refresh token for a new session. Same response shape as `/auth/login`.
Body: `{ "refresh_token": "..." }`.

---

## Resume

### POST /resumes/upload
Upload a DOCX or PDF resume. Classification runs asynchronously in the background after the response is returned.

**Request:** `multipart/form-data` with `file` field (DOCX or PDF, max 10 MB).

**Response 201:**
```json
{
  "id": 42,
  "user_id": "<uuid>",
  "resume": { /* StructuredResumeDocument — parsed fields */ },
  "source_file_url": "documents/<user-id>/templates/42.docx",
  "input_conversion_warning": null,
  "created_at": "2026-05-25T21:23:39Z"
}
```

### GET /resumes
List all non-deleted resumes for the authenticated user.

**Response 200:** Array of `{ "id", "name", "created_at", "source_file_url" }`.

### GET /resumes/{resume_id}
Return full resume record including parsed content.

### DELETE /resumes/{resume_id}
Soft-delete a resume (sets `delete_flg=true`). Returns 204.

---

## Candidate Profile

### GET /candidate-profile
Return the authenticated user's current profile. 404 if none exists.

### POST /candidate-profile
Create a new profile. Body: `{ "profile": CandidateProfileDocument, "profile_version": "1" }`.

### PUT /candidate-profile
Upsert (update or create) the user's profile. Same body as POST.
Does **not** reset `onboarding_completed`.

**Response 200:**
```json
{
  "id": 7,
  "user_id": "<uuid>",
  "profile_version": "1",
  "profile": { /* CandidateProfileDocument */ },
  "onboarding_completed": true,
  "source_resume_id": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### POST /candidate-profile/complete-onboarding
Mark `onboarding_completed=true` for the user's profile.

### GET /candidate-profile/autofill/resumes
List resumes available for autofill.

### GET /candidate-profile/autofill/draft?resume_id={id}
Return cached autofill draft for a resume. 404 if none generated yet.

### POST /candidate-profile/autofill/generate
Generate (or regenerate) a profile draft from a resume using the LLM.

**Request:** `{ "resume_id": 42 }`

**Response 200:**
```json
{
  "resume_id": 42,
  "draft": { /* CandidateProfileDocument */ },
  "status": "ready",
  "resume_hash": "<sha256>",
  "is_stale": false,
  "model": "gpt-4o",
  "generated_at": "..."
}
```

### POST /candidate-profile/autofill/select-source
Persist `source_resume_id` on the profile. Body: `{ "resume_id": 42 }`.

---

## Job Description

### POST /job-descriptions/scrape
Scrape a job description from a URL.

### POST /job-descriptions/manual
Create a job description manually.

### GET /job-descriptions
List the user's stored job descriptions.

### GET /job-descriptions/{id}
Return a single job description.

### DELETE /job-descriptions/{id}
Delete a job description.

---

## Generation

Generation runs synchronously (30–90 s typical). Requires:
- `legal_accepted=true` for the user (checked via `/auth/me`)
- `onboarding_completed=true` on the user's candidate profile

### POST /generations
Trigger the tailoring pipeline for a job + resume.

**Request:**
```json
{
  "job_description_id": 97,
  "structured_resume_id": 42,
  "generation_mode": "conservative"
}
```
`generation_mode` is one of `"conservative"`, `"normal"`, `"aggressive"`.

**Response 201:**
```json
{
  "run_id": 312,
  "status": "succeeded",
  "tailored_document_id": 88,
  "message": null
}
```
Returns 403 if legal not accepted or onboarding not complete.

### GET /generations
List the user's generation runs, newest first. Query params: `limit` (default 50), `offset`.

### GET /generations/{run_id}
Return full detail for a generation run including token usage and error info.

### DELETE /generations/{run_id}
Delete a generation run and its associated tailored documents.

---

## Documents

### GET /documents/{id}
Return document metadata.

### GET /documents/{id}/download
Download the tailored document content.

---

## Legal

### GET /legal/current
Return metadata for the two currently active legal documents (no auth required).

**Response 200:**
```json
{
  "terms": { "id": 1, "doc_type": "terms_of_service", "version": "...", ... },
  "privacy": { "id": 2, "doc_type": "privacy_notice", "version": "...", ... }
}
```

### GET /legal/status
Return the user's acceptance compliance state.

**Response 200:** `{ "compliant": true, "terms": { "accepted": true, ... }, "privacy": { ... } }`

### POST /legal/accept
Record acceptance of both current documents.

**Request:**
```json
{
  "terms_document_id": 1,
  "privacy_document_id": 2,
  "acceptance_method": "api",
  "source_surface": "api"
}
```
Returns 204.

### GET /legal/document/{doc_type}
Return the raw markdown text of a legal document. `doc_type`: `terms_of_service` or `privacy_notice`.

---

## Billing

### GET /billing/status
Return billing status (works without Stripe config, returns free-plan info).

### POST /billing/create-checkout-session
Create Stripe checkout session. Returns 503 if Stripe is not configured.

---

## Admin

All admin endpoints require `Authorization: Bearer <token>` for a user with `role='admin'`.

### POST /admin/evaluate-run
Score a completed generation run. Body: `{ "generation_run_id": 312 }`.
Requires `status='succeeded'` on the run.

### GET /admin/system-stats
Return aggregate counts across all users.

### GET /admin/generation-config
Return persisted generation config (model, etc.).

### PUT /admin/generation-config
Update generation config. Body: `{ "simple_model": "..." }`.

### GET /admin/signup-credit-policy
Return the initial credits granted to new signups.

### PUT /admin/signup-credit-policy
Update initial credits. Body: `{ "initial_credits": 3 }`.

### POST /admin/candidate-profiles/backfill
Re-normalise all stored candidate profiles to the current schema version.

### POST /admin/billing/grant-credits
Grant or deduct generation credits for a user.
Body: `{ "user_id": "<uuid>", "amount": 5 }`.

### POST /admin/billing/register-checkout-session
Manually record a Stripe checkout session as already processed (dedup seed).

### GET /admin/resumes/{resume_id}/classification
Return the stored LLM classification for an uploaded resume.
Returns 404 if the resume does not exist or has not been classified yet
(classification runs asynchronously after upload).

### POST /admin/resumes/{resume_id}/classification/trigger
Manually trigger LLM classification for a resume.

### POST /admin/classification/classify-file
Upload a DOCX/PDF, run classification, return result directly — nothing stored.
Request: `multipart/form-data` with `file` field.
Auth: `X-Cli-Secret` header (or no auth in development without a secret configured).

### GET /admin/logs/download
Download a ZIP of daily log files. Query params: `from_date` (required), `to_date`.

### GET /admin/run-data/download
Download a ZIP of run packs for a date range (one subfolder per run ID). Query params: `from_date`, `to_date`.

### GET /admin/run-data/download/{run_id}
Download the run pack ZIP for a single generation run (`run-data-{run_id}.zip`).

Each run pack contains up to 8 files: the debug JSON (`FirstName_LastName-Company-Role-{run_id}-yyyyMMdd-HHmmss.json`), the resume and cover letter in pdf/docx/txt, and the original uploaded resume template (`{CandidateName}_Resume_Template.pdf|docx`). Missing files are skipped silently.

### GET /admin/benchmark-runs
List recent benchmark runs.

### POST /admin/benchmark-runs
Start a new benchmark run.

### GET /admin/benchmark-runs/{run_id}
Return full benchmark run detail.

### GET /admin/benchmark-runs/{run_id}/download
Download the benchmark report as a ZIP archive.

---

## Webhooks

### POST /webhooks/stripe
Stripe webhook receiver. Validates signature when `STRIPE_WEBHOOK_SECRET` is set.

---

## Health

### GET /api/v1/health
Returns `{ "status": "ok", "service": "resume-tailor-backend", "version": "..." }` (no auth required).

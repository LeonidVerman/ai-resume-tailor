# Phase 4 Schema Notes

**Date:** 2026-03-07
**Tasks covered:** Phase 4, Tasks 25–32 from `IMPLEMENTATION_TASK_PLAN.md`

---

## Schemas added

| File | Task | Contents |
|---|---|---|
| `backend/app/schemas/common.py` | 25 | `APIModel` base, `PaginatedResponse`, `ErrorResponse`, timestamp mixins |
| `backend/app/schemas/auth.py` | 26 | `LoginRequest`, `RegisterRequest`, `TokenResponse`, `AuthMeResponse` |
| `backend/app/schemas/user.py` | 26 | `UserSummary`, `UserUpdateRequest` |
| `backend/app/schemas/candidate_profile.py` | 27 | Full nested profile document + API request/response models |
| `backend/app/schemas/structured_resume.py` | 28 | `StructuredResumeDocument`, `ExperienceEntry`, `ContactInfo`, `SkillsSection` |
| `backend/app/schemas/job_description.py` | 29 | Scrape/manual input requests, `JobDescriptionResponse`, `JobMetadata` |
| `backend/app/schemas/generation.py` | 30 | `GenerationRequest`, `GenerationResponse`, `GenerationRunDetail`, `GenerationOptions` |
| `backend/app/schemas/tailored_document.py` | 31 | `TailoredDocumentDetail`, `TailoredDocumentSummary`, `ArtifactURLs` |
| `backend/app/schemas/evaluation.py` | 32 | `EvaluationScores` (0–1 bounded), `EvaluationRequest`, `EvaluationResponse` |
| `backend/app/schemas/billing.py` | 32 | `BillingStatus`, `CheckoutSessionRequest`, `CheckoutSessionResponse` |
| `backend/app/schemas/admin.py` | 32 | `SystemStats`, `AdminActionResponse` |

---

## Existing repo data reused

### Candidate profile (`candidate_profile.py`)
- Grounded in `profile/candidate_profile.json` (the real candidate profile used by the generator)
- Nested sub-models match the real JSON structure exactly:
  `CandidateIdentity`, `DomainExperience`, `ExperienceHighlight`, `TechnicalSkills`,
  `AuthzAuthnExperience`, `Leadership`, `AIToolingPractice`, `ConstraintsAndPreferences`
- `TechnicalSkills.other: dict[str, list[str]]` catches arbitrary extra categories for generic SaaS users
- All sections beyond identity are `Optional` to support partial profiles during onboarding
- Validated successfully against the real `profile/candidate_profile.json`

### Structured resume (`structured_resume.py`)
- Based on the spec's Resume JSON Schema plus the actual template resume (Leonid_Verman_Resume_Template.docx)
- Added `linkedin_url`, `github_url`, `website_url` to `ContactInfo` (template uses LinkedIn URL)
- Added `employment_type` to `ExperienceEntry` (template uses "Independent Contractor")
- `SkillsSection` supports both flat list and categorized dict (template uses categories)

### Job description (`job_description.py`)
- `JobMetadata` mirrors fields that `extract_metadata_ai()` in the generator already extracts
  (company, job_title), plus future SaaS-useful fields (location, seniority_level, remote_policy)
- `JobDescriptionManualRequest` aligns with `JobData` dataclass (company, job_title, description, source_url)

### Generation (`generation.py`)
- `GenerationOptions.mode` covers both generator run types: `two_phase` and `single_pass`
- `GenerationResponse.tailored_document_id` links to the stored artifact for download
- Status enum matches `GenerationRun.status` values from Phase 3 DB model

### Tailored document (`tailored_document.py`)
- `resume_json / cover_letter_json` are nullable dicts (generator currently produces raw strings;
  structured JSON representation is a future enhancement)
- `ArtifactURLs` covers all four artifact types the generator produces (resume DOCX/PDF + cover letter DOCX/PDF)

---

## Important schema decisions

### `APIModel` base class
All schemas inherit from `APIModel(BaseModel)` with:
- `from_attributes=True` — allows construction from SQLAlchemy ORM objects directly
- `populate_by_name=True` — accepts both field name and alias in input

### Optional vs required
- `CandidateProfileDocument`: `candidate` (identity) is required; all other sections optional
- `StructuredResumeDocument`: `name` is required; `contacts`, `summary`, `experience`, `skills` have defaults
- `EvaluationScores`: all scores optional (nullable until evaluated)
- `BillingStatus`: `subscription_status` and `current_period_end` are optional (free-tier users have none)

### Email validation
`auth.py` uses `EmailStr` from `pydantic[email]` (requires `email-validator` package).

---

## Bug fixed
`profile/candidate_profile.json` had a pre-existing JSON syntax error (missing comma on line 115).
Fixed: `"A_B_tests"` → `"A_B_tests",` so the file is now valid JSON.

---

## Known compromises / deferred refinements

- Candidate profile `experience_highlights` sub-fields use `list[str]` for flexibility;
  could be typed more strictly once a stable schema emerges from real SaaS usage
- `StructuredResumeDocument.raw_text` included for backward compat with generator internals;
  may be dropped once the generator is wired to structured output
- Generation response is synchronous (one response model); async job queue pattern deferred to Phase 10+
- Auth schemas are minimal placeholders; full Supabase JWT integration is Phase 6

---

## Compatibility

- CLI generator (`python -m tailor`) untouched — 469 tests pass
- Backend health endpoint still returns HTTP 200
- No existing imports or module paths changed

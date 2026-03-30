# Phase 6 Service Layer Notes

**Date:** 2026-03-07
**Tasks covered:** Phase 6, Tasks 38–50 from `IMPLEMENTATION_TASK_PLAN.md`

---

## Services added

| File | Task | Purpose |
|---|---|---|
| `backend/app/services/auth_service.py` | 38 | User resolution, get_or_create_user, role checks |
| `backend/app/services/candidate_profile_service.py` | 39 | Profile CRUD, upsert, _to_response |
| `backend/app/services/resume_parser_service.py` | 40 | DOCX/PDF text extraction + heuristic parsing |
| `backend/app/services/job_scraper_service.py` | 41 | Wraps generator scrape_job_url(); ScrapingClient fallback |
| `backend/app/services/job_normalizer_service.py` | 42 | Stores JD records; create_from_scrape / create_from_manual |
| `backend/app/services/prompt_assembly_service.py` | 43 | Thin wrapper around tailor.prompts._load_prompt etc. |
| `backend/app/services/storage_service.py` | 44 | Upload/download DOCX and PDF via StorageClient |
| `backend/app/services/rendering_service.py` | 45 | Wraps save_doc_from_template + docx_to_pdf via temp files |
| `backend/app/services/usage_policy_service.py` | 46 | Free-tier quota enforcement; check_can_generate() |
| `backend/app/services/billing_service.py` | 47 | Stripe checkout, subscription sync, BillingStatus |
| `backend/app/services/generation_service.py` | 48 | Full two-phase pipeline with GenerationRun DB lifecycle |
| `backend/app/services/evaluation_service.py` | 49 | Scores completed runs via tailor.assess |
| `backend/app/services/stats_service.py` | 50 | System-wide aggregate counts for admin dashboard |

---

## Existing generator code reused (unchanged)

| Generator function | Used by |
|---|---|
| `tailor.job.scrape.scrape_job_url()` | JobScraperService (primary path) |
| `tailor.prompts._load_prompt()` / `_load_prompt_optional()` / `_load_candidate_profile()` | PromptAssemblyService |
| `tailor.docx.template_fill.save_doc_from_template()` / `normalize_cover_letter()` | RenderingService |
| `tailor.docx.pdf.docx_to_pdf()` | RenderingService |
| `tailor.core_generation.llm.plan_tailoring()` / `plan_repair_tailoring()` / `tailor_documents_with_plan()` / `tailor_documents()` | GenerationService |
| `tailor.plan_validator.validate_plan_extended()` | GenerationService |

None of the above files were modified.

---

## Key design decisions

### JobScraperService
- Primary path delegates to the existing generator's `scrape_job_url()` which handles LinkedIn, Wellfound, and generic JSON-LD.
- Falls back to `ScrapingClient.fetch()` (httpx → Playwright) if the generator scraper fails (e.g. Playwright not available in a sandboxed backend container).
- Returns `JobScrapedData` dataclass, not `JobData`, so the scraper and normalizer are decoupled.

### GenerationService
- Mirrors `cli._run_two_phase()` logic (2 attempts, repair on attempt 2) without any interactive I/O.
- Creates `GenerationRun` with `status="running"` before calling the LLM so partial failures are visible.
- Rendering (DOCX/PDF) and storage upload are **not** called here; that happens in the API layer so the service stays pure and testable.
- Falls back to single-pass `tailor_documents()` if both Phase 1 attempts fail.

### RenderingService
- Uses `tempfile.NamedTemporaryFile` because `save_doc_from_template()` requires file paths.
- Cleans up temp files in a `finally` block.

### UsagePolicyService
- Free plan: checks `count_succeeded_by_user_id` against `FREE_TIER_GENERATION_LIMIT` (2).
- Paid plans: requires `subscription_status in ("active", "trialing")`.
- Raises HTTP 402 on limit breach; does not write to DB.

### EvaluationService
- Wraps `tailor.assess.score_single()` if it exists; degrades gracefully to null scores if the API is unavailable.
- Upserts the `EvaluationRun` record (create or update) so re-evaluation is safe.

---

## What was intentionally deferred

- DOCX/PDF rendering triggered from the API layer (not GenerationService)
- Async generation (FastAPI BackgroundTasks or Celery) — synchronous for v1
- Full `tailor.assess` integration (score_single API may need adjustment)
- Stripe price-to-plan mapping (read from env vars at runtime)
- PromptAssemblyService overrides for user-supplied candidate profiles (Phase 8)

---

## Compatibility

- CLI generator (`python -m tailor`) untouched — **469 tests pass**
- All new service modules import cleanly with no secrets required
- Backend health endpoint still HTTP 200

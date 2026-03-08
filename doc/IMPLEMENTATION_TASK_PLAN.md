# IMPLEMENTATION_TASK_PLAN.md

# AI Resume Tailoring SaaS
## Implementation Task Plan v0.1

This document defines the **exact project structure** and the **implementation order** for the coding agent.

The goal is to enable autonomous development with minimal manual intervention.

---

# 1. Primary Objective

Build an MVP SaaS platform that allows a user to:

1. Register / log in
2. Upload a master resume
3. Fill a candidate profile
4. Provide a job description by URL or manual paste
5. Generate a tailored resume and cover letter
6. Download generated DOCX / PDF artifacts
7. View basic generation history
8. Use free-tier quota and paid subscription plans

Primary focus for v1:

- software engineers and adjacent technical roles
- desktop-first workflow
- clean, modern, simple UI
- backend-first correctness and traceability

---

# 2. Exact Repository Structure

```text
ai-resume-saas/
├─ README.md
├─ .gitignore
├─ .env.example
├─ docker-compose.yml
├─ Makefile
├─ docs/
│  ├─ IMPLEMENTATION_SPEC.md
│  ├─ IMPLEMENTATION_TASK_PLAN.md
│  ├─ API_CONTRACTS.md
│  ├─ DB_SCHEMA.md
│  └─ PROMPT_STRATEGY.md
├─ scripts/
│  ├─ dev_backend.sh
│  ├─ dev_frontend.sh
│  ├─ run_migrations.sh
│  ├─ seed_dev_data.py
│  └─ create_admin_user.py
├─ backend/
│  ├─ pyproject.toml
│  ├─ alembic.ini
│  ├─ .env.example
│  ├─ tests/
│  │  ├─ conftest.py
│  │  ├─ unit/
│  │  ├─ integration/
│  │  └─ api/
│  ├─ alembic/
│  │  ├─ env.py
│  │  ├─ script.py.mako
│  │  └─ versions/
│  └─ app/
│     ├─ main.py
│     ├─ config.py
│     ├─ logging.py
│     ├─ dependencies.py
│     ├─ constants.py
│     ├─ db/
│     │  ├─ base.py
│     │  ├─ session.py
│     │  ├─ models/
│     │  │  ├─ user.py
│     │  │  ├─ candidate_profile.py
│     │  │  ├─ structured_resume.py
│     │  │  ├─ job_description.py
│     │  │  ├─ generation_run.py
│     │  │  ├─ tailored_document.py
│     │  │  ├─ evaluation_run.py
│     │  │  └─ billing.py
│     │  └─ repositories/
│     │     ├─ user_repository.py
│     │     ├─ candidate_profile_repository.py
│     │     ├─ structured_resume_repository.py
│     │     ├─ job_description_repository.py
│     │     ├─ generation_run_repository.py
│     │     ├─ tailored_document_repository.py
│     │     └─ evaluation_run_repository.py
│     ├─ schemas/
│     │  ├─ common.py
│     │  ├─ auth.py
│     │  ├─ user.py
│     │  ├─ candidate_profile.py
│     │  ├─ structured_resume.py
│     │  ├─ job_description.py
│     │  ├─ generation.py
│     │  ├─ tailored_document.py
│     │  ├─ evaluation.py
│     │  ├─ billing.py
│     │  └─ admin.py
│     ├─ api/
│     │  ├─ router.py
│     │  ├─ health.py
│     │  ├─ auth.py
│     │  ├─ candidate_profile.py
│     │  ├─ resume.py
│     │  ├─ job_description.py
│     │  ├─ generation.py
│     │  ├─ documents.py
│     │  ├─ billing.py
│     │  ├─ admin.py
│     │  └─ webhooks.py
│     ├─ services/
│     │  ├─ auth_service.py
│     │  ├─ resume_parser_service.py
│     │  ├─ candidate_profile_service.py
│     │  ├─ job_scraper_service.py
│     │  ├─ job_normalizer_service.py
│     │  ├─ prompt_assembly_service.py
│     │  ├─ generation_service.py
│     │  ├─ rendering_service.py
│     │  ├─ storage_service.py
│     │  ├─ billing_service.py
│     │  ├─ evaluation_service.py
│     │  ├─ usage_policy_service.py
│     │  └─ stats_service.py
│     ├─ clients/
│     │  ├─ openai_client.py
│     │  ├─ storage_client.py
│     │  ├─ stripe_client.py
│     │  ├─ supabase_client.py
│     │  └─ scraping_client.py
│     ├─ prompts/
│     │  ├─ resume_tailor/
│     │  │  ├─ system.md
│     │  │  ├─ output_schema.json
│     │  │  └─ version.txt
│     │  ├─ cover_letter/
│     │  │  ├─ system.md
│     │  │  ├─ output_schema.json
│     │  │  └─ version.txt
│     │  └─ evaluation/
│     │     ├─ system.md
│     │     ├─ output_schema.json
│     │     └─ version.txt
│     ├─ domain/
│     │  ├─ enums.py
│     │  ├─ errors.py
│     │  ├─ types.py
│     │  └─ policies.py
│     ├─ utils/
│     │  ├─ datetime.py
│     │  ├─ json_schema.py
│     │  ├─ file_hash.py
│     │  ├─ text.py
│     │  └─ ids.py
│     └─ workers/
│        ├─ tasks.py
│        └─ queue.py
├─ frontend/
│  ├─ package.json
│  ├─ next.config.ts
│  ├─ tsconfig.json
│  ├─ .env.example
│  ├─ public/
│  │  └─ logo.svg
│  └─ src/
│     ├─ app/
│     │  ├─ layout.tsx
│     │  ├─ page.tsx
│     │  ├─ login/page.tsx
│     │  ├─ dashboard/page.tsx
│     │  ├─ onboarding/page.tsx
│     │  ├─ resumes/page.tsx
│     │  ├─ jobs/page.tsx
│     │  ├─ generate/page.tsx
│     │  ├─ history/page.tsx
│     │  ├─ billing/page.tsx
│     │  └─ admin/page.tsx
│     ├─ components/
│     │  ├─ ui/
│     │  ├─ layout/
│     │  ├─ forms/
│     │  ├─ candidate-profile/
│     │  ├─ resume/
│     │  ├─ job-description/
│     │  ├─ generation/
│     │  └─ billing/
│     ├─ lib/
│     │  ├─ api.ts
│     │  ├─ auth.ts
│     │  ├─ utils.ts
│     │  ├─ validators.ts
│     │  └─ constants.ts
│     ├─ hooks/
│     │  ├─ useAuth.ts
│     │  ├─ useCandidateProfile.ts
│     │  ├─ useStructuredResume.ts
│     │  ├─ useGeneration.ts
│     │  └─ useBilling.ts
│     ├─ types/
│     │  ├─ api.ts
│     │  ├─ candidate-profile.ts
│     │  ├─ resume.ts
│     │  ├─ job.ts
│     │  ├─ generation.ts
│     │  └─ billing.ts
│     └─ styles/
│        └─ globals.css
└─ infra/
   ├─ railway/
   │  └─ railway.toml
   ├─ vercel/
   │  └─ project.json
   ├─ docker/
   │  ├─ backend.Dockerfile
   │  └─ nginx.conf
   └─ env/
      ├─ backend.env.example
      └─ frontend.env.example
```

---

# 3. Coding Principles

The coding agent must follow these principles:

1. Prefer simple and explicit code over abstraction-heavy design.
2. Keep API route handlers thin.
3. Put workflow logic into services.
4. Keep database models separate from Pydantic schemas.
5. Store structured flexible data in PostgreSQL JSONB.
6. Store files and large artifacts in S3-compatible storage.
7. Treat prompts as versioned files, not inline Python strings.
8. Add tests for every major service and API flow.
9. Avoid premature optimization.
10. Keep code readable for future manual maintenance.

---

# 4. Build Order

The system should be implemented in the following order:

1. Repository scaffold
2. Backend skeleton
3. Frontend skeleton
4. Configuration management
5. Database models and migrations
6. Auth integration
7. Candidate profile CRUD
8. Resume upload and parsing
9. Job description input / scraping
10. Generation pipeline
11. Document rendering
12. Artifact download
13. History
14. Billing and usage policy
15. Admin endpoints
16. Tests
17. Deployment configuration
18. Final polish and documentation

---

# 5. Task List

## Phase 1 — Repository and Tooling Foundation

### Task 1. Create repository skeleton
Create the exact folder structure described above.

### Task 2. Add root repo files
Create:
- README.md
- .gitignore
- .env.example
- docker-compose.yml
- Makefile

### Task 3. Add docs placeholders
Create:
- docs/IMPLEMENTATION_SPEC.md
- docs/IMPLEMENTATION_TASK_PLAN.md
- docs/API_CONTRACTS.md
- docs/DB_SCHEMA.md
- docs/PROMPT_STRATEGY.md

### Task 4. Add scripts directory
Create helper scripts:
- scripts/dev_backend.sh
- scripts/dev_frontend.sh
- scripts/run_migrations.sh
- scripts/seed_dev_data.py
- scripts/create_admin_user.py

### Task 5. Initialize backend Python project
Create backend/pyproject.toml with dependencies for:
- fastapi
- uvicorn
- sqlalchemy
- alembic
- pydantic
- psycopg
- httpx
- pytest
- python-multipart
- boto3 or S3-compatible client
- stripe
- pdf/docx parsing libs

### Task 6. Initialize frontend project
Create Next.js app with:
- TypeScript
- TailwindCSS
- shadcn/ui baseline
- ESLint / Prettier defaults

---

## Phase 2 — Backend Core Skeleton

### Task 7. Create backend config system
Create:
- backend/app/config.py
- env-driven settings model
- support local/dev/prod configuration

### Task 8. Create backend entrypoint
Create:
- backend/app/main.py
- FastAPI app factory
- register middleware
- include API router
- basic health endpoint

### Task 9. Create central API router
Create:
- backend/app/api/router.py
- route inclusion for all endpoint groups

### Task 10. Add health endpoint
Create:
- backend/app/api/health.py
Return simple status and version payload.

### Task 11. Add logging setup
Create:
- backend/app/logging.py
Structured logging preferred.

### Task 12. Add shared constants and dependencies
Create:
- backend/app/constants.py
- backend/app/dependencies.py

---

## Phase 3 — Database Layer

### Task 13. Create DB base and session
Create:
- backend/app/db/base.py
- backend/app/db/session.py

### Task 14. Configure Alembic
Create Alembic setup and verify migrations run locally.

### Task 15. Create user SQLAlchemy model
Create:
- backend/app/db/models/user.py

Fields:
- id
- email
- created_at
- plan_type
- stripe_customer_id
- free_generations_used
- role

### Task 16. Create candidate_profile model
Create:
- backend/app/db/models/candidate_profile.py

Fields:
- id
- user_id
- profile_version
- profile_jsonb
- created_at
- updated_at

### Task 17. Create structured_resume model
Create:
- backend/app/db/models/structured_resume.py

Fields:
- id
- user_id
- resume_jsonb
- source_file_url
- created_at

### Task 18. Create job_description model
Create:
- backend/app/db/models/job_description.py

Fields:
- id
- user_id
- source_url
- raw_text
- parsed_metadata_jsonb
- created_at

### Task 19. Create generation_run model
Create:
- backend/app/db/models/generation_run.py

Fields:
- id
- user_id
- run_type
- status
- model_name
- prompt_version
- input_snapshot_jsonb
- raw_response
- parsed_output_jsonb
- token_input
- token_output
- cost_estimate
- started_at
- completed_at
- error_message

### Task 20. Create tailored_document model
Create:
- backend/app/db/models/tailored_document.py

Fields:
- id
- user_id
- generation_run_id
- company_name
- role_title
- resume_jsonb
- cover_letter_jsonb
- resume_docx_url
- resume_pdf_url
- cover_letter_docx_url
- cover_letter_pdf_url
- created_at

### Task 21. Create evaluation_run model
Create:
- backend/app/db/models/evaluation_run.py

Fields:
- id
- generation_run_id
- truthfulness_score
- role_fit_score
- clarity_score
- seniority_score
- integrated_score
- created_at

### Task 22. Create billing model
Create:
- backend/app/db/models/billing.py
Store subscription and billing state needed by the app.

### Task 23. Create initial migration
Generate first Alembic migration with all MVP tables.

### Task 24. Create repositories
Create repository files for each major entity.

Repositories must only do CRUD and query operations.

---

## Phase 4 — Pydantic Schemas and JSON Models

### Task 25. Create shared schema module
Create:
- backend/app/schemas/common.py

### Task 26. Create auth schemas
Create:
- backend/app/schemas/auth.py
- backend/app/schemas/user.py

### Task 27. Create candidate profile schema
Create:
- backend/app/schemas/candidate_profile.py

Must include validation for flexible but structured candidate profile JSON.

### Task 28. Create structured resume schema
Create:
- backend/app/schemas/structured_resume.py

Must support:
- name
- contacts
- summary
- experience entries
- technical skills

### Task 29. Create job description schemas
Create:
- backend/app/schemas/job_description.py

### Task 30. Create generation schemas
Create:
- backend/app/schemas/generation.py

Include request/response models for generation API.

### Task 31. Create tailored document schemas
Create:
- backend/app/schemas/tailored_document.py

### Task 32. Create evaluation and billing schemas
Create:
- backend/app/schemas/evaluation.py
- backend/app/schemas/billing.py
- backend/app/schemas/admin.py

---

## Phase 5 — External Clients and Integrations

### Task 33. Create OpenAI client
Create:
- backend/app/clients/openai_client.py

Responsibilities:
- model call wrapper
- retries
- token accounting extraction
- error normalization

### Task 34. Create storage client
Create:
- backend/app/clients/storage_client.py

Responsibilities:
- upload file
- download signed URL
- delete file
- S3-compatible API

### Task 35. Create Stripe client
Create:
- backend/app/clients/stripe_client.py

### Task 36. Create Supabase client wrapper
Create:
- backend/app/clients/supabase_client.py

### Task 37. Create scraping client
Create:
- backend/app/clients/scraping_client.py

Keep generic; do not over-engineer job-board-specific scraping in v1.

---

## Phase 6 — Service Layer

### Task 38. Create auth service
Create:
- backend/app/services/auth_service.py

Responsibilities:
- current user resolution
- auth helper methods
- admin checks

### Task 39. Create candidate profile service
Create:
- backend/app/services/candidate_profile_service.py

Responsibilities:
- create/update/get current profile
- validate input payloads
- optional profile versioning behavior

### Task 40. Create resume parser service
Create:
- backend/app/services/resume_parser_service.py

Responsibilities:
- accept uploaded PDF/DOCX
- parse text
- transform to internal structured resume JSON
- preserve source file URL

### Task 41. Create job scraper service
Create:
- backend/app/services/job_scraper_service.py

Responsibilities:
- fetch JD from supported URLs
- normalize extracted text
- graceful fallback errors

### Task 42. Create job normalizer service
Create:
- backend/app/services/job_normalizer_service.py

Responsibilities:
- normalize company name
- role title
- JD text cleanup

### Task 43. Create prompt assembly service
Create:
- backend/app/services/prompt_assembly_service.py

Responsibilities:
- load prompt files
- load schema files
- assemble system + candidate + resume + JD + optional user prompt
- return final prompt payload and prompt version

### Task 44. Create storage service
Create:
- backend/app/services/storage_service.py

Thin wrapper over storage client.

### Task 45. Create rendering service
Create:
- backend/app/services/rendering_service.py

Responsibilities:
- convert generated structured output into DOCX
- optionally generate PDF
- store artifacts in object storage
- return artifact URLs

### Task 46. Create usage policy service
Create:
- backend/app/services/usage_policy_service.py

Responsibilities:
- enforce free-tier quota
- enforce paid plan limits
- provide upgrade-required result when blocked

### Task 47. Create billing service
Create:
- backend/app/services/billing_service.py

Responsibilities:
- current plan state
- Stripe subscription sync
- plan transitions

### Task 48. Create generation service
Create:
- backend/app/services/generation_service.py

This is the core workflow service.

Responsibilities:
1. verify usage policy
2. load candidate profile
3. load structured resume
4. load job description
5. create generation_run as pending
6. build input snapshot
7. assemble prompt
8. call OpenAI client
9. validate parsed output
10. render DOCX/PDF
11. create tailored_document
12. update generation_run as succeeded/failed
13. return final response payload

### Task 49. Create evaluation service
Create:
- backend/app/services/evaluation_service.py

Responsibilities:
- evaluate a single generation run
- store evaluation results

### Task 50. Create stats service
Create:
- backend/app/services/stats_service.py

Responsibilities:
- aggregate basic system metrics for admin dashboard

---

## Phase 7 — Prompt Assets

### Task 51. Create prompt files
Create:
- backend/app/prompts/resume_tailor/system.md
- backend/app/prompts/resume_tailor/output_schema.json
- backend/app/prompts/resume_tailor/version.txt

### Task 52. Create cover letter prompt files
Create:
- backend/app/prompts/cover_letter/system.md
- backend/app/prompts/cover_letter/output_schema.json
- backend/app/prompts/cover_letter/version.txt

### Task 53. Create evaluation prompt files
Create:
- backend/app/prompts/evaluation/system.md
- backend/app/prompts/evaluation/output_schema.json
- backend/app/prompts/evaluation/version.txt

---

## Phase 8 — API Endpoints

### Task 54. Create auth API
Create:
- backend/app/api/auth.py

### Task 55. Create candidate profile API
Create:
- backend/app/api/candidate_profile.py

Endpoints:
- GET /candidate-profile
- POST /candidate-profile
- PUT /candidate-profile

### Task 56. Create resume API
Create:
- backend/app/api/resume.py

Endpoints:
- POST /resume/upload
- GET /resumes
- GET /resumes/{id}

### Task 57. Create job description API
Create:
- backend/app/api/job_description.py

Endpoints:
- POST /job-description/scrape
- POST /job-description/manual
- GET /job-descriptions
- GET /job-descriptions/{id}

### Task 58. Create generation API
Create:
- backend/app/api/generation.py

Endpoints:
- POST /generate
- GET /generations
- GET /generations/{id}

### Task 59. Create document download API
Create:
- backend/app/api/documents.py

Endpoints:
- GET /documents/{id}/download
- GET /documents/{id}

### Task 60. Create billing API
Create:
- backend/app/api/billing.py

Endpoints:
- GET /billing/status
- POST /billing/create-checkout-session

### Task 61. Create Stripe webhooks API
Create:
- backend/app/api/webhooks.py

Endpoints:
- POST /webhooks/stripe

### Task 62. Create admin API
Create:
- backend/app/api/admin.py

Endpoints:
- POST /admin/evaluate-run
- GET /admin/system-stats

---

## Phase 9 — Frontend MVP

### Task 63. Create app shell
Create:
- frontend/src/app/layout.tsx
- frontend/src/app/page.tsx

### Task 64. Create frontend API client
Create:
- frontend/src/lib/api.ts

Responsibilities:
- authenticated requests
- typed response helpers
- error handling

### Task 65. Create auth utilities
Create:
- frontend/src/lib/auth.ts
- frontend/src/hooks/useAuth.ts

### Task 66. Create login page
Create:
- frontend/src/app/login/page.tsx

### Task 67. Create dashboard page
Create:
- frontend/src/app/dashboard/page.tsx

Show:
- current profile state
- resume upload state
- job input entry points
- recent generations

### Task 68. Create onboarding page
Create:
- frontend/src/app/onboarding/page.tsx

Purpose:
guide user through:
1. resume upload
2. profile completion
3. first generation

### Task 69. Create candidate profile components
Create components under:
- frontend/src/components/candidate-profile/

### Task 70. Create resume upload UI
Create components under:
- frontend/src/components/resume/

### Task 71. Create job description UI
Create components under:
- frontend/src/components/job-description/

Support:
- URL scrape
- manual paste

### Task 72. Create generation UI
Create:
- frontend/src/app/generate/page.tsx
- frontend/src/components/generation/

### Task 73. Create history page
Create:
- frontend/src/app/history/page.tsx

### Task 74. Create billing page
Create:
- frontend/src/app/billing/page.tsx
- frontend/src/components/billing/

### Task 75. Create admin page
Create:
- frontend/src/app/admin/page.tsx

Basic metrics only.

---

## Phase 10 — Testing

### Task 76. Add pytest configuration
Create:
- backend/tests/conftest.py

### Task 77. Add unit tests for schemas
Test schema validation behavior.

### Task 78. Add unit tests for resume parser service
Test parsing and normalization logic.

### Task 79. Add unit tests for prompt assembly service
Test prompt composition and version reporting.

### Task 80. Add unit tests for usage policy service
Test free-tier and plan-limit rules.

### Task 81. Add unit tests for generation service
Mock OpenAI and storage/render dependencies.

### Task 82. Add API tests for candidate profile endpoints
### Task 83. Add API tests for resume upload endpoints
### Task 84. Add API tests for job description endpoints
### Task 85. Add API tests for generation endpoints
### Task 86. Add integration test for full happy-path generation flow
Test:
- create user
- upload resume
- create profile
- add job description
- run generation
- fetch document URL

### Task 87. Add billing webhook tests
### Task 88. Add admin API tests

Coverage target:
- backend >= 70%

---

## Phase 11 — Deployment and Operations

### Task 89. Create Railway config
Create:
- infra/railway/railway.toml

### Task 90. Create Vercel config
Create:
- infra/vercel/project.json

### Task 91. Create Docker backend file
Create:
- infra/docker/backend.Dockerfile

### Task 92. Create environment examples
Create:
- infra/env/backend.env.example
- infra/env/frontend.env.example

### Task 93. Finalize local docker-compose setup
Allow local backend + postgres + optional storage emulator if useful.

### Task 94. Add seed scripts
Provide minimal dev data for fast manual testing.

---

## Phase 12 — Final Quality Pass

### Task 95. Review all API error handling
Ensure predictable error response format.

### Task 96. Review all service logging
Ensure every generation run is traceable.

### Task 97. Review auth protections
Ensure all user-specific endpoints require auth.

### Task 98. Review admin protections
Ensure admin endpoints are restricted.

### Task 99. Review storage lifecycle assumptions
Ensure artifact URLs and metadata are consistent.

### Task 100. Final documentation pass
Update README and docs to match actual implementation.

---

# 6. MVP Completion Criteria

The implementation is complete when all conditions below are true:

## User workflow
A non-admin user can:
1. register or log in
2. upload a resume
3. create or update a candidate profile
4. create a job description from URL or manual paste
5. generate a tailored resume and cover letter
6. download generated artifacts
7. view history of generated documents

## Billing workflow
The system can:
1. allow 2 free generations
2. block further generation after free quota is exhausted
3. create Stripe checkout session
4. update plan status from Stripe webhook

## Admin workflow
An admin can:
1. view system stats
2. run evaluation for a generation

## Engineering quality
1. backend tests exist and pass
2. coverage >= 70%
3. migrations run cleanly
4. app starts locally with documented commands
5. no critical endpoint lacks auth or validation

---

# 7. Implementation Guidance for Coding Agent

The coding agent should follow this execution strategy:

1. Implement vertical slices, not all files at once.
2. Start with health check and DB wiring.
3. Then implement candidate profile CRUD.
4. Then resume upload and parsing.
5. Then JD input.
6. Then generation flow.
7. Then rendering and download.
8. Then billing.
9. Then admin and evaluation.
10. Add tests continuously, not at the end.

Important:
- Prefer working software over perfect abstractions.
- Do not invent extra features not listed in spec.
- Do not create unnecessary microservices.
- Keep v1 monolithic.
- Use placeholders only where external dependencies are not yet wired.

---

# 8. Suggested First 15 Concrete Files to Implement

The agent should start with these files first:

```text
backend/app/main.py
backend/app/config.py
backend/app/db/base.py
backend/app/db/session.py
backend/app/api/router.py
backend/app/api/health.py
backend/app/db/models/user.py
backend/app/db/models/candidate_profile.py
backend/app/db/models/structured_resume.py
backend/app/db/models/job_description.py
backend/app/db/models/generation_run.py
backend/app/db/models/tailored_document.py
backend/app/schemas/candidate_profile.py
backend/app/services/candidate_profile_service.py
backend/app/api/candidate_profile.py
```

That establishes the first working backend slice quickly.

---

# 9. Suggested First End-to-End Slice

The first fully completed slice should be:

1. health endpoint
2. DB connection
3. user model
4. candidate profile model
5. candidate profile schema
6. candidate profile CRUD API
7. tests for candidate profile API

Only after that should the agent proceed to resume upload and generation.

---

# 10. Notes

This plan is intentionally explicit to improve coding-agent execution quality.

The preferred strategy is:
- build small working increments
- keep files readable
- maintain test coverage as features land
- avoid over-design in v1

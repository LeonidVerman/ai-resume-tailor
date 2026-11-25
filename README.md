# ai-resume-tailor

AI-powered resume tailoring tool — generates tailored resumes and cover letters for specific job descriptions using a two-phase LLM pipeline.

**Version 0.8.9.2.BETA** — SaaS PDF-path layout fixes: stored-IR geometry round-trip, stale section leftovers, download-fallback converter (#158); run-data archive includes the original resume template (#159)

---

## Current System (CLI Generator)

The existing generator is a fully working command-line tool. It remains the primary working system and is not affected by the SaaS transition in progress.

### Quick start

```bash
pip install -e .

# Tailor from a job board URL (scrapes via Playwright)
tailor --position-url <URL>

# Tailor from a local job description file
tailor --position-desc jobs/acme.txt

# Single-pass mode (skip Phase 1 plan)
tailor --position-desc jobs/acme.txt --simple

# Debug mode (write debug JSON only, skip docx/pdf)
tailor --position-desc jobs/acme.txt --debug
```

Or via module:

```bash
python -m tailor --position-desc jobs/acme.txt
```

### Required files

| Path | Description |
|---|---|
| `templates/Leonid_Verman_Resume_Template.docx` | Master resume `.docx` template |
| `templates/Leonid_Verman_Cover_Letter_Template.docx` | Cover letter template |
| `profile/candidate_profile.json` | Candidate profile JSON |
| `prompts/` | LLM prompt files |
| `schemas/phase1_output.json` | Phase 1 output schema |
| `config/domain_translation_rules.json` | Domain translation rules |
| `.env` | API keys and config overrides |

### Environment variables

Copy `.env.example` to `.env` and fill in your values.

### Running tests

```bash
pytest tests/
```

### Calibration scripts

```bash
bash tests/run_calibrate.sh
bash tests/run_assess.sh
```

---

## SaaS Transition (In Progress)

This repository is incrementally transitioning toward a SaaS platform.

The SaaS product will allow users to:
1. Register / log in
2. Upload a master resume
3. Fill a candidate profile
4. Provide a job description (URL or paste)
5. Generate a tailored resume and cover letter
6. Download DOCX / PDF artifacts

**Spec documents:** `doc/IMPLEMENTATION_SPEC.md`, `doc/IMPLEMENTATION_TASK_PLAN.md`

### Planned stack

- **Backend:** FastAPI + SQLAlchemy + Alembic + PostgreSQL (Supabase)
- **Frontend:** Next.js + TypeScript + TailwindCSS
- **Auth:** Supabase Auth (JWT)
- **Storage:** S3-compatible (Cloudflare R2)
- **Payments:** Stripe

### Repository layout (target)

```
backend/     → FastAPI application (future)
frontend/    → Next.js application (future)
infra/       → Deployment configs (future)
scripts/     → Dev helper scripts (future)
src/tailor/  → Existing CLI generator (current, preserved)
tests/       → Existing test suite (current, preserved)
doc/         → Specification documents
```

The current CLI generator at `src/tailor/` will be integrated into the backend as a service in a later phase.

---

## Project structure

```
src/tailor/         CLI generator source
tests/              Generator test suite and calibration scripts
prompts/            LLM prompt files
schemas/            JSON schemas for LLM output validation
config/             Domain translation rules and config
templates/          DOCX resume/cover letter templates
profile/            Candidate profile JSON
doc/                Specification and design documents
backend/            Future FastAPI backend (scaffolded, not yet implemented)
frontend/           Future Next.js frontend (scaffolded, not yet implemented)
```

# Database Schema

> **Status:** Placeholder — not yet implemented.
> This document describes the PostgreSQL schema for the SaaS backend.
> See `IMPLEMENTATION_SPEC.md` §4 for the initial table list.

---

## Overview

Database: PostgreSQL (hosted on Supabase)
ORM: SQLAlchemy 2.x
Migrations: Alembic

Flexible/structured document fields use PostgreSQL JSONB.
Large artifacts (DOCX, PDF) are stored in S3-compatible object storage, not the database.

---

## Tables

### users

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| email | VARCHAR UNIQUE | |
| created_at | TIMESTAMPTZ | |
| plan_type | VARCHAR | free \| starter \| pro |
| stripe_customer_id | VARCHAR | nullable |
| free_generations_used | INTEGER | default 0 |
| role | VARCHAR | user \| admin |

### candidate_profiles

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| profile_version | VARCHAR | |
| profile_jsonb | JSONB | candidate profile document |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |

### structured_resumes

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| resume_jsonb | JSONB | structured resume document |
| source_file_url | VARCHAR | R2 URL of original upload |
| created_at | TIMESTAMPTZ | |

### job_descriptions

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| source_url | VARCHAR | nullable; original URL |
| raw_text | TEXT | |
| parsed_metadata_jsonb | JSONB | company, title, etc. |
| created_at | TIMESTAMPTZ | |

### generation_runs

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| run_type | VARCHAR | single_pass |
| status | VARCHAR | pending \| running \| succeeded \| failed |
| model_name | VARCHAR | |
| prompt_version | VARCHAR | |
| input_snapshot_jsonb | JSONB | inputs at generation time |
| raw_response | TEXT | raw LLM response |
| parsed_output_jsonb | JSONB | validated parsed output |
| token_input | INTEGER | |
| token_output | INTEGER | |
| cost_estimate | NUMERIC | USD |
| started_at | TIMESTAMPTZ | |
| completed_at | TIMESTAMPTZ | nullable |
| error_message | TEXT | nullable |

### tailored_documents

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| user_id | UUID FK → users | |
| generation_run_id | UUID FK → generation_runs | |
| company_name | VARCHAR | |
| role_title | VARCHAR | |
| resume_jsonb | JSONB | |
| cover_letter_jsonb | JSONB | |
| resume_docx_url | VARCHAR | R2 URL |
| resume_pdf_url | VARCHAR | R2 URL; nullable |
| cover_letter_docx_url | VARCHAR | R2 URL |
| cover_letter_pdf_url | VARCHAR | R2 URL; nullable |
| created_at | TIMESTAMPTZ | |

### evaluation_runs

| Column | Type | Notes |
|---|---|---|
| id | UUID PK | |
| generation_run_id | UUID FK → generation_runs | |
| truthfulness_score | NUMERIC | 0–1 |
| role_fit_score | NUMERIC | 0–1 |
| clarity_score | NUMERIC | 0–1 |
| seniority_score | NUMERIC | 0–1 |
| integrated_score | NUMERIC | 0–1 |
| created_at | TIMESTAMPTZ | |

---

_SQLAlchemy model files and Alembic migrations will be created in Phase 3 of the task plan._

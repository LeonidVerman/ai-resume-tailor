
# AI Resume Tailoring SaaS
## Implementation Specification v0.1

---

# 1. System Overview

## Purpose
The system is a SaaS platform that automatically generates tailored resumes and cover letters for job descriptions.

Primary focus for initial release:

Software engineers and related technical roles

Users workflow:
1. Upload resume
2. Fill candidate profile
3. Provide job description (URL or pasted text)
4. Generate tailored resume and cover letter
5. Download artifacts

Generation uses:
- structured candidate data
- LLM backend
- internal resume schema

---

# 2. Technology Stack

## Backend
Python 3.11+  
FastAPI  
Uvicorn  
Async-first architecture

## Database
PostgreSQL (Supabase)  
JSONB fields for structured documents  
SQLAlchemy 2.x ORM  
Alembic migrations

## Object Storage
S3-compatible storage  
Recommended: Cloudflare R2  

Used for:
- uploaded resumes
- rendered DOCX/PDF
- archived artifacts

## Authentication
Supabase Auth (JWT)

## Payments
Stripe

## Frontend
Next.js  
React  
TailwindCSS  
shadcn/ui

## LLM Integration
OpenAI API  
Example default model: gpt-5.2

---

# 3. System Architecture

User Browser  
↓  
Frontend (Next.js)  
↓  
FastAPI Backend  
↓  
PostgreSQL (Supabase)  
↓  
Object Storage (R2)  
↓  
OpenAI API

---

# 4. Database Schema

## users
- id
- email
- created_at
- plan_type
- stripe_customer_id
- free_generations_used

## candidate_profiles
- id
- user_id
- profile_version
- profile_jsonb
- created_at
- updated_at

## structured_resumes
- id
- user_id
- resume_jsonb
- source_file_url
- created_at

## job_descriptions
- id
- user_id
- source_url
- raw_text
- parsed_metadata_jsonb
- created_at

## generation_runs
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

## tailored_documents
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

## evaluation_runs
- id
- generation_run_id
- truthfulness_score
- role_fit_score
- clarity_score
- seniority_score
- integrated_score
- created_at

---

# 5. Data Structures

## Resume JSON Schema

```json
{
  "name": "string",
  "contacts": {
    "email": "string",
    "phone": "string",
    "location": "string"
  },
  "summary": "string",
  "experience": [
    {
      "company": "string",
      "role": "string",
      "start_date": "string",
      "end_date": "string",
      "bullets": ["string"]
    }
  ],
  "technical_skills": ["string"]
}
```

## Candidate Profile JSON Schema

```json
{
  "candidate_profile_version": "string",
  "candidate_summary": "string",
  "technical_skills": [],
  "architecture_patterns": [],
  "domain_experience": [],
  "leadership": {
    "team_size_max": 0,
    "responsibilities": []
  },
  "soft_skills": [],
  "ai_experience": []
}
```

---

# 6. Backend API

## Authentication
POST /auth/login  
POST /auth/register  
GET /auth/me

## Resume Upload
POST /resume/upload

## Candidate Profile
GET /candidate-profile  
POST /candidate-profile  
PUT /candidate-profile

## Job Description
POST /job-description/scrape  
POST /job-description/manual

## Resume Generation
POST /generate

## Artifact Download
GET /documents/{id}/download

## Admin APIs
POST /admin/evaluate-run  
GET /admin/system-stats

---

# 7. Backend Services

## Resume Parsing Service
- Parse PDF/DOCX
- Convert to structured resume JSON

## Job Board Scraping Service
Supports major boards (LinkedIn, Indeed, etc.)  
Fallback: manual paste

## Prompt Assembly Service
Combines:
- system prompts
- candidate profile
- resume
- job description
- user prompts

## Generation Service
- LLM invocation
- validation
- retry logic

## Rendering Service
Convert generated resume to DOCX and PDF

---

# 8. Frontend UI

Pages:
- Login
- Dashboard
- Resume Upload
- Candidate Profile Setup
- Job Description Input
- Generation Results
- History
- Billing

Design: wizard-style onboarding with minimal friction.

---

# 9. User Management

Authentication: Supabase Auth  
Authorization: JWT tokens

Roles:
- user
- admin

---

# 10. Usage Policies

Free tier:
- 2 resume generations

Paid plans:
- Starter
- Pro

Generation limits enforced before /generate.

---

# 11. Stripe Integration

Stripe handles subscriptions and payments.

Backend processes webhooks:
- invoice.payment_succeeded
- customer.subscription.updated

---

# 12. Testing

Unit tests:
- prompt assembly
- parsing
- generation logic
- schema validation

Integration tests:
- resume upload
- JD scraping
- generation pipeline

API tests:
- pytest
- httpx

LLM calls mocked during tests.

---

# 13. Security

- JWT authentication
- rate limiting
- input validation
- file size limits
- prompt injection mitigation

---

# 14. Deployment

Backend: Railway  
Frontend: Vercel  
Database: Supabase  
Storage: Cloudflare R2

---

# 15. Observability

Track:
- request logs
- LLM token usage
- generation success rate
- cost

---

# 16. Success Criteria

System considered complete when user can:

1. Register
2. Upload resume
3. Fill candidate profile
4. Input job description
5. Generate tailored resume
6. Download artifacts

Backend coverage target: ≥70%.

---

# 17. Phase 2 Enhancements

- prompt management system
- resume template library
- AI feedback loop
- ATS scoring
- job application tracking

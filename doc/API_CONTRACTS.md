# API Contracts

> **Status:** Placeholder — not yet implemented.
> This document will define the REST API contracts for the SaaS backend.
> See `IMPLEMENTATION_SPEC.md` §6 for the initial endpoint list.

---

## Conventions

- All endpoints are prefixed with `/api/v1/`
- Authentication via `Authorization: Bearer <JWT>` header
- Request and response bodies are JSON
- Errors return `{ "error": { "code": "...", "message": "..." } }`
- Timestamps are ISO 8601 UTC strings

---

## Auth

### POST /auth/login
### POST /auth/register
### GET /auth/me

---

## Resume

### POST /resume/upload
### GET /resumes
### GET /resumes/{id}

---

## Candidate Profile

### GET /candidate-profile
### POST /candidate-profile
### PUT /candidate-profile

---

## Job Description

### POST /job-description/scrape
### POST /job-description/manual
### GET /job-descriptions
### GET /job-descriptions/{id}

---

## Generation

### POST /generate
### GET /generations
### GET /generations/{id}

---

## Documents

### GET /documents/{id}
### GET /documents/{id}/download

---

## Billing

### GET /billing/status
### POST /billing/create-checkout-session

---

## Webhooks

### POST /webhooks/stripe

---

## Admin

### POST /admin/evaluate-run
### GET /admin/system-stats

---

_Detailed request/response schemas will be added as each endpoint is implemented._

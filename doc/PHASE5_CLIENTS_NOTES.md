# Phase 5 Client Notes

**Date:** 2026-03-07
**Tasks covered:** Phase 5, Tasks 33–37 from `IMPLEMENTATION_TASK_PLAN.md`

---

## Client modules added

| File | Task | Purpose |
|---|---|---|
| `backend/app/clients/openai_client.py` | 33 | OpenAI chat completions wrapper |
| `backend/app/clients/storage_client.py` | 34 | S3-compatible object storage (boto3) |
| `backend/app/clients/stripe_client.py` | 35 | Stripe checkout + subscription wrapper |
| `backend/app/clients/supabase_client.py` | 36 | Supabase SDK bootstrap wrapper |
| `backend/app/clients/scraping_client.py` | 37 | HTTP + Playwright scraping client |

---

## Existing repo code reused

### OpenAI client
Two patterns extracted directly from `src/tailor/core_generation/llm.py`:
- `_max_tokens_kwargs` — routes `max_tokens` vs `max_completion_tokens` based on model family regex
  (identical logic, now in `OpenAIClient._max_tokens_kwargs`)
- `_extract_usage` — normalizes `response.usage` into `TokenUsage(prompt, completion, total)`

The existing generator's `get_client()` singleton and all prompt/pipeline logic remain
**entirely unchanged** — this client is for future backend service use only.

### Scraping client
`ScrapingClient.get_rendered()` mirrors `scrape.get_rendered_html()` (Playwright headless Chromium,
`networkidle` wait state). The stealth init script mirrors `wellfound.py _STEALTH_INIT_SCRIPT`.
The existing `src/tailor/job/scrape.py` and `wellfound.py` are **unchanged**.

---

## Interface decisions

### OpenAIClient
- Instantiable class with lazy SDK client creation (easy to mock in tests)
- `complete(messages, ...)` — generic completion
- `complete_json(messages, json_schema, ...)` — structured output convenience wrapper
- `estimate_cost_usd(usage, model)` — rough cost accounting (not for billing)
- `CompletionResult` dataclass: `content`, `model`, `usage: TokenUsage`, `raw_response`
- No secrets required at import/instantiation; SDK reads `OPENAI_API_KEY` env var as fallback

### StorageClient
- Lazy boto3 client creation
- `upload_bytes(data, key)` / `upload_fileobj(fileobj, key)` → `UploadResult`
- `get_signed_url(key, expires_in)` → signed URL string
- `delete_object(key)` / `object_exists(key)`
- `public_base_url` for CDN-fronted buckets (avoids generating signed URLs for public content)

### StripeClient
- `create_checkout_session(price_id, ...)` → `CheckoutSessionResult`
- `get_or_create_customer(email, user_id)` → customer ID string
- `get_subscription_state(customer_id)` → `SubscriptionState`
- `construct_webhook_event(payload, sig_header)` → validated Stripe event
- No business rules here; plan transitions belong in billing service (Phase 14)

### SupabaseClientWrapper
- Intentionally lightweight — no production flows use Supabase yet
- Lazy dual client pattern: public (anon key) + admin (service role key)
- `verify_token(jwt)` / `get_user_by_id()` as auth helpers for Phase 6
- `is_configured()` — cheap check without a network call
- Full JWT verification deferred to Phase 6 auth service

### ScrapingClient
- `get(url)` — plain httpx fetch (no JS)
- `get_rendered(url, stealth=False)` — Playwright headless Chromium
- `fetch(url, force_playwright=False)` — smart: tries plain HTTP first, falls back to Playwright
- `ScrapeResult` dataclass: `url`, `status_code`, `html`, `js_rendered`, `ok`, `text`
- Board-specific parsing (JSON-LD, Next.js data, Apollo cache) deferred to job_scraper_service

---

## What was intentionally deferred

- Generator migration to use `OpenAIClient` (separate phase)
- Job board-specific scraping logic (future `job_scraper_service`)
- Stripe webhook event handling (Phase 14 billing service)
- Full Supabase JWT verification flow (Phase 6 auth service)
- S3 multipart upload for large files (not needed in v1)
- Async versions of clients (acceptable to add later if needed)

---

## Compatibility

- CLI generator (`python -m tailor`) untouched — 469 tests pass
- No secrets required at import time; all clients fail only when actually used
- Backend health endpoint still HTTP 200

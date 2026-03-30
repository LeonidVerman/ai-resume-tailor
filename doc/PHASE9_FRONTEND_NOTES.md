# Phase 9 — Frontend MVP Notes

## Overview

Phase 9 adds a clean, minimal SaaS frontend built with Next.js 14, TailwindCSS,
and lightweight hand-rolled components (no shadcn dependency). All 11 routes
build cleanly. The UI is tightly aligned with the Phase 8 backend API.

---

## Pages added

| Route | File | Purpose |
|-------|------|---------|
| `/` | `app/page.tsx` | Redirects → `/dashboard` (auth) or `/login` |
| `/login` | `app/login/page.tsx` | Dev-mode auth: enter User ID |
| `/dashboard` | `app/dashboard/page.tsx` | Overview, stat cards, quick actions, recent runs |
| `/onboarding` | `app/onboarding/page.tsx` | 3-step wizard: resume → profile → done |
| `/resumes` | `app/resumes/page.tsx` | Upload and list resumes |
| `/jobs` | `app/jobs/page.tsx` | Add (URL scrape or paste) and list job descriptions |
| `/generate` | `app/generate/page.tsx` | Select resume + JD, submit generation, view result |
| `/history` | `app/history/page.tsx` | Paginated list of generation runs |
| `/billing` | `app/billing/page.tsx` | Plan status, quota bar, upgrade CTAs |
| `/admin` | `app/admin/page.tsx` | System stats, evaluate-run action |

---

## Components added

### `components/layout/`
- `Sidebar.tsx` — dark sidebar with nav, resources section, admin link (role-gated), user footer
- `AppShell.tsx` — auth guard + layout wrapper used by all protected pages

### `components/ui/`
- `Button.tsx` — primary / secondary / ghost / danger variants, loading spinner
- `Card.tsx` — `Card`, `CardHeader`, `CardBody`, `CardFooter`
- `Badge.tsx` — default / success / warning / danger / info / purple
- `Input.tsx` — `Input` and `Textarea` with label, error, hint
- `Spinner.tsx` — loading spinner with optional label

### `components/candidate-profile/`
- `ProfileForm.tsx` — simplified editor for name, headline, summary, domains, role-fit themes.
  Full JSONB schema support (experience highlights, technical skills) deferred to later phase.

### `components/resume/`
- `ResumeUpload.tsx` — drag-and-drop + file picker; calls `POST /resumes/upload`
- `ResumeCard.tsx` — display card with selection state

### `components/job-description/`
- `JobDescriptionForm.tsx` — toggle between URL scrape and manual paste modes

### `components/generation/`
- `GenerationForm.tsx` — selects resume + JD, checks quota, calls `POST /generations`
- `GenerationResult.tsx` — shows run status, polls if needed, provides download links

### `components/billing/`
- `BillingStatus.tsx` — plan card with quota progress bar, upgrade plan cards

---

## Backend endpoints wired

| Endpoint | Used by |
|----------|---------|
| `GET /auth/me` | `useAuth`, login flow, dashboard |
| `GET /candidate-profile` | Onboarding, dashboard |
| `PUT /candidate-profile` | ProfileForm |
| `POST /resumes/upload` | ResumeUpload |
| `GET /resumes` | ResumesPage, GenerationForm |
| `POST /job-descriptions/scrape` | JobDescriptionForm |
| `POST /job-descriptions/manual` | JobDescriptionForm |
| `GET /job-descriptions` | JobsPage, GenerationForm |
| `DELETE /job-descriptions/{id}` | JobsPage |
| `POST /generations` | GenerationForm |
| `GET /generations` | HistoryPage, dashboard |
| `GET /documents/{id}` | GenerationResult |
| `GET /documents/{id}/download` | GenerationResult, HistoryPage (download links) |
| `GET /billing/status` | BillingPage, GenerationForm (quota check) |
| `POST /billing/create-checkout-session` | BillingPage upgrade flow |
| `GET /admin/system-stats` | AdminPage |
| `POST /admin/evaluate-run` | AdminPage |

---

## Lib / hooks

- `src/lib/api.ts` — typed API client; reads `NEXT_PUBLIC_API_URL` env;
  adds `X-User-Id` header from localStorage for all authenticated requests
- `src/lib/auth.ts` — localStorage helpers (`getStoredUserId`, `setStoredUserId`, `clearStoredUserId`)
- `src/lib/utils.ts` — `cn()`, `formatDate()`, `formatDateTime()`
- `src/hooks/useAuth.ts` — auth state hook; `login()` verifies against `/auth/me`;
  redirects to `/login` when unauthenticated

---

## Fully wired vs scaffolded

| Page / feature | Status |
|----------------|--------|
| Login (dev-mode) | Wired — X-User-Id auth |
| Dashboard | Wired — loads real data from all endpoints |
| Resume upload + list | Wired |
| Job description add (URL + manual) + list | Wired |
| Generation trigger + result | Wired |
| History (paginated) | Wired |
| Billing status + quota | Wired |
| Billing upgrade (Stripe) | Wired — returns 503 if Stripe not configured |
| Onboarding wizard | Wired — resume upload + profile form |
| Candidate profile (basic fields) | Wired (name, headline, summary, domains, themes) |
| Admin stats | Wired — requires role=admin |
| Admin evaluate-run | Wired — requires role=admin |
| Candidate profile (full JSONB) | Scaffolded — advanced fields deferred |
| Document download (DOCX/PDF) | Scaffolded — links to text endpoint until S3 wired |

---

## Auth limitations

Auth is **dev-mode only** (X-User-Id header bypass):
- The login page prompts for a User ID (UUID).
- The ID must exist in the database (created via API seed or DB insert).
- No password, no email/password flow, no Supabase JWT.
- Full Supabase JWT auth is deferred to Phase 10+.

When Supabase auth is added:
1. Replace `lib/auth.ts` with Supabase client `@supabase/supabase-js`
2. Update `lib/api.ts` to send `Authorization: Bearer <jwt>` instead of `X-User-Id`
3. Update `backend/app/dependencies.py` `get_current_user` to verify JWT

---

## Configuration

```
frontend/.env.local:
  NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1
```

CORS on backend defaults to `["http://localhost:3000"]` — matches Next.js dev server.

---

## Deferred items

- Full Supabase JWT login page
- Candidate profile advanced editor (experience highlights, technical skills)
- Document preview (inline text view before download)
- DOCX/PDF signed URL downloads (requires S3 storage wired in backend)
- Generation async polling UI (currently synchronous; backend will move to background)
- Responsive/mobile layout (currently desktop-first)
- Profile onboarding progress saved server-side
- Toast/notification system (currently inline error/success messages)

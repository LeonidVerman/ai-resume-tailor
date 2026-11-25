# CVRocket Guest Generation — Implementation Plan

Companion to `CVRocket_Guest_Generation_Design.pdf` (issue #155), refined by the
product decisions recorded 2026-07-27:

- Supabase anonymous JWTs for guest identity; registration flow unchanged;
  secure transactional **claim/merge** after registration (no upgrade-in-place).
- Guest identity created only after Turnstile succeeds and upload is initiated.
- Explicit Terms/Privacy consent before upload/classification, with timestamp
  and document versions stored.
- Auto-accepted resume-derived temporary profile, marked unreviewed.
- DB-backed guest/device/IP counters, global daily cost + concurrency caps,
  admin kill switch — all mandatory.
- Paste-only job description for guests; URL fetch stays registered-only.
- Unclaimed guest content deleted after 7 days; minimal usage/security/legal
  records retained longer.
- Three lifetime free generations total; a guest generation consumes one,
  leaving two after registration.
- Guest output: preview + resume/cover-letter **PDF only**. DOCX/TXT, View
  Changes, history and further generations require registration. Claimed
  artifacts appear in account history.
- `/try` public; root may initially redirect there (lightweight landing later).
- DB-backed funnel-events table + basic admin metrics in v1.

---

## Phase 0 — Schema & config foundations (1 migration set, no behavior change)

**Alembic migrations** (`backend/alembic/versions/`):

- `users`: add `is_anonymous BOOL NOT NULL DEFAULT false`,
  `guest_claimed_by VARCHAR NULL` (target user id after merge),
  `guest_purged_at`, `terms_version VARCHAR NULL`,
  `privacy_version VARCHAR NULL`, `legal_accepted_at TIMESTAMP NULL`.
- `guest_entitlements`: `user_id PK/FK`, `allowed INT DEFAULT 1`,
  `used INT DEFAULT 0`, `reserved INT DEFAULT 0`, `reserved_at TIMESTAMP NULL`,
  `updated_at`. (Separate table rather than overloading `billing` — guests
  have no billing row; SELECT … FOR UPDATE gives the doc's atomic
  reservation. `reserved_at` lets cleanup auto-release reservations older
  than 30 minutes so a crashed process never permanently consumes the
  entitlement.) **This table is the single source of truth for guest usage**
  — `users.free_generations_used` is NOT touched during the guest lifetime.
- `guest_devices`: `device_token_hash PK`, `first_seen_at`,
  `generation_used_at NULL`, `guest_user_id NULL`.
- `guest_abuse_events`: `id`, `user_id NULL`, `ip_daily_hash`, `event_type`,
  `risk_reason`, `created_at` (+ index on `ip_daily_hash, created_at`).
- `funnel_events`: `id`, `user_id NULL`, `event_type VARCHAR`,
  `meta JSONB NULL`, `created_at` (+ index on `event_type, created_at`).
- `candidate_profiles`: add `is_unreviewed BOOL NOT NULL DEFAULT false`.
- `structured_resumes`: add `sha256 VARCHAR(64) NULL` — checksum of the
  uploaded file, persisted for all uploads (guest and registered). No
  behavior attached yet; future use: dedup, merge logic, caching, abuse
  investigation.

**Config** (`backend/app/config.py` + `admin_config` keys, all admin-tunable):

- `guest_enabled` (kill switch, default **false**),
- `guest_daily_global_cap` (e.g. 50), `guest_concurrent_cap` (e.g. 3),
- `guest_ip_daily_limit` (default 3), `guest_ip_burst_limit` (e.g. 2/10 min),
- `guest_retention_days` (default 7),
- `turnstile_site_key` / `turnstile_secret` (env),
- `ip_hash_secret` (env; daily HMAC key derivation `HMAC(secret, ip + yyyymmdd)`).

## Phase 1 — Guest identity, consent, Turnstile (backend)

- **Supabase**: enable anonymous sign-ins in the project; do NOT enable
  Supabase-side captcha (Turnstile is verified by our backend so the token can
  gate more than token minting).
- `POST /api/v1/guest/session` (new router `backend/app/api/guest.py`, public):
  body `{turnstile_token, terms_version, privacy_version, device_token?}`.
  Steps: verify kill switch → verify Turnstile server-side → soft IP velocity
  check (`guest_abuse_events` by `ip_daily_hash`) → device-token check/mint
  (httpOnly signed cookie, hash stored in `guest_devices`) → create Supabase
  anonymous user (admin API) → `get_or_create_user` with `is_anonymous=true`,
  legal fields set → create `guest_entitlements` row → return the Supabase
  access/refresh tokens (stored by the frontend in the existing
  `art_access_token`/`art_refresh_token` keys so `frontend/src/lib/api.ts`
  works unchanged) → funnel event `guest_session_created`.
- `get_current_user` (`backend/app/dependencies.py`): surface
  `user.is_anonymous`; no other change — every existing owner check keeps
  working because the guest IS a normal `users` row.
- Legal gate: guests satisfy it via the consent recorded at session creation
  (timestamp + versions on the user row).

## Phase 2 — Guest-scoped pipeline restrictions (backend)

- `POST /resumes/upload`: for anonymous users — maximum one stored resume at
  a time; uploading another **replaces** the existing guest resume (deletes
  the previous row + storage objects and regenerates the temporary profile),
  so a wrong-file upload never strands the guest. Existing background
  classification is kept (it now runs only behind a Turnstile-minted
  identity).
- Job descriptions: anonymous users may only create via paste;
  URL-fetch endpoint returns 403 `GUEST_PASTE_ONLY`.
- Profile: after upload+parse, backend auto-runs the existing autofill draft
  (`profile_autofill_service`) and saves it as `CandidateProfile` with
  `is_unreviewed=true`; `onboarding_completed`-equivalent gate in
  `POST /generations` branches for `is_anonymous` (profile must exist, review
  not required; resume remains authoritative evidence).
- `POST /generations` guest path, before the existing pipeline:
  1. kill switch; 2. global daily cap (count of guest generations today);
  3. concurrency cap (sum of `reserved` across guest entitlements);
  4. device token present + unused; 5. IP velocity;
  6. **atomic reserve** on `guest_entitlements` (FOR UPDATE;
     `used + reserved >= allowed` → 429 with register CTA);
  on success `reserved-=1; used+=1`, stamp `guest_devices.generation_used_at`;
  on internal failure `reserved-=1` (credit released); `reserved_at` set on
  reserve, cleared on release. Every rejection writes a `guest_abuse_events`
  row.
- Credits: `guest_entitlements.used` is the sole guest usage counter. The
  lifetime "3 total" accounting is applied at claim time (Phase 4), not by
  double-writing `users.free_generations_used` during the guest lifetime.
- Documents: for anonymous users, list/download only PDF artifacts (resume +
  cover letter); DOCX/TXT return 403 `REGISTER_TO_UNLOCK`. Existing
  owner-check download proxy handles authorization as-is.

## Phase 3 — Frontend `/try` + public shell

- New `PublicShell` (no `AppShell` auth redirect): logo, minimal footer with
  legal links.
- `frontend/src/app/try/page.tsx` — the doc's 3-step flow:
  1. Turnstile widget + consent checkbox → upload (PDF/DOCX ≤10MB) →
     "Resume processed — {name}, {title}" confirmation from parse response;
  2. paste job description → detected title/company confirmation, inline edit;
  3. "Generate tailored resume and cover letter" → existing synchronous call +
     progress UI (reuse `GenerationForm` internals where practical).
- Result view: rendered preview, resume PDF + cover-letter PDF downloads,
  locked rows for DOCX/TXT/View-Changes with "Create free account" CTA
  (primary: Download resume; secondary: Create free account and save).
- Guest state carried by the same token storage; `/login`/`/register` pages
  untouched. Root `/`: anonymous visitors redirect to `/try` (lightweight
  landing page later).
- Funnel events fired from the backend on each step transition.

## Phase 4 — Claim/merge after registration

- `POST /api/v1/guest/claim` (authenticated as the NEW registered user):
  body `{guest_access_token}` — possession of the guest JWT is the ownership
  proof. Backend verifies the guest token via the same Supabase verification
  path, loads the guest user, then in ONE transaction:
  reassign `structured_resumes`, `job_descriptions`, `generation_runs`,
  `documents` (and the unreviewed `candidate_profiles` row only if the
  registered user has none) to the new user id; add
  `guest_entitlements.used` to the new user's `free_generations_used`
  (3 lifetime − 1 = 2 left); mark guest user `guest_claimed_by`, deactivate
  it; funnel event `guest_claimed`.
- R2 object keys stay under the old `documents/{guest_id}/…` namespace —
  authorization is row-based, so no object copying; the cleanup job skips
  claimed guests.
- Frontend: after successful registration, if a guest token is present in
  storage, call `/guest/claim`, then drop the guest token and route into the
  normal dashboard (history now shows the guest run).

## Phase 5 — Retention & metrics

- Cleanup job: APScheduler started in the FastAPI lifespan (acceptable for
  the current single-instance deployment; if the backend later scales out,
  move to an external scheduler without touching the rest of the guest
  architecture). Every run first **releases stale reservations**
  (`reserved > 0 AND reserved_at < now − 30 min` → `reserved = 0`), then
  daily — for unclaimed anonymous users older than
  `guest_retention_days`: delete R2 objects, resumes, JDs, runs, documents,
  profile; keep the `users` row as a tombstone (id, timestamps, legal fields)
  plus `guest_abuse_events`/`funnel_events`; stamp `guest_purged_at`.
  Also: "Delete my files now" endpoint + button on the guest result page.
- Admin metrics (v1): funnel counts by day (sessions, uploads, generations
  started/completed/failed, downloads, claims), guest spend proxy
  (generation count × configured unit cost), repeated device/IP counters —
  one aggregate endpoint + a simple table on the existing admin page,
  alongside the kill switch and cap controls.

## Testing & rollout

- Per-phase pytest (FastAPI TestClient): entitlement reservation concurrency
  (two parallel generates → one 429), device/IP limits, guest gate branches,
  claim/merge transactionality (rows reassigned atomically, credits summed),
  PDF-only document filtering, cleanup job idempotency.
- Frontend: /try happy path + rejection states (limits, kill switch).
- Rollout: ship with `guest_enabled=false`; enable on staging; verify
  Turnstile + anon sign-in against the staging Supabase project; then
  production with conservative caps (daily 25 / concurrent 2), watch the
  admin funnel + abuse tables for a week before raising limits.

## Deliberately deferred

- View Changes for guests, DOCX/TXT guest downloads (registration incentive).
- URL job fetch for guests.
- Real landing page at `/` (marketing content).
- Background/queued generation (public sync endpoint is capped instead).
- External analytics (PostHog etc.) — funnel table covers v1.

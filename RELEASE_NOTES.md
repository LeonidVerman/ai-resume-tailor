# Release Notes

## 0.8.6.BETA — 2026-05-11

### Bug fixes
- **#56 Fix rendering issues**: LLM-driven classification now drives experience section
  rendering for date-first (Pattern B) resume layouts. Extra bullets and skill lines beyond
  template slots reflow as overflow paragraphs rather than being truncated or packed with
  semicolons. Technical Skills and Professional Summary are no longer truncated for linear
  (non-sidebar) templates. Content injection grader updated to recall-based metric for
  accurate coverage reporting.

---

## 0.8.5.BETA — 2026-05-09

### Bug fixes
- **#32 Post processing screwed formatting**: fixed visual fragmentation and missing
  summary content in rendered resume templates. Name and header text no longer renders
  vertically letter-by-letter in narrow columns. Templates that use non-canonical section
  labels (General Info, Professional Overview) now correctly map to the Professional
  Summary region. Summary text is injected into the correct column slot instead of
  landing between name components.

---

## 0.8.4.BETA — 2026-04-30

### Bug fixes
- **#48 "Invalid token" after inactivity on Admin page**: token refresh on session expiry now
  covers all binary download and upload endpoints (Admin downloads, document downloads,
  classification upload), not just JSON API calls.
- **#44 Improve layout of download documents table**: Resume and Cover Letter columns are now
  consistently separated — left column Resume (PDF, DOCX, TXT), right column Cover Letter
  (PDF, DOCX, TXT) — on both the Generate and History pages.
- **#42 Aggressive cover letter does not include contacts section**: cover letter heading
  (name, contact line, date) is now assembled deterministically from the candidate profile
  for all generation modes. Candidate profile extended with email, phone, and LinkedIn URL
  fields; email is prefilled from login; generation is blocked until email and phone are set.

---

## 0.8.3.BETA — 2026-04-29

### New features
- **Resume classification integrated into Internal Representation processing and rendering**

---

## 0.8.2.BETA — 2026-04-24

### New features
- **AI resume template classification**: uploaded resumes are now classified by an LLM pipeline
  (with deterministic validation, repair, and safe downgrade). Classification result is stored
  per resume but not yet used in actual processing.

---

## 0.8.1.BETA — 2026-04-15

### New features
- **Fill candidate profile from example profile**: users can now one-click populate all
  candidate profile sections from a built-in example profile to quickly explore the system.
- **Fill candidate profile from resume**: users can select an uploaded resume and generate
  a complete candidate profile draft via a single LLM call. The draft is cached per resume
  with SHA-256 staleness detection, accessible from both the profile editor and the onboarding
  wizard. First-time users with no resumes can upload directly from the picker modal.

### Bug fixes
- **#30 TXT generation**: plain-text download format is now available for all generated documents.
- **#28 Cover letter wrong name**: Leonid's personal header was leaking into generated cover letters
  for web users. The cover letter template is now stripped of the author's contact block and
  replaced with the candidate's name before being passed to the LLM.
- **#27 Job description viewer**: inline job description preview available from the generate page.
- **#31 Scraping failure**: fixed edge cases causing job description URL scraping to fail.
- **#34 Wrong person and contacts on resume / wrong resume template**: fixed a three-part bug
  chain — cover letter template contamination, an IR zero-section crash in `updater.py`, and the
  download fallback silently using the wrong resume template. Fallback renders now look up the
  user's original resume via `structured_resume_id` stored in the generation record.
- **#35 Wrong free credits counter**: the initial free credits counter was decrementing the total
  instead of incrementing used. For users with `monthly_limit_override=0` (credit-pack-only),
  `increment_atomic` now short-circuits so generations always consume from `extra_credits`. The
  generate form shows "N credits remaining" rather than a misleading used/total ratio.
- **#33 Wrong title of parsed resume**: resume title showed a phone number or section header
  instead of the candidate's name. Parser now strips email/phone/URL tokens from each line before
  extracting the leading run of title-cased name tokens.
- **#29 Error parsing resume**: fixed a crash when parsing PDFs whose IR contains zero detected
  sections (`updater.py` guard added).

---

## 0.8.0.BETA — 2026-04-14

### New features
- **Fill candidate profile from example profile**: users can now one-click populate all
  candidate profile sections from a built-in example profile to quickly explore the system.
- **Fill candidate profile from resume**: users can select an uploaded resume and generate
  a complete candidate profile draft via a single LLM call. The draft is cached per resume
  with SHA-256 staleness detection, accessible from both the profile editor and the onboarding
  wizard. First-time users with no resumes can upload directly from the picker modal.

---

## 0.7.1.BETA — 2026-04-12

### Bug fixes
- **Initial free generation count admin setting** (`signup_credit_service.py`, `admin_config.py`,
  migration `l5m6n7o8p9q0`): replaced the `signup_credit_mode` string enum (`normal`/`beta`) with
  a plain configurable integer `initial_credits` (default 3) editable directly from the Admin
  panel. Users granted credits have `monthly_limit_override=0` so their total allowance is exactly
  the configured amount rather than credits plus the free monthly quota on top. Generate page now
  shows "0/N credits used" for credit-only users instead of the misleading "Monthly quota reached"
  message.

---

## 0.7.0.BETA — 2026-04-11

### New features
- **Help & Quick Start page** (`/help`): sidebar link and dedicated page with onboarding
  guidance and quick-start instructions for new users.

### Bug fixes
- **Stale token overlap in two-column templates** (`docx_renderer.py`): residual `<w:tab/>`
  elements were left in role-header paragraphs after text distribution, causing stale tokens
  to appear alongside the updated content in sidebar/two-column resume templates.
- **Experience section not rewritten for NBSP/tab-column templates** (`docx_parser.py`,
  `text_parser.py`, `docx_renderer.py`): role headers that use non-breaking spaces and tab
  stops for column alignment (instead of a `|` pipe separator) were not recognised as role
  headers, leaving the experience section unchanged from the template. Detection now handles
  the NBSP/tab-column format; `•` and `◉` bullet variants are also supported.
- **Signup credit mode not applied to new users** (`admin_config_repository.py`, migration
  `k4l5m6n7o8p9`): `AdminConfigRepository.get()` used `.first()` without `ORDER BY`, making
  row selection non-deterministic when duplicate rows existed. A TOCTOU race on an empty table
  could produce two rows; the admin's "beta" update would land on one row while new-user
  signups read the other (still "normal"), granting 3 credits instead of 10. Fixed by adding
  `order_by(AdminConfig.id)` and a migration that deduplicates extra rows and adds a
  `CHECK (id = 1)` constraint to prevent recurrence.

---

## 0.6.0.BETA — 2026-04-08
- Modernized UI

## 0.5.5.BETA — 2026-04-07
- Introduced Conservative, Normal and Aggressive generation modes
- Bugfixes

## 0.5.4.BETA — 2026-04-06
- Improved layout postprocessing
- Bugfixes

## 0.5.3.BETA — 2026-03-30

### New features

- **View Changes — resume diff popup** (`frontend`): history rows now show a "View Changes" button
  that opens a modal comparing the original and tailored resume. Diff is broken down by section
  (Professional Summary, Experience bullets, Technical Skills). Experience bullets show added,
  removed, and changed entries.
- **Inline word-level diff highlighting** (`ResumeDiffModal.tsx`): changed bullets render with
  red/green word-span highlighting using an LCS-based diff algorithm. Phrase-level clustering
  (`clusterIntoPhraseOps`) bridges small equal gaps so multi-word edits appear as single spans.
  Word-boundary insertion (`ensureWordBoundaries`) prevents words from running together.
- **Debug JSON persisted to object storage** (`StorageService`): after each generation run the
  debug JSON (Phase 1 plan + Phase 2 output) is uploaded to object storage and is downloadable
  from the admin page. `STORAGE_REGION` config var added for Supabase S3 compatibility.
- **Forgot / reset password flow** (`auth`): users can request a password-reset email and set a
  new password via a token link. New pages: `/forgot-password`, `/reset-password`.
- **Structured event logging** (`logging`): 10 key user events (registration, login, generation
  start/complete, download, etc.) emit structured `INFO` log lines for ops monitoring.
- **4 new ATS scrapers + Indeed resilience** (`scraper`): added Greenhouse, Lever, Workday, and
  Ashby scrapers. Indeed scraper gains multi-strategy extraction (DOM → RSS feed fallback),
  ScraperAPI cloud proxy support, stronger session priming, and updated Chrome fingerprint.
- **Legal consent system** (`legal`): users must accept a user agreement and privacy notice on
  first login. Acceptance is persisted; expired sessions redirect back to the acceptance page.

### Bug fixes

- **DOCX compiler — singular `SKILL` heading** (`docx_parser.py`, `text_parser.py`, `diff.py`):
  resumes using `SKILL` (singular) as the skills section heading were not recognised, causing the
  section to be kept verbatim instead of updated. Added `"skill"` to `_SKILLS_NAMES` in both
  parsers and to `_ALL_SECTION_HEADERS` / `_SECTION_ALIASES` in `diff.py`.
- **DOCX compiler — stale body_items for table-based DOCX** (`updater.py`): when the LLM added
  a section absent from a table-based template (extras path), the original `body_items` (stale
  table XML) was incorrectly returned, causing the renderer to output the unchanged original.
  Fixed by aligning the `body_items` return condition with the in-place update condition
  (`has_table_blocks and not match.extras`).
- **Diff modal crash** (`ResumeDiffModal.tsx`): `TypeError: Cannot read properties of undefined
  (reading 'match')` when `change.before` or `change.after` was `undefined`. Fixed by making
  `computeInlineDiff` accept `string | undefined` and adding early-return guards. `before`/`after`
  on `ResumeDiffBulletChange` are now optional in `api.ts`.
- **Diff modal spacing artifacts**: adjacent remove/add spans rendered without whitespace between
  them (e.g. `withSenior 15+backend`). Fixed by `ensureWordBoundaries` post-processing pass.
- **Indeed scraper**: fixed 401 errors, added `Referer`/`Sec-Fetch-Site` headers, improved
  session cookie priming, added ScraperAPI proxy support for cloud deployments.
- **Deploy**: `PORT` env var now respected in backend Dockerfile; `legal/` directory copied into
  image; migrations run automatically on container start.
- **Frontend**: `useSearchParams` wrapped in `Suspense` on login page; legal session redirect
  on expired acceptance; missing role diff arrays handled gracefully.

### Tests

- 14 new regression tests for non-canonical heading recognition (SKILL, EMPLOYMENT HISTORY,
  CERTIFICATIONS AND TRAINING, letter-spaced headings, etc.) in `test_structural_editor.py`.
- 16 new tests (Tests A–D) covering table-based DOCX scenarios: extras path, no-extras path,
  extras + semantic matches, and diff-vs-rendered sanity.
- **373 tests passing** (up from 357 at 0.5.2).

---

## 0.5.2.BETA — 2026-03-25

Second deployed version. Bigint PK migration complete; UUID rollback columns dropped.

---

## 0.5.1.BETA — 2026-03-24

First deployed version.

---

## 0.5.0.BETA — 2026-03-10

Initial SaaS version.

---

## 0.1.8.ALPHA — 2026-03-07

### Fixed
- **Cover letter hyperlink concatenation** (`docx/template_fill.py`): `replace_paragraph_text`
  now clears `<w:t>` text inside `<w:hyperlink>` elements in addition to direct runs.
  Previously, the hyperlinked email address in the cover letter name paragraph was not cleared
  and was appended to the candidate name (e.g. `Leonid Vermanleonidverman@gmail.com`).
  Resume generation was already protected by the skip-pre-H2 logic; this fix covers the
  cover letter fallback path where all paragraphs are processed by `_apply_groups`.

### Added
- **`doc/GENERATION_ENTRYPOINTS.md`**: reference document covering both CLI entrypoints
  (`tailor` generate and `tailor assess`), required files, all environment variables with
  defaults, pipeline flowcharts, expected output paths, and key source modules.
- **`tests/test_smoke.py`** (32 tests): no-LLM smoke tests verifying config attributes,
  all prompt files are present and loadable, both templates are readable, resume and cover
  letter template fill produce valid `.docx` output, cover letter hyperlink regression,
  `normalize_cover_letter` correctness, and `validate_plan` acceptance/rejection.

### Tests
- 469 tests passing (up from 437 at 0.1.7).

---

## 0.1.7.ALPHA — 2026-03-05

### Added
- **Single-pass model config** (`--simple` mode): introduced `SIMPLE_MODEL` and
  `SIMPLE_TEMPERATURE` env vars so the single-pass path has its own model config
  (was sharing Phase 2 settings). Defaults: `SIMPLE_MODEL=gpt-5.2`, `SIMPLE_TEMPERATURE=0.3`.
- **`prompts/tailor.txt`** — GENERAL_LAYER v1.4 (single-pass prompt).

### Benchmark
- Integral score 7.48 (previous best: 6.54 single-pass, 6.19 two-phase).

### Tests
- 437 tests passing (up from 436 at 0.1.6).

---

## 0.1.6.ALPHA — 2026-03-05

**Skill graph, skill/tool allowlist enforcement, schema fix, and `--simple` mode.**

### Added
- **`skill_graph` in Phase 1 output** (`schemas/phase1_output.json`, `llm.py`, `writer_packet.py`):
  Phase 1 planner now produces a `skill_graph` object with `direct_skills` (candidate's verified
  skills from the master resume) and `related_skills` (adjacent skills that may be claimed from
  experience context). `validate_plan()` enforces presence and structural correctness of both lists.
  `WriterPacket` derives `skill_allowlist_skills_section` and `skill_allowlist_experience_claims`
  directly from the graph.
- **Check 15 — Skill section allowlist** (`phase2_validator.py`): detects tokens in the resume
  Skills section that are not in `skill_allowlist_skills_section`; emits
  `SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION` per offending token.
- **Check 16 — Experience tool claim allowlist** (`phase2_validator.py`): scans experience bullets
  for high-risk tool mentions not present in `skill_allowlist_experience_claims`; emits
  `SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION`. `_HIGH_RISK_TOOL_LEXICON` constant introduced.
- **`--simple` / `-s` CLI flag** (`cli.py`): forces single-pass generation (skips Phase 1 planning)
  regardless of the `ENABLE_TWO_PHASE` environment variable. Mutually exclusive with `--calibrate`
  and `--calibrate-data`. Useful for rapid iteration and baseline scoring.

### Fixed
- **`min_theme_occurrences` schema** (`schemas/phase1_output.json`): changed from a free-form
  `additionalProperties` object to a typed array of `{theme_id: string, min_count: integer}`
  objects, enabling strict JSON-schema validation of narrative plan theme entries.

### Changed
- Phase 1, Phase 2, and repair prompts updated to reflect `skill_graph` structure and
  `min_theme_occurrences` array format.
- `validate_plan()` and `plan_repair_tailoring()` updated to pass `skill_graph` validation.

### Tests
- 437 tests passing (up from 433 at 0.1.5).

---

## 0.1.5.ALPHA — 2025-03-03

### Added
- **Narrative binding validators (Checks 12/13/14)**: deterministic enforcement of the
  `narrative_plan` produced by Phase 1 planner.
  - `SummaryThemeValidator` (Check 12): resume summary must contain at least one
    signature-term hit for each `must_cover_theme_id`.
  - `AnchorRoleDominanceValidator` (Check 13): the anchor role's first-K bullets must
    satisfy per-theme `min_theme_occurrences` for the top-K themes.
  - `DomainTranslationAnchorValidator` (Check 14): when `domain_mismatch=true`, validates
    `min_total_rule_instantiations`, `min_instantiations_in_anchor_role`, and
    `require_target_frame_in_anchor_role_first_k` against the `domain_translation_ledger`.
- **`narrative_plan` wired through pipeline**: Phase 1 output field passed verbatim into
  `WriterPacket` and validated by `validate_plan()` (anchor_role_id cross-checked against
  `resume_strategy.experience`).
- **`domain_translation_ledger` parameter** added to `validate_phase2_output()` so Check 14
  can inspect per-rule instantiation records from the Phase 2 writer.
- **Employer integrity validator (Check 11)**: prevents Phase 2 from inventing new employers
  or mutating company names; enforces date-range integrity against the master resume.
- **`master_role_dates`** added to `WriterPacket` (parsed from master resume Experience section).
- **Calibrate-data mode** (`--calibrate-data DIR`): assess pre-generated resume/cover-letter
  samples matched to positions by company name, without running the full tailoring pipeline.
  Run scripts: `tests/run_calibrate_samples.sh` / `tests/run_calibrate_samples.cmd`.
- **Domain translation mechanism**: Phase 1 plans `domain_translation_rule_ids`; Phase 2
  enforces forbidden phrases (`DOMAIN_FORBIDDEN_PHRASES`) and instantiates rules via the
  `domain_translation_ledger`.

### Changed
- `validate_plan()` now requires `narrative_plan` key (added to `_PLAN_REQUIRED_KEYS`).
- `repair_brief.global_issues` extended with `narrative_missing_summary_themes`,
  `narrative_anchor_missing_theme_ids`, `employer_integrity_violations`,
  `date_integrity_violations`, `domain_forbidden_phrases`.
- Phase 2 repair loop passes `domain_translation_ledger` through all validation iterations.

### Tests
- 433 tests passing (up from 364 at 0.1.4).

---

## 0.1.4.ALPHA — 2025-03-01

**Role level expansion, role preservation, mechanism placement, manager density, assess debug output.**

### New features

- **Expanded `role_level` enum** (`llm.py`, `schemas/phase1_output.json`): four levels → seven. New values: `manager` (people + delivery accountability), `staff` (high-leverage IC, cross-team technical leadership), `principal` (org-wide technical strategy and standards). Coercion updated: `"Staff Engineer"` → `staff` (previously fell to `senior`); `"Engineering Manager"` → `manager`; `"Principal …"` → `principal`; `"Head of …"` → `director`.
- **Role weight profiles** extended (`writer_packet.py`): added `_WEIGHT_PROFILES` entries for `manager` (arch 0.3 / strategic 0.4 / operational 0.3), `principal` (0.5 / 0.4 / 0.1), and `staff` (0.65 / 0.25 / 0.1).
- **`role.txt` loader passes `ROLE_LEVEL`** (`llm.py`): `_build_phase2_developer_instructions` now accepts `role_level` and forwards it to `_load_prompt_optional("role", ROLE_LEVEL=…)`. `role.txt` already documents all seven levels.
- **Role preservation — `MISSING_ROLE` validator check** (`phase2_validator.py`): `WriterPacket` gains `master_resume_role_names` (all role headers from master resume). Validator check #8 emits `MISSING_ROLE: <role>` error if any master role is absent from the output — either as a full role header or within an `Earlier roles` collapsed block. `repair_brief.global_issues` gains `missing_roles` field. New helpers: `_extract_earlier_roles_block()`, `_role_found_in_output()`. Repair prompt step 6 (already in place) handles re-insertion.
- **Mechanism placement warning** (`phase2_validator.py`): for high-priority roles with `mech_required > 0`, warns when no mechanism appears in the first two bullets — nudges writer/repair to front-load mechanism content.
- **Manager-level leadership/delivery density** (`phase2_validator.py`): when `role_level == "manager"`, warns if fewer than 2 leadership-verb bullets (`Led`, `Managed`, `Mentored`, `Guided`, `Coordinated`, `Partnered`, `Improved`) appear in high-priority roles; also warns if the JD is delivery-oriented but the resume contains no delivery vocabulary (`backlog`, `roadmap`, `timeline`, `risk`).
- **Assess debug output** (`assess.py`): `_process_one_position` now calls `save_debug_data` after tailoring, writing `tmp/<Company>-<Role>-<timestamp>.json` (phase 1 + phase 2 debug) — same format as the regular `tailor` command. No docx/pdf generated.
- **Assessment run scripts** (`tests/run_assess.sh`, `tests/run_assess.cmd`): convenience wrappers with defaults (model: gpt-5.2, temperature: 0.5, positions: `tests/data/positions.txt`); all CLI flags pass through.

### Tests

- 50 new deterministic tests (total: 315; was 265 at 0.1.3).
- New test classes: `TestRoleTxtLoading`, `TestRolePresenceValidation`, `TestMechanismPlacementCheck`, `TestManagerDensityChecks`; expanded `TestWriterPacketWeightProfile` (manager/staff/principal profiles); expanded `TestValidatePlan` (new enum values + coercion).

---

## 0.1.3.ALPHA — 2025-02-28

**Postprocessors removed, evidence ledger, LLM judge, and repair loop simplification.**

### New features
- **Evidence ledger validation** (`phase2_validator.py`): Phase 2 writer now emits a structured `evidence_ledger` alongside the resume/cover letter. Validator cross-checks each ledger entry against actual output spans — rewording is allowed, but fabrication is flagged. Exports `LEDGER_MISMATCH_ERROR_PREFIX`, `LEDGER_SPAN_NOT_FOUND_PREFIX`, `LEDGER_ENTRY_MISSING_PREFIX`.
- **LLM judge round** (`prompts/judge.txt`): after repair, any remaining `LEDGER_SPAN_MISMATCH` errors are submitted to a judge model (default `gpt-4o-mini`) for semantic verdict. `apply_judge_to_validation(report, verdicts)` removes approved mismatches from the error list. Controlled by `ENABLE_PHASE2_JUDGE` (default true) and `PHASE2_JUDGE_MODEL`.
- **`repair_brief`** in `ValidationReport`: structured dict with `global_issues` (missing metrics, skills, unsafe nouns, cover letter date) and per-role `action_plan` passed to repair prompt.
- **`judge_candidates`** in `ValidationReport`: list of `{id, kind, target, location, exact_span}` items eligible for judge evaluation.
- **Repair loop simplified**: `PHASE2_MAX_REPAIR_ATTEMPTS` reduced from 3 to 1 — pipeline is now write → 1 repair → 1 judge, eliminating unnecessary LLM calls.

### Removed
- **Mechanism postprocessor** (`mechanism_postprocessor.py`) deleted — enforcement now handled entirely by writer prompt + repair loop + judge.
- **Token postprocessor** (`token_postprocessor.py`) deleted.

### Tests
- 2 new test files: `tests/test_ledger_validation.py` (54 tests), `tests/test_phase2_repair_loop.py` (25 tests).
- Total: 265 tests (was 263 at 0.1.2).

---

## 0.1.2.ALPHA — 2025-02-27

**Mechanism taxonomy, enforcement hardening, and WriterPacket provenance split.**

### New features
- **Mechanism taxonomy split** (`mechanism_taxonomy.py`): the flat `must_surface_mechanisms` pool is replaced by three typed buckets — `arch` (implementation-level "how" phrases, strictly enforced), `strategic` (leadership/vision signals, soft check for director roles), and `operational` (process/delivery signals, soft check for director roles). New module exports `classify_phrase()`, `build_mechanism_taxonomy()`, and `_get_canonical_phrase()` with a variant-normalisation map.
- **WriterPacket provenance split**: `arch_mechanisms_primary` (candidate profile phrases) and `arch_mechanisms_backstop` (safe-translation phrases) emitted separately; postprocessor iterates primary pool first. `must_surface_mechanisms` retained as backward-compat alias pointing to the arch list.
- **Role weight profiles** in WriterPacket (`role_weight_profile`): director roles get `{arch: 0.4, strategic: 0.4, operational: 0.2}`, senior roles get `{arch: 0.7, strategic: 0.2, operational: 0.1}`. Phase 2 prompt (v1.9) instructs the writer to bias bullet selection according to these weights after hard minimums are satisfied.
- **Via-clause guard** in mechanism postprocessor: injection target selection uses four priority passes; bullets already carrying a `via`/`using`/`through` clause are deprioritised to prevent awkward stacking.
- **Director soft signal checks** in validator: warns when fewer than 2 strategic or fewer than 1 operational signal is present in the output.

### Bug fixes
- **Mechanism postprocessor injection artifacts**: (1) trailing punctuation (`.`, `,`, `;`, `:`) stripped from bullet text before appending the via-clause; (2) `_normalize_via_phrase()` lowercases the first character of injected phrases unless it is an ALL-CAPS acronym or CamelCase proper noun.
- **Effective priority in `role_density_shortfall_allowance`**: allowance was computed from raw plan priority, under-estimating it for roles the validator promotes via top-K non-thin repositioning. `_build_role_density_shortfall_allowance()` now mirrors the validator's two-step logic (thin classification → top-K promotion → effective priority).
- **Mechanism counting consistency**: removed bare tool names (`aws`, `docker`, `kubernetes`, `rest api`, etc.) from the legacy `_MECHANISM_KEYWORDS` fallback set; fixed two counting passes in the postprocessor that used the non-arch-aware checker, causing deficit detection to differ from the validator.
- **Taxonomy quality gates**: (1) experience-statement gate in `classify_phrase()` rejects phrases with experience/expertise language but no mechanism-shaped noun before the ARCH check; (2) `_substring_dedup_arch()` drops shorter phrases that are proper substrings of longer ones, keeping only the most specific form.
- **`role_density_shortfall_allowance` in validator**: `phase2_validator` now reads the per-role allowance from `writer_packet` via `_find_role_allowance()`, applying `effective_min_bullets = max(1, min_bullets − allowance)` per role.
- **Taxonomy/weights prompt fix**: corrected weight-profile reference in `phase2.txt` and `phase2_repair.txt`.
- **Candidate profile extended** with additional technology signals.

### Tests
- 106 new tests (total: 263; was 157 at 0.1.1).
- New test file: `tests/test_mechanism_taxonomy.py` (47 tests); new file: `tests/test_phase2_validator_role_bullets.py`.

---

## 0.1.1.ALPHA — 2025-02-26

**Improved generation**

### New features
- **Density shortfall allowance** (`role_density_shortfall_allowance` in WriterPacket): when the master resume has fewer bullets than the density minimum for a high/medium priority role, the Phase 2 writer is now permitted to add up to 1 derived-but-grounded bullet per role (suppressed for thin roles and roles without plan evidence).
- **Updated Phase 2 prompts**: writer (v1.7) and repair (v3.1) prompts now include explicit guidance for the density shortfall allowance and a density preflight check. The repair prompt gains `TAILORING_PLAN` as an explicit input and a clearer support rule referencing `evidence_map`.

### Bug fixes
- **Abbreviated role-name matching** (`_roles_match`): Phase 1 sometimes abbreviates compound job titles (e.g. `"VP / Director of Software Development | Corp"` instead of `"VP / Director of Software Development / Lead Software Developer | Corp"`), causing `role_source_bullet_counts` and `role_source_char_counts` to return 0 for that role. A new pipe-part fallback matcher resolves this across all five matching callsites in `writer_packet.py` and `phase2_validator.py`.

### Tests
- 15 new deterministic tests (total: 157).

---

## 0.1.0.ALPHA — 2025-02-25

**Initial version.**

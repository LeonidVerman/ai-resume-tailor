# Release Notes

## 0.1.4.APLHA — 2025-03-01

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

## 0.1.3.APLHA — 2025-02-28

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

## 0.1.2.APLHA — 2025-02-27

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

## 0.1.1.APLHA — 2025-02-26

**Improved generation**

### New features
- **Density shortfall allowance** (`role_density_shortfall_allowance` in WriterPacket): when the master resume has fewer bullets than the density minimum for a high/medium priority role, the Phase 2 writer is now permitted to add up to 1 derived-but-grounded bullet per role (suppressed for thin roles and roles without plan evidence).
- **Updated Phase 2 prompts**: writer (v1.7) and repair (v3.1) prompts now include explicit guidance for the density shortfall allowance and a density preflight check. The repair prompt gains `TAILORING_PLAN` as an explicit input and a clearer support rule referencing `evidence_map`.

### Bug fixes
- **Abbreviated role-name matching** (`_roles_match`): Phase 1 sometimes abbreviates compound job titles (e.g. `"VP / Director of Software Development | Corp"` instead of `"VP / Director of Software Development / Lead Software Developer | Corp"`), causing `role_source_bullet_counts` and `role_source_char_counts` to return 0 for that role. A new pipe-part fallback matcher resolves this across all five matching callsites in `writer_packet.py` and `phase2_validator.py`.

### Tests
- 15 new deterministic tests (total: 157).

---

## 0.1.0.APLHA — 2025-02-25

**Initial version.**

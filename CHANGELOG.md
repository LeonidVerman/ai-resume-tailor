# Changelog

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

## 0.1.4 — 2025-02-xx

Initial two-phase pipeline with WriterPacket, Phase 2 validator (Checks 1–10),
evidence ledger, LLM-as-judge post-repair round, and domain translation rules.

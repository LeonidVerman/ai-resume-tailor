# Phase 7 Prompt Assets Notes

**Date:** 2026-03-07
**Tasks covered:** Phase 7, Tasks 51–53 from `IMPLEMENTATION_TASK_PLAN.md`

---

## Summary

This phase externalizes prompt content as versioned, inspectable files under
`backend/app/prompts/`. The existing generator prompt files (`prompts/*.txt`)
were **not moved or modified**. All existing CLI behavior is preserved.

---

## Files added

### Task 51 — Resume tailoring prompt assets

| File | Source |
|---|---|
| `backend/app/prompts/resume_tailor/system.md` | Verbatim copy of `prompts/phase1.txt` (PLANNER_LAYER v5.2) |
| `backend/app/prompts/resume_tailor/output_schema.json` | Copy of `schemas/phase1_output.json` (already in repo) |
| `backend/app/prompts/resume_tailor/version.txt` | `planner_v5.2` |

### Task 52 — Cover letter prompt assets

| File | Source |
|---|---|
| `backend/app/prompts/cover_letter/system.md` | Extracted from `prompts/phase2.txt` — cover-letter-specific sections only |
| `backend/app/prompts/cover_letter/output_schema.json` | New JSON schema for the cover_letter field of Phase 2 output |
| `backend/app/prompts/cover_letter/version.txt` | `writer_v9.2` |

### Task 53 — Evaluation prompt assets

| File | Source |
|---|---|
| `backend/app/prompts/evaluation/system.md` | Verbatim copy of `prompts/assess.txt` (assessment rubric) |
| `backend/app/prompts/evaluation/output_schema.json` | New JSON schema derived from the `assess_v2` output format in `assess.txt` |
| `backend/app/prompts/evaluation/version.txt` | `assess_v2` |

---

## Compatibility strategy used

**Approach B** (from spec): stable prompt asset files created, current generator
behavior preserved. The existing generator continues to load from `prompts/*.txt`
unchanged.

The two-phase pipeline has 4 active prompt files:
- `prompts/phase1.txt` — Phase 1 planner (resume tailoring plan)
- `prompts/phase2.txt` — Phase 2 writer (resume + cover letter text)
- `prompts/phase1_repair.txt` — Phase 1 repair on validation failure
- `prompts/phase2_repair.txt` — Phase 2 repair on validation failure

Additional supporting prompt files (also unchanged):
- `prompts/tailor.txt` — single-pass fallback (GENERAL_LAYER v1.4)
- `prompts/versions/tailor.txt` — older single-pass version
- `prompts/role.txt` — role-level tuning instructions (ROLE_LEVEL_TUNING v2.0)
- `prompts/candidate.txt` — candidate-specific positioning anchors
- `prompts/task.txt` — dynamic task instruction with company/role substitution
- `prompts/judge.txt` — semantic ledger verification judge (JUDGE_LAYER v1.0)
- `prompts/assess.txt` — full evaluation/assessment prompt (assess_v2)
- `prompts/extract_metadata.txt` — lightweight metadata extraction

---

## What was externalized vs what remains dynamic

| Layer | Status |
|---|---|
| Phase 1 planner system prompt | Externalized in `resume_tailor/system.md` (verbatim) |
| Phase 1 output schema | Externalized in `resume_tailor/output_schema.json` (copied from `schemas/`) |
| Cover letter writer rules | Externalized in `cover_letter/system.md` (extracted subset of `phase2.txt`) |
| Cover letter output schema | New `cover_letter/output_schema.json` |
| Evaluation rubric | Externalized in `evaluation/system.md` (verbatim from `assess.txt`) |
| Evaluation output schema | New `evaluation/output_schema.json` |
| Dynamic prompt assembly (Phase 1/2 user messages: JD, resume, profile, domain rules) | **Remains dynamic in code** — assembled by `tailor.core_generation.llm` |
| Role-level tuning prompt (`role.txt`) | Not externalized to backend — loaded dynamically by generator |
| Candidate-specific anchors (`candidate.txt`) | Not externalized — user-specific, not a stable system asset |
| Repair prompts (`phase1_repair.txt`, `phase2_repair.txt`) | Not externalized — consumed directly by the generator repair loop |
| Single-pass fallback (`versions/tailor.txt`) | Not externalized — fallback path only |

---

## PromptAssemblyService additions

Three new methods added to `backend/app/services/prompt_assembly_service.py`:

```python
load_backend_prompt_asset(category, filename) -> str
    # Read backend/app/prompts/<category>/<filename>

load_backend_output_schema(category) -> dict
    # Load and parse output_schema.json for a category

get_prompt_version(category) -> str
    # Return the version string from version.txt
```

Existing methods (`load_prompt`, `load_prompt_optional`, `load_candidate_profile`,
`load_resume_template_text`, `load_cover_template_text`) are unchanged.

---

## Versioning

| Category | Version | Source |
|---|---|---|
| `resume_tailor` | `planner_v5.2` | PLANNER_LAYER version tag in `phase1.txt` |
| `cover_letter` | `writer_v9.2` | PHASE2_WRITER_LAYER version tag in `phase2.txt` |
| `evaluation` | `assess_v2` | `version` field in `assess.txt` JSON output |

---

## Validation performed

```
python -c "json.loads(...)" → all 3 output_schema.json files are valid JSON
python -c "PromptAssemblyService().get_prompt_version(cat)" → all 3 versions load
python -m pytest tests/ -q → 469 passed
```

---

## Known limitations / deferred items

- **Phase 2 writer system.md not separately externalized**: The full `phase2.txt`
  (PHASE2_WRITER_LAYER) is not in `backend/app/prompts/resume_tailor/` because it
  is the *writer* layer that produces both resume and cover letter together. The
  `cover_letter/system.md` extracts the CL-specific subset. Full Phase 2 writer
  externalization is deferred to a future prompt-restructuring phase.

- **Repair prompts not externalized**: `phase1_repair.txt` and `phase2_repair.txt`
  are consumed by the generator's repair loop and are not yet exposed as backend
  assets. They follow the same versioning convention and can be added later.

- **Generator not yet consuming backend assets**: The generation service still
  calls the generator's `plan_tailoring()` / `tailor_documents_with_plan()` which
  load from `prompts/` directly. The backend prompt assets are reference copies.
  Full switchover is a future task.

- **candidate.txt not externalized**: This file is candidate-specific and will
  evolve into a per-user candidate profile injected dynamically from the DB
  (CandidateProfile → candidate_profile.json flow).

- **role.txt not externalized**: Role-level tuning is loaded dynamically by the
  generator based on `role_level` from the Phase 1 plan. Externalization would
  require a multi-file asset structure per role level.

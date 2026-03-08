# Prompt Strategy

> **Status:** Partial — existing CLI generator uses a mature two-phase prompt strategy.
> This document describes the current approach and the planned SaaS prompt architecture.

---

## Current CLI Generator Prompt Strategy

The CLI generator uses a **two-phase pipeline**:

### Phase 1 — Plan

- **Goal:** Analyse the job description and produce a structured tailoring plan.
- **Model:** Configurable via `PHASE1_MODEL` (default `gpt-4o`).
- **Output:** Structured JSON validated against `schemas/phase1_output.json`.
- **Prompt files:**
  - `prompts/phase1.txt` — system prompt
  - `prompts/phase1_repair.txt` — repair prompt (used on second attempt)
  - `prompts/role.txt` — role-level instruction block
  - `prompts/task.txt` — task context block
  - `prompts/candidate.txt` — candidate context block

### Phase 2 — Write

- **Goal:** Generate the tailored resume and cover letter using the plan.
- **Model:** Configurable via `PHASE2_MODEL` (default `gpt-4o`).
- **Output:** Full resume and cover letter text.
- **Prompt files:**
  - `prompts/phase2.txt` — system prompt
  - `prompts/phase2_repair.txt` — repair prompt

### Single-pass mode (`--simple`)

- Combines plan + write into one LLM call.
- **Prompt files:**
  - `prompts/tailor.txt`

### Validation

- Phase 1 output is validated against JSON Schema (`schemas/phase1_output.json`).
- Phase 2 output is validated by `phase2_validator.py` (deterministic checks).
- A judge model (`PHASE2_JUDGE_MODEL`, default `gpt-4o-mini`) evaluates ledger entries.

---

## Planned SaaS Prompt Architecture

Prompts will be treated as **versioned files**, not inline strings.

```
backend/app/prompts/
  resume_tailor/
    system.md        → Phase 2 (write) system prompt
    output_schema.json
    version.txt
  cover_letter/
    system.md
    output_schema.json
    version.txt
  evaluation/
    system.md
    output_schema.json
    version.txt
```

### Versioning convention

- Each prompt directory contains a `version.txt` (e.g., `v2.1`).
- Generation runs record `prompt_version` in the `generation_runs` table.
- This enables tracing output quality to specific prompt versions.

### Prompt assembly service

`backend/app/services/prompt_assembly_service.py` will:
1. Load prompt files from disk
2. Load the JSON schema for structured output
3. Assemble system prompt + candidate profile + resume + JD + optional user prompt
4. Return the assembled payload and prompt version string

---

_Detailed prompt templates will be added in Phase 7 of the task plan._

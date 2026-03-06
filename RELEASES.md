# Release Notes

## 0.1.7.ALPHA — 2026-03-05

### Changes
- **Single-pass model config** (`--simple` mode): introduced `SIMPLE_MODEL` and `SIMPLE_TEMPERATURE`
  env vars so the single-pass path has its own model config (was sharing PHASE2 settings).
  Defaults: `SIMPLE_MODEL=gpt-5.2`, `SIMPLE_TEMPERATURE=0.3`.
- **Prompt version**: added `prompts/versions/tailor.txt` — GENERAL_LAYER v1.4 (single-pass).
- **Benchmark**: integral score 7.48 (previous best: 6.54 single-pass, 6.19 two-phase).

---

## 0.1.6.ALPHA — 2026-03-04

### Changes
- Skill graph (initial version).
- Skill/tool allowlist checks in Phase 2 validator.
- Schema fix for Phase 1 output.
- `--simple` flag for single-pass mode.
- Benchmark: single-pass 6.54, two-phase 6.19.

---

## 0.1.5.ALPHA and earlier

See git log.

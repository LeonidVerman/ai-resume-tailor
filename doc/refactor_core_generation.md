# Refactor: core_generation module

## Summary

Five generator-related modules were moved from `src/tailor/` into a new
`src/tailor/core_generation/` subpackage.  The originals were replaced with
thin backward-compatibility shims so that every existing import path, script,
and test continues to work unchanged.

---

## What was moved

| Original path | New path |
|---|---|
| `src/tailor/llm.py` | `src/tailor/core_generation/llm.py` |
| `src/tailor/plan_validator.py` | `src/tailor/core_generation/plan_validator.py` |
| `src/tailor/phase2_validator.py` | `src/tailor/core_generation/phase2_validator.py` |
| `src/tailor/writer_packet.py` | `src/tailor/core_generation/writer_packet.py` |
| `src/tailor/mechanism_taxonomy.py` | `src/tailor/core_generation/mechanism_taxonomy.py` |

### What was NOT moved

These modules remain at the `tailor/` level — they are shared infrastructure,
not exclusive to generation:

| Module | Reason |
|---|---|
| `config.py` | Loaded by everything (CLI, assess, generation) |
| `prompts.py` | Used by generation AND assess pipeline |
| `debug.py` | Shared utility |
| `diff.py` | CLI utility (compare master vs tailored resume) |
| `docx/` | Rendering layer (template fill, PDF export) |
| `job/` | Job data model (shared between generate and assess) |
| `cli.py` | CLI entrypoint |
| `assess.py` | Assessment pipeline |
| `__main__.py` | Module entrypoint |

---

## New module structure

```
src/tailor/core_generation/
    __init__.py          — public API (see below)
    llm.py               — LLM client, plan_tailoring, tailor_documents_with_plan,
                           tailor_documents, validate_plan, PlanParseError,
                           PlanValidationError, TailorResult
    plan_validator.py    — validate_plan_extended (Phase 1 deep checks)
    phase2_validator.py  — validate_phase2_output, apply_judge_to_validation,
                           error-code constants, helper functions
    writer_packet.py     — build_writer_packet (Phase 2 constraint builder)
    mechanism_taxonomy.py — classify_phrase, build_mechanism_taxonomy, PhraseCategory
```

### Public API (`tailor.core_generation`)

New SaaS integration code should import from `tailor.core_generation`:

```python
from tailor.core_generation import (
    plan_tailoring,
    tailor_documents_with_plan,
    tailor_documents,
    validate_plan,
    validate_plan_extended,
    build_writer_packet,
    validate_phase2_output,
    TailorResult,
    PlanParseError,
    PlanValidationError,
    PhraseCategory,
    classify_phrase,
    build_mechanism_taxonomy,
)
```

---

## Compatibility shims

Each moved module was replaced at its original path with a backward-compatible
shim.  Shim behaviour:

| Module | Shim type | Notes |
|---|---|---|
| `tailor/llm.py` | `sys.modules` alias | Replaced in `sys.modules` with the implementation module. Ensures `unittest.mock.patch('tailor.llm.X')` patches the correct object — critical for existing tests that mock LLM calls. |
| `tailor/plan_validator.py` | Explicit re-export | `from tailor.core_generation.plan_validator import validate_plan_extended` |
| `tailor/phase2_validator.py` | `import *` + explicit privates | Public names via `*`; private names used by tests and `writer_packet` imported explicitly. |
| `tailor/writer_packet.py` | Explicit re-export | `from tailor.core_generation.writer_packet import build_writer_packet` |
| `tailor/mechanism_taxonomy.py` | `import *` + explicit privates | Public names via `*`; private names used by tests imported explicitly. |

---

## Internal import updates

Within `core_generation/`, cross-references between moved modules use relative
imports.  References to unmoved modules (`config`, `prompts`, `job`) remain
absolute `tailor.*` imports.

Changes in `core_generation/writer_packet.py`:
```python
# Before (absolute)
from tailor.mechanism_taxonomy import build_mechanism_taxonomy, _normalize_for_dedup
from tailor.phase2_validator import normalize_role_header, ...
# After (relative)
from .mechanism_taxonomy import build_mechanism_taxonomy, _normalize_for_dedup
from .phase2_validator import normalize_role_header, ...
```

Changes in `core_generation/llm.py`:
```python
# Before (absolute)
from tailor.plan_validator import validate_plan_extended
from tailor.phase2_validator import LEDGER_MISMATCH_ERROR_PREFIX, ...
from tailor.writer_packet import build_writer_packet
# After (relative)
from .plan_validator import validate_plan_extended
from .phase2_validator import LEDGER_MISMATCH_ERROR_PREFIX, ...
from .writer_packet import build_writer_packet
```

---

## CLI behavior preserved

All commands continue to work identically:

```bash
python -m tailor --position-desc jobs/acme.txt
python -m tailor assess --positions tests/data/positions.txt
```

Scripts unchanged:

```
tests/run_assess.sh / run_assess.cmd
tests/run_calibrate.sh / run_calibrate.cmd
tests/run_calibrate_samples.sh / run_calibrate_samples.cmd
```

---

## Validation performed

```
python -m pytest tests/ -q          →  469 passed
python -m tailor --help             →  OK (all flags present)
python -m tailor assess --help      →  OK (all flags present)
from tailor.core_generation import … →  OK (public API clean import)
from tailor.llm import …             →  OK (backward compat)
from tailor.phase2_validator import … →  OK (backward compat, incl. privates)
from tailor.mechanism_taxonomy import … → OK (backward compat, incl. privates)
from tailor.writer_packet import …   →  OK (backward compat)
from tailor.plan_validator import … →  OK (backward compat)
```

---

## Risks / follow-ups

- **`assess.py` and `cli.py` still import from the shim paths** (`tailor.llm`,
  `tailor.plan_validator`, etc.). These are fully functional through the shims,
  but future work could update them to import from `tailor.core_generation`
  directly.
- **`diff.py`** was deliberately left at `tailor/` level (it is a rendering
  utility used only by `cli.py`). It can be moved to `core_generation/` in a
  future step if desired.
- **`prompts.py`** is shared between `core_generation/llm.py` and `assess.py`.
  It stays at `tailor/` level; if the assess pipeline is ever extracted into
  its own module, prompts should be revisited.

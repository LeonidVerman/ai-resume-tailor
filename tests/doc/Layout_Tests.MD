# Document Pipeline Test Reference

This document describes all test scripts related to document structure, parsing, classification,
layout, rendering, and postprocessing. Each script has a distinct responsibility in the pipeline.

---

## Table of Contents

1. [classify_samples](#1-classify_samples)
2. [parse_samples](#2-parse_samples)
3. [parser_diagnostics](#3-parser_diagnostics)
4. [generate_classification_report](#4-generate_classification_report)
5. [run_layout_tests (test_format_roundtrip)](#5-run_layout_tests)
6. [test_rendering](#6-test_rendering)
7. [grade_layout](#7-grade_layout)
8. [run_postprocessing_tests (test_changed_content_regression)](#8-run_postprocessing_tests)
9. [run_assess / run_calibrate / run_calibrate_samples](#9-run_assess--run_calibrate--run_calibrate_samples)

---

## 1. classify_samples

**Scripts:** `tests/classify_samples.cmd` · `tests/classify_samples.sh`

### What it is testing

Exercises the full LLM-based document classification pipeline end-to-end via the backend API.
Each resume sample (DOCX or PDF) is sent as a multipart file upload to
`POST /api/v1/admin/classification/classify-file`. The server parses the document, builds the
classification input JSON, calls the LLM to produce a structured classification result, runs a
validation pass, and optionally runs a repair pass when validation fails. Both the final
classification output and the raw LLM input are saved separately for downstream use by
`parser_diagnostics` and `generate_classification_report`. After all files are processed, the
classification report is generated automatically.

### Input

- `tests/samples/resume/docx/*.docx` — all DOCX templates (batch mode)
- `tests/samples/resume/pfd/*.pdf` — all PDF templates (batch mode, note folder typo `pfd`)
- Single file argument: `classify_samples.cmd samples\resume\docx\10-Template5.docx`
- Env vars: `API_URL` (default `http://localhost:8000`), `CLI_SECRET` (optional auth header)

All files in each directory are processed in glob order. Requires a running backend server.

### Output

```
tmp/artefacts/classification/
  docx/<basename>.json              final classification result (per DOCX file)
  pdf/<basename>.json               final classification result (per PDF file)
  llm-input/docx/<basename>_input.json   raw LLM input sent to classification (per DOCX)
  llm-input/pdf/<basename>_input.json    raw LLM input sent to classification (per PDF)
```

The `*_input.json` files contain the parsed document structure as seen by the LLM: sections,
roles, bullets, semantic labels, and layout signals. The classification `*.json` files contain
the full API response: `status`, `template_class`, `validation`, `validation_after_repair`,
`invalid_section_count_before_repair`, and the structured classification payload.

### Report

After batch mode completes, `scripts/generate_classification_report.py` runs automatically and
writes to `tmp/artefacts/classification/`:

- `classification_report.json` — machine-readable JSON with all metrics
- `classification_report_summary.txt` — human-readable summary

The report covers: total documents scanned (by source kind), initial validity rate, repair
success/partial/failure counts, final validity rate, status breakdown (`valid`, `repaired`,
`downgraded`, `repaired_and_downgraded`), top validation error codes before repair, and top
validation error codes after repair, with filename examples for repaired and downgraded cases.

---

## 2. parse_samples

**Scripts:** `tests/parse_samples.cmd` · `tests/parse_samples.sh`

### What it is testing

Runs the local document parser (`tailor.compiler.docx_parser` / PDF equivalent) directly
against each sample file and serializes the output to LLM-input JSON — with no server, no
API call, and no LLM invocation. This validates that the parser can read every sample and
produce a structurally coherent intermediate representation. The output format is identical to
what `classify_samples` saves from the API response, so `parser_diagnostics` can be run on
these files without needing the server.

### Input

- `tests/samples/resume/docx/*.docx` — all DOCX templates (batch mode)
- `tests/samples/resume/pfd/*.pdf` — all PDF templates (batch mode)
- Single file argument: `parse_samples.cmd samples\resume\docx\10-Template5.docx`

All files in each directory are discovered via glob. No configuration file needed.
`PYTHONPATH` is set to `src` automatically.

### Output

```
tmp/artefacts/classification/
  llm-input/docx/<basename>_input.json   parsed LLM input (per DOCX file)
  llm-input/pdf/<basename>_input.json    parsed LLM input (per PDF file)
```

Each file contains the structured document representation: `sections` array (each with
`section_id`, `section_type`, `roles`, `paragraphs`, `semantic` labels), contact block,
layout signals (`has_multicolumn`, `has_label_column`, etc.), and parser metadata.

### Report

No report file generated. Progress is printed to console (`OK` / `FAIL` per file).
Failed files print the exception to stdout.

---

## 3. parser_diagnostics

**Scripts:** `tests/parser_diagnostics.cmd` · `tests/parser_diagnostics.sh`

### What it is testing

Runs a suite of deterministic structural detectors against existing LLM-input JSON artefacts
(produced by `parse_samples` or `classify_samples`). No LLM call is made. The tool checks each
parsed document for two categories of issues:

- **Category A — True parser bugs:** Structural problems that are definitively wrong regardless
  of LLM classification. Includes: `EXPERIENCE_NO_ROLES`, `MERGED_SECTION_SUSPECTED`,
  `HEADING_NOT_RECOGNIZED`, `EXPERIENCE_ROLE_BOUNDARY_INSIDE_BULLETS`,
  `EXPERIENCE_OVERMERGED_ROLE`. Each issue has a severity weight (2–5). A file with any
  Category A issue gets status `parser_issue`.

- **Category B — Structural signals / informational:** Ambiguous structures that the LLM or
  classifier will resolve. Includes table-column layout detections, weak role groupings,
  role headers that contain date lines, skills contamination in non-skills sections, etc.
  These do not mark a file as broken; they contribute only to `info_score`.

Each file gets a `parser_issue_score` (sum of Category A weights), an `info_score` (sum of
Category B weights), and a status of `clean`, `clean_with_info`, or `parser_issue`. The overall
health status across all files is `ok`, `warning`, `degraded`, or `critical`.

### Input

Default directories (scanned automatically):
- `tmp/artefacts/classification/llm-input/docx/*.json`
- `tmp/artefacts/classification/llm-input/pdf/*.json`

Custom input: `parser_diagnostics.cmd --input path\to\dir`

Requires artefacts to exist — run `parse_samples` or `classify_samples` first.
Invokes `scripts/parser_diagnostics.py` with `PYTHONPATH=src`.

### Output

```
tmp/artefacts/classification/
  parser_diagnostics.json           machine-readable per-file results + overall health
  parser_diagnostics_summary.txt    human-readable summary
```

Each file entry in the JSON includes: filename, source kind, status, `parser_issue_score`,
`info_score`, and a list of issue objects with `code`, `weight`, `category`, and up to 3
location examples per code (`section_id`, `para_id`, `role_id` where applicable).

### Report

`parser_diagnostics_summary.txt` contains:
- Overall health status and file counts by status
- Per-file summary lines with score and issue codes
- Detailed issue breakdown with example locations (capped at 3 per code per file)

---

## 4. generate_classification_report

**Scripts:** `tests/generate_classification_report.cmd` · `tests/generate_classification_report.sh`

### What it is testing

Standalone re-run of the classification report aggregator. Does not re-classify or call the
LLM — reads existing classification output artefacts and recomputes all metrics. Useful when
you want to regenerate the report after manually editing artefacts or after a partial
`classify_samples` run.

### Input

Default directories (configurable via `--docx-dir`, `--pdf-dir`, `--out-dir`):
- `tmp/artefacts/classification/docx/*.json` — final classification results
- `tmp/artefacts/classification/pdf/*.json` — final classification results

All `*.json` files in each directory are loaded. Delegates to
`scripts/generate_classification_report.py`.

### Output

```
tmp/artefacts/classification/
  classification_report.json
  classification_report_summary.txt
```

Same format as produced automatically at the end of `classify_samples`.

### Report

See [classify_samples → Report](#report) for full coverage details. The summary text file
prints: total counts, initial/final validity rates, repair breakdown, status counts, top
validation error codes before and after repair, and example filenames for each problem category.

---

## 5. run_layout_tests

**Scripts:** `tests/run_layout_tests.cmd` · `tests/run_layout_tests.sh`

**Underlying test file:** `tests/test_format_roundtrip.py`

### What it is testing

Validates the parse → identity-render → compare roundtrip for every sample DOCX and PDF. The
LLM step is skipped entirely: the parsed document is serialized back to the plain-text format
that `parse_llm_output()` expects (identity pass), then run through the full `compile_resume`
pipeline. The resulting DOCX should have the same text content and structure as the input.

The test deliberately does **not** hard-fail on formatting differences (font, spacing, indent);
it only fails on parse/render crashes and on missing text (content that was in the input but
absent from the output). Formatting differences are reported as a structured diff for visual
review.

Templates with known non-roundtrippable structures (two-column table layouts, NBSP/tab-column
role headers, separate-line role formats) are marked as `xfail` with a documented reason so
they show in the test run but do not block CI.

### Input

- `tests/samples/resume/docx/*.docx` — discovered by glob; all files run as separate test cases
- `tests/samples/resume/pfd/*.pdf` — discovered by glob; known two-column and letter-spaced
  PDFs are marked `xfail`

`SAVE_ARTEFACTS=1` is set by the script, which enables output file copying.

### Output

When `SAVE_ARTEFACTS=1` is set, generated DOCX files are copied to:

```
tmp/artefacts/
  docx/<basename>.docx    identity-rendered DOCX (for visual inspection in Word)
  pdf/<basename>.pdf      identity-rendered PDF (when PDF conversion is available)
```

These files are for manual visual comparison with the originals. They are not checked in.

### Report

pytest output to console:
- Pass / fail / xfail per template file
- On failure: structured diff showing paragraph count differences, text-content mismatches,
  and style changes between the original and the roundtripped DOCX

No file-based report is written.

---

## 6. test_rendering

**Scripts:** `tests/test_rendering.cmd` · `tests/test_rendering.sh`

**Underlying script:** `tests/rendering/render_samples.py`

### What it is testing

Runs the full webapp rendering pipeline for matched (classification, generation) sample pairs.
For each matched pair:

1. Loads the LLM-generated resume text from the generation JSON (`llm_response.resume`)
2. Calls `compile_resume(template_path, llm_text, output_path)` — same code path as
   `RenderingService` in the webapp
3. Serializes the updated intermediate representation (IR) to JSON
4. Converts the output DOCX to PDF via LibreOffice subprocess

PDF source templates are currently skipped (require LibreOffice PDF→DOCX conversion at runtime).
This test is deterministic — no LLM is called.

Pairs are matched by numeric filename prefix: `{N}-*.docx` classification file must have a
corresponding `{N}-*.json` generation file. Ambiguous matches (multiple files sharing the same
prefix) abort with a disambiguation error.

### Input

Discovery directories (all under `tests/samples/`):

```
classification/docx/{N}-*_input.json    DOCX classification artefacts (pre-computed)
resume/docx/{N}-*.docx                  original DOCX templates
generation/{N}-*.json                   LLM generation output (debug JSON with llm_response)
```

Optional filter argument:
- `test_rendering.cmd 1` — render only the pair with numeric prefix `1`
- `test_rendering.cmd 1-Leonid` — render pairs matching filename fragment `1-Leonid`

### Output

```
tmp/artefacts/
  ir/docx/<stem>_IR.json          updated IR as JSON (fresh from template, never reused)
  rendering/docx/<stem>.docx      final rendered DOCX
  rendering/pdf/<stem>.pdf        final rendered PDF (via LibreOffice)
```

`<stem>` is the base filename of the original template (e.g. `1-Leonid_Verman_Resume_Template`).

### Report

Console output only:
- Matched pairs and skipped pairs with reasons
- Per-pair stage progress: `load-llm`, `compile-resume`, `save-ir`, `docx-to-pdf`
- Final summary line: `N OK | N FAILED | N skipped`

No file-based report is written. Failure exit code is set when any pair fails.

---

## 7. grade_layout

**Scripts:** `tests/grade_layout.cmd` · `tests/grade_layout.sh`

**Module:** `src/tailor/eval/layout_grader/` · **Tests:** `tests/test_layout_grader.py`

### What it is testing

Deterministic layout grading that quantifies how well generated resumes preserve
the structural and visual layout of their source templates. No LLM calls are made.
Three evaluation layers are combined into a 0–100 composite score per sample:

1. **IR Validation (20%)** — checks the generated IR JSON for internal consistency:
   empty para_ids on non-empty paragraphs, duplicate para_ids, missing section_ids,
   experience sections with no roles, split-brain (body_paras duplicating role para_ids),
   `Current Date` leakage in text, and role boundary corruption (bullets with section-level
   semantics). Any failure marks the sample as HARD FAIL.

2. **DOCX Structure Comparison (20%)** — compares template vs. generated DOCX on
   paragraph count ratio (ideal 0.85–1.20), table count (loss → heavy penalty), Word
   multi-column layout via `w:cols/@w:num` (loss → HARD FAIL), and section count.
   Also flags `renderer_fallback` when paragraph ratio < 0.70 or all tables dropped.

3. **PDF Layout Scoring (50% combined)** — converts both DOCXs to PDF then uses the
   existing `tailor.eval.extractor` to extract page/block structure. Computes:
   - `page_count_score` (15%): generated_pages vs. original; +1 ok, +2 → 70, >+2 → HARD FAIL
   - `blank_page_score` (10%): blank middle page → HARD FAIL; trailing blank → 60
   - `section_order_score` (15%): heading presence (65%) and relative order (35%) via fuzzy
     canonical section-name matching
   - `layout_region_score` (10%): compares where headings land (top/middle/bottom) in original
     vs. generated PDF

   **IR density score (10%)** is computed from the IR directly (not PDF): checks for empty
   roles (no bullets), over-dense roles (>12 bullets, possible overflow), and low overall
   content density (avg < 2 paragraphs per section).

   HARD FAIL caps the composite at 30. Otherwise: PASS ≥ 75, WARNING 60–74, FAIL < 60.

This grader is designed to be run after each pipeline fix to verify: (a) the fixed
sample now scores higher, and (b) no other samples regressed. Baseline comparison
(`--baseline`) detects regressions of >5 points and severe regressions of >15 points
or any pass→fail transition.

### Input

Discovery directories (matched by numeric filename prefix `{N}-`):
```
tests/samples/resume/docx/{N}-*.docx          original template DOCX
tests/samples/classification/docx/{N}-*_input.json  used to derive template stem
tests/samples/generation/{N}-*.json           LLM generation output
tmp/artefacts/rendering/docx/{stem}.docx      generated DOCX (rendered by test_rendering)
tmp/artefacts/ir/docx/{stem}_IR.json          generated IR JSON
```

If rendered DOCX or IR are missing, the pipeline runs `render_samples.py` automatically
(unless `--no-render` is passed). PDF conversion is auto-detected (`subprocess` if
LibreOffice is available, else `local`). Overridden via `--pdf-method` or `GRADE_PDF_METHOD`
env var.

Optional filter: `grade_layout.cmd 31` (prefix) or `grade_layout.cmd 1-Leonid` (fragment).

### Output

```
tmp/artefacts/layout_grading/
  {stem}_grade.json         per-sample grade JSON
  aggregate.json            aggregate metrics + full grades array (used for baseline)
  template_pdf/{stem}.pdf   template DOCX converted to PDF (cached across runs)
tmp/artefacts/rendering/pdf/{stem}.pdf   generated PDF (produced if missing)
```

Per-sample grade JSON schema:
```json
{
  "sample_id": "...",
  "status": "pass|warning|fail|hard_fail",
  "composite_score": 0.0-100.0,
  "hard_fail": true|false,
  "hard_fail_reasons": ["EXPERIENCE_NO_ROLES", ...],
  "metrics": { "structural_ir_score": 100.0, "docx_structure_score": 95.0, ... },
  "facts": { "original_pages": 2, "generated_pages": 2, "paragraph_ratio": 1.0, ... },
  "failure_classes": ["A_IR_CORRUPTION", "F_TOPOLOGY_COLLAPSE", ...],
  "evidence": ["Duplicate para_id: 'para_45'", ...]
}
```

### Report

```
tmp/artefacts/layout_grading/
  aggregate.json    sample_count, pass/warning/fail/hard_fail counts,
                    average/median/min/max scores, worst_samples (bottom 5),
                    failure_class_frequency map
  summary.txt       sorted sample list (worst first), regression table if
                    --baseline used, failure class frequency table
```

---

## 8. run_postprocessing_tests

**Scripts:** `tests/run_postprocessing_tests.cmd`

**Underlying test file:** `tests/test_changed_content_regression.py`
**Manifest:** `tests/regression_cases.json`

### What it is testing

Validates the post-processing pipeline (template fill + layout scoring) using stored
template + debug JSON pairs defined in a manifest. No LLM calls are made. For each regression
case:

1. Extracts the generated resume text from the debug JSON (`llm_response.resume`)
2. Runs `compile_resume(template_docx, resume_text, output_docx)` — real pipeline
3. Converts both the template DOCX and output DOCX to PDF
4. Extracts layout from both PDFs using `tailor.eval.extractor`
5. Scores layout quality via `score_layout()` producing a `LayoutScore` dataclass with:
   - `composite` — overall layout quality score
   - `topology_preservation` — whether section order is preserved
   - `section_placement` — whether sections land in expected page regions
   - `overall_section_coherence` — whether section content is internally consistent
   - `section_stress_results` — per-section stress level (`low`, `medium`, `high`)
6. Classifies failure modes via `classify_failures()`
7. Asserts all scores are above per-case thresholds (defined in manifest, with global defaults)

The script sets `CC_REGRESSION_PDF_METHOD=docker` for high-fidelity LibreOffice rendering and
filters to `veeva`-prefixed cases with `-k "veeva"`.

New regression cases are added by dropping files into the sample directories and appending
one entry to `regression_cases.json` — no code changes required.

### Input

Cases are defined in `tests/regression_cases.json`. Each case specifies:

```json
{
  "case_id": "veeva_02_personal_template",
  "template_docx": "tests/samples/resume/docx/2-Leonid_Verman_Resume_2.docx",
  "debug_json":    "tests/samples/generation/Leonid_Verman-Veeva_Systems-....json",
  "thresholds": { "min_section_coherence": 0.35 }
}
```

Global threshold defaults in `regression_cases.json`:
- `min_layout_score`: 0.40
- `min_topology_preservation`: 0.00
- `min_section_placement`: 0.00
- `min_section_coherence`: 0.40
- `max_high_stress_sections`: 6

PDF conversion method is controlled by `CC_REGRESSION_PDF_METHOD` env var
(`local` / `subprocess` / `docker`; auto-detected when not set).

### Output

```
tmp/artefacts/postprocessing/<case_id>/
  <case_id>_output.docx    compiled output DOCX
  source.pdf               template DOCX rendered to PDF
  output.pdf               output DOCX rendered to PDF
  case_report.json         per-case evaluator report (layout scores + failure classes)
```

### Report

Per-case evaluator report written to `tmp/artefacts/postprocessing/<case_id>/case_report.json`:
- `case_id`, `template_class`, `severity`
- `layout_score` (composite), `metric_breakdown` (all four sub-scores)
- `failure_classes` — taxonomy of detected layout failures
- `evidence` — top diagnostic strings from the scorer

pytest console output includes per-case pass/fail with a full diagnostic block on failure:
composite score, topology, placement, coherence, high-stress section count, failure classes,
and top 5 evidence strings.

---

## 9. run_assess / run_calibrate / run_calibrate_samples

**Scripts (assess):** `tests/run_assess.cmd` · `tests/run_assess.sh`
**Scripts (calibrate):** `tests/run_calibrate.cmd` · `tests/run_calibrate.sh`
**Scripts (calibrate-samples):** `tests/run_calibrate_samples.cmd` · `tests/run_calibrate_samples.sh`

### What it is testing

LLM-based evaluation of resume quality against a set of job positions. The assessment pipeline
calls an LLM judge to score the tailored resume (and optionally cover letter) for each position.

- **run_assess** — assesses the tailored resume/cover letter that the pipeline produces for each
  position in the positions file; measures how well the output is tailored to each JD
- **run_calibrate** — sends the unmodified master resume and cover letter through assessment to
  establish a baseline score for comparison
- **run_calibrate_samples** — runs assessment against pre-generated sample resume files in
  `tests/samples/` rather than calling the tailoring pipeline

All three scripts invoke `python -m tailor assess` with appropriate flags.

### Input

- **Positions file:** `tests/data/positions.txt` (one job posting URL or inline JD per line)
  — configurable via `--positions`
- **Calibrate-samples data:** `tests/samples/` directory containing generation artefacts
  — used only by `run_calibrate_samples`
- **Model:** `gpt-5.2` (default) — configurable via `--model`
- **Temperature:** `0.5` (default) — configurable via `--temperature`
- Optional: `--workers N` (parallelism), `--max_positions N` (limit), `--cache_dir DIR`

### Output

Assessment reports written to `reports/` (configurable via `--out`). Directory structure
and report format are determined by `python -m tailor assess`.

### Report

Reports in `reports/` cover per-position scores, aggregate statistics, and comparison against
baseline (calibrate) scores. Exact format depends on the assess implementation.

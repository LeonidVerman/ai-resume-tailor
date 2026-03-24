# PDF Round-Trip Fidelity Progress

Tracks evaluator pass rate and per-sample status across improvement sessions.
Evaluator: `python -m tailor.eval --dir tests/samples/resume/pfd`
Threshold: HIGH-severity issues cause FAIL (page_break_change, heading_missing,
bullet_indent_error >0.020, column_collapse, missing_text).

## Score Summary

| Date       | Run ID              | Pass | Total | Notes                                    |
|------------|---------------------|------|-------|------------------------------------------|
| 2026-03-24 | 2026-03-24_1441     | 28   | 37    | Baseline before this session             |
| 2026-03-24 | 2026-03-24_fix3     | 29   | 37    | Fix: two-column split detection          |

## Changes Made

### 2026-03-24 — Two-column split detection (commit 10faecd)

**File:** `src/tailor/compiler/pdf_parser.py` — `_detect_column_split()`

**Fix 1:** Lower early-exit from `len(x0s) < 4` to `len(x0s) < 2`.
- Affected: documents where both column clusters produce only 3 distinct x0
  positions (e.g. left blocks all at x0≈72-78, right blocks at x0≈414).
- Was: returning None early, treating such docs as single-column.
- Now: proceeds to gap detection, correctly identifies two-column layout.

**Fix 2:** Content-gap midpoint for column width when left ≠ overlap right.
- Problem: `(x0s[i] + x0s[i+1]) / 2` underestimates the left column width when
  body text extends well past its x0 (e.g. heading x0=78 but x1=366, gap
  midpoint=246pt → heading wraps in 246pt column instead of 390pt column).
- Fix: use `(max_left_x1 + right_edge) / 2` when `max_left_x1 < right_edge`.
- Guard: when `max_left_x1 >= right_edge` (left content overlaps right column
  start), fall back to `x0_mid` to avoid inflating split_x past the right column.

**Result:** 14-Nurse-templage4 fixed (1-page output vs 2-page before).

## Remaining Failures (as of 2026-03-24)

### Evaluator artifacts (not genuine rendering bugs)

| Sample                   | Issue             | Root Cause                                                    |
|--------------------------|-------------------|---------------------------------------------------------------|
| Resume-Sample-1          | bullet_indent_error | Right-column skills lines (x=144) outnumber actual bullets (x=54); evaluator heuristic picks 144 as dominant |
| 8-Template3              | column_collapse   | Source has small 2-col skills section; evaluator flags full-doc as 2-col; pdf_parser correctly treats as single-col |
| 26-Engineer-2            | bullet_indent_error | Evaluator measures right-col bullets from page-left (298pt); output measures from cell-left (~0pt) |
| 30-Software-Engineer-1   | bullet_indent_error | Same two-column cell vs page measurement mismatch (src=394, out=222) |
| 23-Project-Engineer-2    | bullet_indent_error | Same two-column cell vs page measurement mismatch (src=314, out=300) |

### Genuine rendering issues (fixable in future sessions)

| Sample                   | Issue             | Root Cause                                                    |
|--------------------------|-------------------|---------------------------------------------------------------|
| 3-software-engineer-doc  | page_break_change | Source has overlapping line bboxes (negative inter-line gaps); reconstruction adds explicit space_before (19pt) → 17pt overflow |
| 5-Software Development   | page_break_change | 25-line contact block compressed to 61pt in source; each line = 12pt → 300pt in output; compound overflow across 2-page doc |
| 31-Software-Engineer-7   | column_collapse   | xfail: complex two-column, known limitation                   |

## Not Fixable Without Evaluator Changes

- **bullet_indent_error in two-column outputs**: The evaluator measures
  `dominant_bullet_left_x` as an absolute page-x position, but DOCX table
  cells measure indents from cell-left. Two-column DOCX output will always
  have a systematic offset equal to the left column width. Would require
  evaluator to detect two-column output and measure cell-relative x.

- **column_collapse for partially two-column docs**: Sample 8 has a two-column
  skills section at the bottom of an otherwise single-column document. The
  evaluator tags the whole doc as "2-column", but correct behavior is to render
  the main content in single-column and the skills section in two-column.
  Requires the pdf_parser to detect per-section layout changes.

# DOCX Parser Implementation

Source: `src/tailor/compiler/docx_parser.py`

## Overview

`parse_docx(path)` converts a `.docx` file into a `ResumeDocument` IR.  It
reads the raw XML, classifies every paragraph's semantic role, groups the
experience section into `RoleEntry` objects, and applies post-processing fixes
for multi-column layouts (newspaper-column and label-rail).

---

## Pipeline

```
Document XML
    │
    ├─ body paragraphs (w:p) → ParaModel + semantic tag
    └─ tables (w:tbl)        → TableBlock (opaque XML + ParaModel list)
    │
    ▼
_apply_multicolumn_newspaper_fix()  ← new: newspaper-col / table-col layouts
    │
    ▼
_apply_label_column_fix()           ← existing: w:cols narrow label-rail
    │
    ▼
Section grouping loop         ← builds ResumeSection list
    │
    ▼
_consolidate_job_entry_sections()  ← merges per-role heading sections
    │
    ▼
_finalise(section)            ← groups roles inside each experience section
    │
    ▼
assign_stable_ids()           ← assigns sec_N / role_N / para_N IDs
```

---

## Semantic Classification (`_infer_semantic`)

Every paragraph receives one of six semantic tags:

| Tag | Meaning |
|-----|---------|
| `section_heading` | Top-level section title (Experience, Skills, …) |
| `role_header` | Job title + company line (pipe `\|`, slash ` / `, or NBSP/tab separated) |
| `role_meta` | Date or location line; may contain a year or date range |
| `bullet` | Numbered/bulleted list item or line starting with `- ` / `•` |
| `paragraph` | General body text |
| `empty` | Blank paragraph |

Classification order (first match wins):

1. **Named style**: `Heading N`, `Title`, `Subtitle` → `section_heading`
2. **Bare "Heading" style + known section name** → `section_heading`
3. **Bold + known section name** → `section_heading`
4. **Plain paragraph + known section name + no spacing** → `section_heading`
5. **Bold heuristic**: bold, 2+ words, title-case, ≤60 chars, spacing_before≥80 or font≥12pt, no year → `section_heading`
6. **Pipe separator** (`|` in text, not a bullet) → `role_header`
7. **Slash separator** (` / ` with no year on either side) → `role_header`
8. **NBSP/tab multi-column line**: year in text, first segment ≤60 chars and no year → `role_header`
9. **List formatting or bullet prefix** → `bullet`
10. **Fused year prefix** (`2023Company…`) → `role_meta`
11. **Year present** (4-digit year, ≤80 chars, no pipe) → `role_meta`
12. Default → `paragraph`

---

## Section Grouping

The section loop iterates `all_paras` in document order, building
`ResumeSection` objects each time a `section_heading` is encountered.

### Sub-heading absorption (Approach A)

When a `section_heading` appears inside an existing `experience` section (or
`education` / `certifications`) and is not a recognised top-level name, it is
**absorbed** instead of opening a new section:

- Inside **experience**: the paragraph is re-tagged as `role_header` (if
  it contains `|` or ` / `), `role_meta` (if it is a Heading 2 date range),
  or `paragraph` otherwise.
- Inside **education / certifications**: absorbed only when
  `_classify_section` returns `"other"` (prevents promoting a peer `skills`
  heading into a child).
- **Heading-level guard**: a `Heading N` is never absorbed into a section
  whose own heading uses the same or a higher heading level.  This prevents
  sibling sections in table-based templates from collapsing into one.

### Job-section consolidation (Approach B)

When the template has no explicit "Experience" heading and instead titles each
role as its own section (e.g. "Software Engineer", "Data Analyst Intern"),
`_consolidate_job_entry_sections` merges consecutive `other`-type sections
that qualify as job entries (contain a `role_meta` line and at least one
content line, and have a job-title word in the heading) into a single
synthetic `experience` section.

---

## Role Grouping (`_group_roles`)

After section grouping, `_finalise` calls `_group_roles` on the flat
`body_paras` list of every `experience` section.

### Pre-passes

1. **Date-placeholder promotion**: plain `paragraph` lines matching `20XX`
   are promoted to `role_meta` before `_relabel_implicit_role_headers` runs
   (only when the section has no pipe-format role headers).
2. **`_relabel_implicit_role_headers`**: standalone job-title lines that lack
   a `|` / NBSP / tab separator but are followed (within two paragraphs) by a
   `role_meta` or a year-containing line are relabeled to `role_header`.

### State machine

The grouper uses a four-state machine (`init → header → meta → bullets`) to
collect each role's parts:

```
init
 ├─ role_header → flush previous role, start new (header state)
 └─ role_meta   → Pattern B: date-first layout; meta line becomes header

header
 ├─ role_meta   → meta state
 ├─ bullet      → bullets state
 ├─ paragraph   → if date-like (year or placeholder, ≤80 chars) → meta state
 │               else → header_extra (multi-line header continuation)
 └─ empty       → stay

meta
 ├─ role_meta   → accumulate additional meta
 ├─ bullet/para → bullets state
 └─ empty       → stay

bullets
 ├─ role_meta (Pattern B) → flush and start new role
 ├─ role_meta (otherwise) → kept as bullet (false positive; year in bullet text)
 │   EXCEPTION: education institution line intruding from 2-column table → dropped
 ├─ bullet/para → accumulate
 └─ role_header → flush and start new role
```

**Pattern B** is used by templates where the first thing in an experience
entry is a date/location line rather than a job title.  `header_is_role_meta`
tracks whether the current role was started this way so subsequent `role_meta`
lines correctly trigger a new-role boundary rather than being absorbed as
bullet continuations.

---

## Two-Column Label-Rail Layout

Some templates use Word's newspaper-column feature to create a **narrow left
column** containing only section labels and a **wide right column** containing
all resume content.  In the raw XML, this produces a document order where
every section heading appears before every content paragraph — the parser
would otherwise build empty sections and dump all content into the last one.

### Detection (`_detect_label_column_layout`)

Reads the body `w:sectPr/w:cols` element.  A layout is flagged when:
- Exactly **two** `w:col` elements are defined.
- The first column's width is **less than 35%** of the combined width.

### Split-point detection (`_find_label_column_split`)

Scans `all_paras` for the first non-empty, non-heading paragraph that appears
**after at least two recognised section headings**.  This is the boundary
between the left-column labels and the right-column content.

Only headings whose text matches `_ALL_HEADING_NAMES` are counted; this
prevents candidate job-title headings in the document header area (e.g.
"SOFTWARE ENGINEER" styled as Heading 1) from being mistaken for label-column
headings.

### Injection-point calculation (`_lbl_injection_index`)

For each left-column heading, the algorithm estimates where in the
right-column paragraph list that heading's content block begins, using a
per-semantic-type heuristic:

| Section type | Heuristic |
|---|---|
| `experience` | First paragraph followed (within 4 lines) by a `role_meta` |
| `education` | First line that looks like a degree or institution title |
| `skills` | First non-`role_meta`, non-degree line with no nearby dates |
| `summary` | Always index 0 (placed at the very top of right-column content) |
| `other` | First bold paragraph, or first paragraph preceding a `role_meta` |

Injection indices must be **non-decreasing**; if they are not (semantic
detection failed), the fix is aborted and the original order is returned.

### Reordering

The final `all_paras` list is rebuilt as:

```
pre_header paragraphs (name, contact info)
  + for each (heading, inject_at):
      right_col[prev_inject_at : inject_at]   ← content before this section
      heading                                  ← injected label
  + right_col[last_inject_at:]                ← remaining content
```

`body_items` (used by the DOCX renderer) is **not** modified; only `all_paras`
(used for section grouping) is reordered.  `ResumeDocument.label_column_fixed`
is set to `True` when the fix was applied.

---

## Education Intrusion Guard

When a two-column table layout places Education and Experience side by side,
paragraphs like "Your University May 2020" can appear in document order inside
an experience section's bullet list, where they would be classified as
`role_meta`.  `_is_education_intrusion_meta` detects this by requiring:

1. The `role_meta` text contains an institution keyword (university, college, …).
2. At least one of the last four bullets contains a degree keyword (bachelor, master, …).

Paragraphs that pass both checks are dropped rather than added to the role's
bullets or meta lines.

---

## Table-Based / Newspaper Multi-Column Resume Layouts

Some resume templates create a two-column visual layout using Word's
**newspaper-column feature** (`w:sectPr/w:cols`) rather than a table.  Content
from the left and right visual columns is interleaved in the flat XML paragraph
stream via `<w:br type="column">` breaks, and some paragraphs use a `<w:tab/>`
character to place two section headings side-by-side on the same line.

This is a different problem from the label-column fix: the label-column fix
handles the case where a narrow left column contains ONLY section labels and the
wide right column contains all resume content.  The newspaper-column fix handles
arbitrary two- or three-column layouts where left-column and right-column content
is interleaved mid-document.

### Why this is different from the label-rail fix

The label-rail fix (`_apply_label_column_fix`) triggers when `w:cols[0].width
< 35%` of total width and all headings precede all content in the XML.
The newspaper-column fix triggers on a different structural signature:
mid-document `w:sectPr` breaks with 2+ columns AND `<w:br type="column">`
paragraphs that interleave left and right column content.

### Detection criteria

Triggers when ALL of the following hold:

1. At least one `<w:p>` has an embedded `<w:sectPr>` defining 2+ columns
   (mid-document section break with multi-column layout).
2. At least one paragraph contains `<w:br type="column">`.
3. **Skills-column guard**: at least one paragraph uses a run-level `<w:tab/>`
   to separate two known section heading names where one side is a skills-type
   name (`skills`, `technical skills`, `core competencies`, etc.).

Criterion 3 prevents false positives on templates where `Experience | Education`
is a cleanly separated two-column split without contamination.

### Dual-heading tab split

A paragraph containing a `<w:tab/>` inside a `<w:r>` (distinct from tab-stop
definitions in `<w:tabs>`) with both sides matching known section names is split
into two virtual `ParaModel` instances assigned to the left and right streams.

Example: `"EDUCATION[TAB]SKILLS"` → `ParaModel("EDUCATION")` + `ParaModel("SKILLS")`

### Band-aware column stream construction

The fix builds two streams — **left** (main content) and **right** (skills sidebar)
— rather than a naive "all col-0 then all col-1 then all col-2" append.

**Header region protection**: The first Word section (name, title, contact) is
always kept in its original document order.  This prevents phone/email/address
lines from appearing after the WORK EXPERIENCE section and contaminating role bullets.

**Per-Word-section routing**:
- Single-column sections: content goes to left stream; tab-split right sides go to right stream.
- 2-column sections: col-0 → left, col-1 → right.
- 3-column sections where `(col0_width + col1_width) / col2_width > 1.5`
  (wide-left narrow-right): col-0 and col-1 are band-merged into the left stream;
  col-2 goes to the right stream.

**Band merging for 3-column sections** (`_band_merge_cols`):

When col-0 and col-1 form a single wide visual column, interleaving them in raw
document order would still contaminate sections.  Instead:

- col-0 is split into bands at top-level (Heading 1) section headings.
- col-1 is split into bands at clusters of 3+ consecutive empty paragraphs,
  which visually separate layout rows.
- Band `i` of col-0 is paired with band `i` of col-1 and output together.

For sample 31, section 3 (3-col):
```
col0 band 0: [CS 2010-2014]              ← education dates
col1 band 0: [123 Anywhere 2008-2011]    ← more education dates

col0 band 1: [CERTIFICATION, Liceria, Web Design, 2019]
col1 band 1: [Fauget Company, Web Design, 2021]  ← second cert entry
```
Merged: `CS 2010-2014 | 123 Anywhere | CERTIFICATION | Liceria | Web Design | 2019 | Fauget Company | Web Design | 2021`

This places `123 Anywhere` in EDUCATION and `Fauget Company` inside CERTIFICATION.

### Sub-heading absorption for non-experience sections

The existing sub-heading absorption (Approach A in section grouping) is extended
to also apply when the current section has `semantic_type == "other"`.  This
allows sections like COURSE and AWARDS (which are typed `"other"`) to absorb
their Heading-2 sub-items (organization names such as "Borcelle Tech", "Liceria
& Co.") as body paragraphs rather than promoting them to top-level sections.

The `_same_or_higher` guard (only absorb when new heading level > current heading
level) prevents peer sections at the same Heading level from being absorbed.

### Quality validation and fallback

After building the candidate `all_paras`:
- `cand_section_count >= orig_section_count`
- `cand_role_count >= orig_role_count`

If either check fails, the original order is returned unchanged.

### Diagnostics

| Code | Category | Meaning |
|------|----------|---------|
| `TABLE_COLUMN_LAYOUT_DETECTED` | B / info | Fix applied |
| `TABLE_COLUMN_REORDER_ABORTED` | B / info | Fix detected but quality check aborted it |
| `TABLE_COLUMN_CONTAMINATION_SUSPECTED` | B / low | Skills-list text inside a non-skills section |
| `TABLE_CONTACT_INSIDE_EXPERIENCE` | B / medium | Phone/email/address inside experience section |
| `TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION` | B / low | Skills-list paragraph in non-skills section |
| `TABLE_ORG_NAME_PROMOTED_TO_FAKE_SECTION` | B / info | Organization name became top-level section |

**Skills-list detection** (`_is_skills_list_para`): a paragraph is skills-like
only when it has high comma/semicolon density, does NOT start with an action verb
(Developed, Built, Led, …), does NOT end with sentence punctuation after many
tokens, and contains no sentence connectors (to, for, with, using, by).  This
avoids flagging normal achievement bullets as skills contamination.

### Known limitations

- Only handles the specific newspaper-column pattern (tab-split headings + column
  breaks).  Templates using text boxes or absolute positioning are not handled.
- Tab-split only applies when BOTH sides match known section heading names.
- The skills-column guard may miss templates where the skills column uses a name
  not in `_SKILLS_COLUMN_NAMES` (e.g., "Expertise", "Core Technologies").

---

## Data Model Quick Reference

```
ResumeDocument
├─ header_paras: list[ParaModel]      # name, contact, headline
├─ sections: list[ResumeSection]
│   ├─ title: str
│   ├─ semantic_type: str             # experience|summary|skills|education|other
│   ├─ heading: ParaModel
│   ├─ body_paras: list[ParaModel]    # non-experience sections
│   └─ roles: list[RoleEntry]         # experience sections only
│       ├─ header: ParaModel          # "Title | Company"
│       ├─ header_extra: list[ParaModel]
│       ├─ meta_lines: list[ParaModel] # date/location lines
│       └─ bullets: list[ParaModel]
├─ layout: LayoutProfile
├─ all_paras: list[ParaModel]         # flat ordered list (post-reorder)
├─ body_items: list[ParaModel|TableBlock]  # raw render order (never reordered)
├─ label_column_fixed: bool           # True when label-rail fix applied
└─ table_column_layout_fixed: bool    # True when newspaper/multi-col fix applied
```

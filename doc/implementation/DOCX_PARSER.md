# DOCX Parser Implementation

Source: `src/tailor/compiler/docx_parser.py`

## Overview

`parse_docx(path)` converts a `.docx` file into a `ResumeDocument` IR.  It
reads the raw XML, classifies every paragraph's semantic role, groups the
experience section into `RoleEntry` objects, and applies a post-processing fix
for the two-column label-rail layout that some templates use.

---

## Pipeline

```
Document XML
    │
    ├─ body paragraphs (w:p) → ParaModel + semantic tag
    └─ tables (w:tbl)        → TableBlock (opaque XML + ParaModel list)
    │
    ▼
_apply_label_column_fix()     ← reorders all_paras for 2-col label layouts
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
└─ label_column_fixed: bool
```

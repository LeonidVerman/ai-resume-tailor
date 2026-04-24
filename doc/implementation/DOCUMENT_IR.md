# Document Internal Representation (IR)

The IR is the in-memory (and optionally serialized) model of a resume document that sits
between the raw source file (DOCX or PDF) and the final rendered output. It captures both
the **structured content** (sections, roles, bullets) and the **visual formatting** (paragraph
styles, XML prototypes, page geometry) needed to reconstruct a correctly formatted DOCX after
the LLM has rewritten the text.

All classes are defined in `src/tailor/compiler/models.py`.

---

## 1. Object Tree

```
ResumeDocument
├── header_paras: list[ParaModel]          # name, contact — before first section
├── sections: list[ResumeSection]
│   ├── heading: ParaModel
│   ├── body_paras: list[ParaModel]        # summary / skills / other
│   └── roles: list[RoleEntry]             # experience sections only
│       ├── header: ParaModel
│       ├── header_extra: list[ParaModel]  # long-header continuations (not rendered)
│       ├── meta_lines: list[ParaModel]    # date / location
│       └── bullets: list[ParaModel]
├── layout: LayoutProfile
├── all_paras: list[ParaModel]             # flat document-order mirror (all paragraphs)
└── body_items: list[ParaModel | TableBlock] | None   # DOCX render order; None for PDF/deserialized
```

---

## 2. Classes

### `ParaModel`

The fundamental unit — one paragraph.

| Field | Type | Description |
|---|---|---|
| `text` | `str` | Plain text content |
| `style` | `ParaStyle` | Formatting metadata (DOCX source) |
| `semantic` | `str` | See §3 |
| `paragraph_profile` | `ParagraphProfile \| None` | Set for PDF source; `None` for DOCX source |
| `para_id` | `str` | Stable positional ID (`"para_1"`, `"para_2"`, …); `""` until `assign_stable_ids()` runs |

Key methods:
- `with_text(new_text)` — shallow copy, new text
- `clone_as(new_text, semantic)` — deep-copies `xml_proto` and `paragraph_profile`; for extra bullets
- `to_dict() / from_dict()` — round-trips `text`, `semantic`, `paragraph_profile`, `para_id`; **excludes `xml_proto`**

---

### `ParaStyle`

Paragraph and run-level formatting from DOCX sources (Word XML).

| Field | Type | Description |
|---|---|---|
| `style_name` | `str \| None` | Resolved Word style name (e.g. `"Heading 1"`, `"List Paragraph"`) |
| `alignment` | `str \| None` | `w:jc/@val` |
| `indent_left` / `indent_right` | `int \| None` | Twips |
| `hanging` | `int \| None` | Hanging indent in twips |
| `spacing_before` / `spacing_after` | `int \| None` | Twips |
| `line_spacing` | `int \| None` | Twips |
| `keep_with_next` | `bool \| None` | Word keep-with-next flag |
| `numbering` | `dict \| None` | `{"ilvl": int, "numId": int}` for list items |
| `bold`, `italic` | `bool \| None` | Run-level overrides from first run |
| `font_name` | `str \| None` | Font from first run |
| `font_size_pt` | `float \| None` | Size from first run |
| `color` | `str \| None` | Hex RRGGBB from first run |
| `xml_proto` | lxml element | `deepcopy` of original `w:p` — **not serialized; runtime only** |

`xml_proto` is the primary vehicle for DOCX style preservation. The renderer clones it and
patches the text, replaying every formatting detail (runs, numbering refs, hyperlinks, etc.)
exactly.

---

### `ParagraphProfile`

Formatting profile for PDF-sourced paragraphs (no Word XML available).

| Field | Type | Serialized | Description |
|---|---|---|---|
| `font_name` | `str \| None` | yes | Normalized Windows-compatible font name |
| `font_size_pt` | `float \| None` | yes | Point size (modal across spans) |
| `bold` | `bool` | yes | Bold flag |
| `italic` | `bool` | yes | Italic flag |
| `indent_left_pt` | `float` | yes | Points from column's left edge |
| `hanging_indent_pt` | `float` | yes | Hanging (first-line outdent) indent |
| `space_before_pt` | `float` | yes | Estimated Y-gap to previous block |
| `space_after_pt` | `float` | yes | Space after paragraph |
| `alignment` | `str \| None` | yes | `left \| center \| right \| justify` |
| `text_color` | `str \| None` | yes | Hex RRGGBB foreground color |
| `background_color` | `str \| None` | yes | Hex RRGGBB paragraph shading fill |
| `column_id` | `str \| None` | yes | `left \| right \| None` for two-column layouts |
| `body_text_x0_pt` | `float` | **no** | Raw per-line x0; used by PUA bullet-pair merging |
| `inline_image_bytes` | `bytes \| None` | **no** | PNG bytes of inline icon extracted from PDF |
| `inline_image_size_pt` | `float` | **no** | Icon square size in points |
| `text_runs` | `list \| None` | **no** | `[(text, bold), …]` for mixed-bold role headers |

---

### `RoleEntry`

One job entry inside an experience section.

| Field | Type | Description |
|---|---|---|
| `header` | `ParaModel` | Role header line (e.g. `"Senior Engineer \| Acme Corp"`) |
| `header_extra` | `list[ParaModel]` | Continuation lines for long headers (not rendered) |
| `meta_lines` | `list[ParaModel]` | Date / location paragraphs |
| `bullets` | `list[ParaModel]` | Achievement bullet points |
| `role_id` | `str` | Normalized header text — used for text-based matching in the updater |
| `role_id_stable` | `str` | Synthetic positional ID (`"role_1"`, `"role_2"`, …); `""` until `assign_stable_ids()` runs |

---

### `ResumeSection`

One section of the resume.

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Raw heading text from the document |
| `heading` | `ParaModel` | The heading paragraph |
| `semantic_type` | `str` | See §4 |
| `body_paras` | `list[ParaModel]` | Flat content for non-experience sections |
| `roles` | `list[RoleEntry]` | Populated for `experience` sections; empty otherwise |
| `section_id` | `str` | Synthetic positional ID (`"sec_1"`, `"sec_2"`, …); `""` until `assign_stable_ids()` runs |

---

### `LayoutProfile`

Page-level geometry.

| Field | Type | Description |
|---|---|---|
| `page_width_pt`, `page_height_pt` | `float` | Page dimensions in points |
| `margin_top/bottom/left/right_pt` | `float` | Page margins in points |
| `default_font_name` | `str` | Fallback font (from Normal style or PDF modal) |
| `default_font_size_pt` | `float` | Fallback font size |
| `column_split_x` | `float \| None` | X-coordinate of two-column split (PDF only) |
| `left_col_width_twips` / `right_col_width_twips` | `int \| None` | Column widths in twips (PDF only) |
| `left_col_bg_color` / `right_col_bg_color` | `str \| None` | Hex RRGGBB column background fills (PDF only) |

---

### `TableBlock`

An opaque Word table preserved for layout-faithful rendering (DOCX source only).

| Field | Type | Description |
|---|---|---|
| `xml_proto` | lxml element | `deepcopy` of original `w:tbl` — structure, borders, shading fully preserved |
| `para_models` | `list[ParaModel]` | References to the `ParaModel` for each `w:p` inside the table, in document order |

During rendering the table XML is cloned and each cell paragraph's text is patched in-place
from its `ParaModel.text`. `TableBlock` is never serialized.

---

### `ResumeDocument`

Root of the IR tree.

| Field | Type | Description |
|---|---|---|
| `header_paras` | `list[ParaModel]` | Paragraphs before the first section heading (name, contact) |
| `sections` | `list[ResumeSection]` | Structured section list |
| `layout` | `LayoutProfile` | Page geometry |
| `all_paras` | `list[ParaModel]` | Flat document-order mirror of every paragraph |
| `source_kind` | `str` | `"docx"` or `"pdf"` |
| `body_items` | `list[ParaModel \| TableBlock] \| None` | Top-level render order; `None` for PDF / deserialized |
| `label_column_fixed` | `bool` | `True` when label-column layout reordering was applied by the parser |

`to_dict()` serializes `source_kind`, `header_paras`, `sections`, `layout`, and optionally
`label_column_fixed`. `all_paras` and `body_items` are **not** serialized — `all_paras` is
rebuilt on deserialization; `body_items` is always `None` after deserialization.

---

## 3. Paragraph Semantic Types

| Semantic | Description | Assigned when |
|---|---|---|
| `section_heading` | Section title | Matches `"Heading N"` Word style; title-case bold with size/spacing thresholds; bare `"Heading"` style + known name; or known section name with no style/bold (plain-text template) |
| `role_header` | Job title / company line | Contains `\|` pipe separator; or `/` separator with no year in either half; or standalone job-title word with short text preceding a `role_meta` line |
| `role_meta` | Date / location line | Contains a 4-digit year (or `20XX` placeholder), ≤ 80 chars, no pipe |
| `bullet` | Achievement bullet | Has Word numbering properties; starts with `List` style; or starts with a bullet character (`-`, `•`, `·`, `–`, `●`, `◉`, …) |
| `paragraph` | General body text | Any other non-empty text |
| `empty` | Spacer | No text or whitespace only |

---

## 4. Section Semantic Types

| `semantic_type` | Heading keywords (examples) |
|---|---|
| `experience` | Experience, Work Experience, Employment, Professional Experience, Work History |
| `summary` | Summary, Professional Summary, Objective, Profile, About |
| `skills` | Skills, Technical Skills, Technologies, Core Competencies, Relevant Skills |
| `education` | Education, Academic Background, Educational History |
| `certifications` | Certifications, Licenses, Credentials |
| `languages` | Languages |
| `websites` | Links, Websites, Portfolio |
| `other` | Anything else (Awards, Volunteer Work, Publications, Contact, etc.) |

---

## 5. Stable IDs

`assign_stable_ids(doc: ResumeDocument)` in `models.py` assigns positional synthetic IDs after
parsing:

- `section.section_id` → `"sec_1"`, `"sec_2"`, …
- `role.role_id_stable` → `"role_1"`, `"role_2"`, … (globally sequential across all sections)
- `para.para_id` → `"para_1"`, `"para_2"`, … (globally sequential across the whole document)

IDs are assigned in document order and are stable for the same parsed document. They round-trip
through `to_dict() / from_dict()` so a deserialized IR carries the same IDs as the original
parse. Calling `assign_stable_ids` a second time re-assigns from scratch (idempotent).

These IDs are used by the LLM classification pipeline to reference specific paragraphs and
roles in diagnostic output, without relying on mutable text content.

---

## 6. Building the IR

### From a DOCX template

**Entry point:** `parse_docx(path: str) -> ResumeDocument` in `src/tailor/compiler/docx_parser.py`

1. python-docx opens the file; all `w:p` and `w:tbl` elements iterated from `doc.element.body`.
2. Style IDs resolved to friendly names via `doc.styles`.
3. Page dimensions and margins read from `w:pgSz` / `w:pgMar`; default font from Normal style.
4. Per `w:p`: text extracted from `w:t` elements (`w:br` → `\n`); `ParaStyle` populated from
   `w:pPr` and first run; `xml_proto = deepcopy(w:p)` captured; semantic inferred.
5. Per `w:tbl`: all nested `w:p` parsed as `ParaModel`; table wrapped in `TableBlock(deepcopy(w:tbl))`.
6. **Label-column fix**: when the document uses a narrow left column of section labels and a wide
   right column of content (detected by `w:cols` with left column < 35% of right), paragraphs are
   reordered to reading order (left-column labels interleaved with right-column content).
   `ResumeDocument.label_column_fixed = True` is set.
7. Paragraphs before the first `section_heading` → `header_paras`.
8. Each `section_heading` starts a new `ResumeSection`.
9. Experience sections: `body_paras` split into `RoleEntry` objects by `_group_roles()` state machine
   (`init → header → meta → bullets`).
10. Consecutive `other`-typed sections that look like standalone job entries consolidated into a
    synthetic experience section.
11. `assign_stable_ids(doc)` called before return.

### From a PDF

**Entry point:** `parse_pdf(pdf_bytes: bytes) -> ResumeDocument` in `src/tailor/compiler/pdf_parser.py`

1. Scanned document guard: < 50 characters total → `RuntimeError`.
2. Duplicate page detection: repeated pages (exact text) skipped after the first occurrence (handles template gallery PDFs).
3. Header/footer suppression via four strategies: exact-text repetition across pages, Y-position
   buckets, absolute zone exclusion (top/bottom 8% on pages 2+), running-header repetition.
4. Two-column detection: `_detect_column_split()` finds a whitespace gap ≥ 9% of page width
   spanning ≥ 30% of page height. Paragraphs reordered to left-column then right-column reading order.
5. Raw PyMuPDF blocks coalesced into logical paragraphs via Y-gap and same-row fragment merging.
6. `ParagraphProfile` built for each paragraph: font, size, bold/italic, indent, spacing, color, column assignment.
7. Bullet detection: PUA glyph + text pairs merged; small vector bullet shapes detected; continuation
   lines re-joined by lowercase-start heuristic.
8. Inline icons (6–20 pt vector drawings) rasterized to PNG and stored as `inline_image_bytes`.
9. ~70 custom/subset fonts normalized to standard Windows equivalents; unknown fonts fall back to document default.
10. Section and role grouping (same logic as DOCX, §6 steps 7–9, with bold/size inference instead of Word styles).

---

## 7. LLM Output Merging

**Entry point:** `apply_tailored(original: ResumeDocument, llm_sections: list[LlmSection]) -> ResumeDocument`
in `src/tailor/compiler/updater.py`

The LLM emits plain text parsed by `text_parser.py` into `LlmSection` / `LlmRole` objects using
the same heading/pipe/year/bullet heuristics as the parser.

### Section matching

1. Pass 1: exact heading match (case-insensitive).
2. Pass 2: semantic type match (when headings differ but `semantic_type` agrees).
3. Hard fail: if any LLM section is unmatched **and** any original content section is also unmatched
   → return `original` verbatim (no partial recovery).

**Locked sections** — never modified regardless of LLM output:
```
education  certifications  languages  websites
```

Only `summary`, `experience` (bullets only), and `skills` are rewritten.

### Role updating (experience sections)

- Roles matched by position to originals. Header and meta-line `xml_proto`s reused verbatim;
  bullet protos reused for existing bullets, cloned from an archetype for extras; surplus originals dropped.
- Dash-format path: when the LLM writes `"Title — Company"`, only bullets are updated; headers and
  meta lines kept verbatim from the template.

### Body section updating (summary, skills)

- Decorative paragraphs (mixed-font runs, no alphanumerics) detected and preserved verbatim.
- Content paragraphs paired by position with LLM lines; archetype cloned for extras.
- Spacer/empty paragraphs preserved for visual layout.

### Skills sanitization

Automatically removed from the skills output:
- Date markers (`CURRENT_DATE`, `"Generated on"`, etc.)
- Lines starting with `"Additional"`
- Full prose sentences (≥ 6 tokens ending with `.`, `!`, or `?`)

### Extra sections

- Extra experience sections: never created.
- Extra skills in a header column: injected into `header_paras`.
- Extra summary: placed before existing sections in the right column.
- Other extras: inserted at LLM output position, styles cloned from nearest existing section,
  forced to left alignment.

---

## 8. Rendering Back to DOCX

**Entry point:** `render_docx(doc: ResumeDocument, template_path: str, output_path: str) -> None`
in `src/tailor/compiler/docx_renderer.py`

### DOCX-sourced documents (`xml_proto` path)

1. Template DOCX copied to output (preserves styles, numbering definitions, headers, footers).
2. All `w:p` and `w:tbl` children stripped from body (final `w:sectPr` retained).
3. For each item in `body_items`:
   - `ParaModel` → clone `xml_proto`, call `_set_para_text()`, insert before `w:sectPr`.
   - `TableBlock` → clone `xml_proto`, patch each nested `w:p` text from `para_models`, insert.

**Text distribution in `_set_para_text()`:**
- Whitespace-only runs (NBSP, soft hyphen, zero-width chars) restored to original text.
- Content runs: new text distributed proportionally across runs by original character lengths.
- Residual `w:tab` elements stripped after distribution (become mid-word characters after content changes).
- VML text boxes (`w:pict`) left untouched.

### PDF-sourced documents (`para_builder` path)

When `xml_proto` is `None`, `build_para_element()` in `src/tailor/compiler/para_builder.py`
constructs a fresh `w:p` from `ParagraphProfile`:

- Paragraph properties (`w:pPr`): style, alignment, indentation, spacing, shading.
- PUA bullets: three-run structure — Symbol glyph + tab + body text.
- Regular bullets: `ListParagraph` style with inline `"• "` prefix.
- Line spacing forced to exact (consistent across LibreOffice and Word).
- Inline images appended as `w:drawing`.

### Two-column rendering

- **PDF two-column**: a single-row borderless `w:tbl` fills the page. Left cell =
  `left_col_width_twips`; right cell = remainder. Full-width paragraphs rendered above the table;
  background-colored header bands reconstructed as a full-page-width table with cell shading.
- **DOCX native two-column**: when `w:cols num=2` with unequal widths (left < 60% of right), the
  section column definition is replaced with a two-cell table; `w:cols` removed from `w:sectPr`.

### Cleanup passes

| Pass | What it does |
|---|---|
| `_strip_section_break` | Removes stale `w:sectPr` inside paragraph `w:pPr` |
| `_strip_column_break` | Removes `w:br type="column"` — column placement determined by natural flow |
| `_strip_last_rendered_page_breaks` | Removes `w:lastRenderedPageBreak` (treated as hard breaks by LibreOffice) |
| `_patch_bullet_numbering` | Replaces Symbol `` with `•`; maps Symbol/Wingdings → Calibri |
| `_fix_anchor_layout_in_cell` | Sets `layoutInCell="0"` on floating anchors in table cells |

---

## 9. Serialization

The IR round-trips through JSON for storage in `structured_resumes.template_ir_jsonb`.

**Serialized:**
- All `ParagraphProfile` fields except `body_text_x0_pt`, `inline_image_bytes`, `inline_image_size_pt`, `text_runs`
- All `ParaModel` fields except `ParaStyle.xml_proto`
- `LayoutProfile`, `ResumeSection` (including `section_id`), `RoleEntry` (including `role_id_stable`)
- `ResumeDocument.source_kind`, `label_column_fixed`

**Not serialized:**
- `ParaStyle.xml_proto` (lxml element)
- `TableBlock` and `body_items`
- Runtime fields on `ParagraphProfile` (see §2)
- `all_paras` (rebuilt from sections on deserialization)

After deserialization, `source_kind` is effectively treated as `"pdf"` regardless of original
source — `xml_proto` is always `None`, so the renderer always uses `para_builder`. This is
intentional: the template IR is stored once at upload time and reused for all generation runs.

---

## 10. Data Flow

```
DOCX template                        PDF document
parse_docx()                         parse_pdf()
↓                                    ↓
ParaStyle.xml_proto                  ParaModel.paragraph_profile
(deepcopy of w:p)                    (font/size/spacing/color)
ResumeDocument (source_kind=docx)    ResumeDocument (source_kind=pdf)
         │                                    │
         └──────── assign_stable_ids() ───────┘
                           │
                  [optional: to_dict → DB → from_dict]
                           │
             apply_tailored(original, llm_sections)
                           │
                  Section matching + role updating
                  (text replaced, style protos reused)
                           │
                  ResumeDocument (updated)
                           │
              ┌────────────┴────────────┐
              │ xml_proto present?       │
              │ (DOCX path)             │ (PDF / deserialized path)
              ↓                          ↓
      Clone xml_proto            build_para_element()
      _set_para_text()           from ParagraphProfile
              │                          │
              └────────────┬────────────┘
                           ↓
               render_docx(updated, template, output_path)
                           ↓
                    Output DOCX file
```

---

## 11. What Is Preserved vs. Lost

### Preserved

| Category | Details |
|---|---|
| DOCX paragraph formatting | Fonts, sizes, colors, spacing, numbering, styles, tabs, line breaks, hyperlinks, content controls — via `xml_proto` deepcopy |
| DOCX table structure | Borders, cell widths, shading, merge spans — via `TableBlock.xml_proto` |
| Page geometry | Dimensions, margins, column widths, default fonts |
| Two-column visual layout | Column widths, background colors, header bands |
| Bullet indentation | `indent_left` / `hanging`, numbering level |
| PDF approximate formatting | Font name (normalized), size, bold/italic, alignment, spacing, color, column assignment |

### Lost / Transformed

| Category | Details |
|---|---|
| Paragraph text | Replaced by LLM output |
| Role continuation lines (`header_extra`) | Not rendered — merged into single LLM header |
| Dropped roles | Roles removed by LLM are not rendered |
| Mixed run-level formatting | Text distributed proportionally; per-word bold/color may shift |
| Inline images in template | Not reconstructed |
| PDF custom fonts | Normalized to ~70 known equivalents; exotic fonts fall back to document default |
| PDF drawing decorations | Captured as color/style metadata; not reproduced as vector drawings |
| Column breaks | Stripped; natural flow determines column placement after tailoring |
| Stale page break markers | Stripped; always stale after content changes |
| PDF headers/footers | Filtered out during parsing; not reconstructed |

---

## 12. Known Limitations

**Parsing**

1. Semantic inference is heuristic — threshold-based (bold + spacing + word count + title-case ratio). Ambiguous paragraphs can be misclassified.
2. Role grouping assumes `header → meta → bullets` order. Resumes with company name on its own line before the title need the `_relabel_implicit_role_headers` pass; edge cases remain.
3. Two-column detection (PDF) requires a visible gap ≥ 9% of page width spanning ≥ 30% of page height. Tightly packed or three-column templates are not detected.
4. Font normalization covers ~70 fonts. Custom or embedded fonts outside this list fall back to the document default.
5. PUA bullet pair merging only fuses consecutive `[empty PUA line] + [paragraph]` pairs. Pairs separated by other content are not merged.
6. Bullet continuation merging uses `lowercase start + no sentence-end` heuristic. Capitalized continuation lines (proper nouns) split incorrectly.

**LLM Output Merging**

1. Hard fail on section mismatch — if both LLM and original have unmatched entries, the entire document is returned verbatim with no partial recovery.
2. Bullet count mismatch — extras cloned from archetype; surplus originals dropped. Important original bullets can be lost.
3. Role identity by text — if the LLM rephrases a job title, text match fails and position match is used as fallback, which can misalign roles if the LLM also reorders them.
4. Decorative paragraph detection relies on `xml_proto` mixed-font analysis; PDF paragraphs (no `xml_proto`) are never recognized as decorative and may be overwritten.

**Rendering**

1. Text distribution is proportional, not semantic — mixed bold/color runs may apply formatting to a different portion after length changes.
2. Tab stripping is universal — all residual `w:tab` elements removed, including intentional alignment tabs in headings or address lines.
3. Spec §5 enforcement is heuristic — extra experience sections blocked via `_is_experience_like()` keyword check; edge cases may slip through.
4. Skills sanitization over-fires — sentence detection uses `≥ 6 tokens + terminal punctuation`; multi-token skill descriptions ending with a period may be removed.

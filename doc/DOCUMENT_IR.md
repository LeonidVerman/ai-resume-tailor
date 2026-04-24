# Document Internal Representation (IR)

The IR is the in-memory (and optionally serialized) model of a resume document that sits
between the raw source file (DOCX or PDF) and the final rendered output.  It captures
both the **structured content** (sections, roles, bullets) and the **visual formatting**
(paragraph styles, XML prototypes, page geometry) needed to reconstruct a correctly
formatted DOCX after the LLM has rewritten the text.

---

## 1. Core Data Model

All classes are defined in `src/tailor/compiler/models.py`.

### `ParagraphProfile`

Stores formatting for paragraphs parsed from **PDF** sources.  Used when no Word XML
prototype is available.

| Field | Type | Description |
|---|---|---|
| `font_name` | `str \| None` | Normalized Windows-compatible font name (Calibri, Arial, …) |
| `font_size_pt` | `float \| None` | Point size (modal across spans) |
| `bold` | `bool` | Bold flag |
| `italic` | `bool` | Italic flag |
| `indent_left_pt` | `float` | Indentation from column origin in points |
| `hanging_indent_pt` | `float` | Hanging (first-line outdent) indent in points |
| `space_before_pt` | `float` | Estimated Y-gap to previous block |
| `space_after_pt` | `float` | Space after paragraph |
| `alignment` | `str \| None` | `left \| center \| right \| justify` |
| `text_color` | `str \| None` | Hex RRGGBB foreground color |
| `background_color` | `str \| None` | Hex RRGGBB paragraph shading fill |
| `column_id` | `str \| None` | `left \| right \| None` for two-column layouts |

**Runtime-only fields (NOT serialized):**

| Field | Description |
|---|---|
| `body_text_x0_pt` | Raw per-line x0; used when merging PUA bullet glyph+text pairs |
| `inline_image_bytes` | PNG bytes of an inline icon extracted from the PDF |
| `inline_image_size_pt` | Icon square size in points |
| `text_runs` | `[(text, bold), …]` for mixed-bold role headers |

---

### `ParaStyle`

Captures paragraph and run-level formatting from **DOCX** sources (Word XML).

| Field | Type | Description |
|---|---|---|
| `style_name` | `str \| None` | Resolved Word style name (e.g. `"Heading 1"`) |
| `alignment` | `str \| None` | `w:jc/@val` |
| `indent_left/right` | `int \| None` | Twips |
| `hanging` | `int \| None` | Hanging indent in twips |
| `spacing_before/after` | `int \| None` | Twips |
| `line_spacing` | `int \| None` | Twips (or line-height mode) |
| `keep_with_next` | `bool \| None` | Word keep-with-next flag |
| `numbering` | `dict \| None` | `{"ilvl": int, "numId": int}` for list items |
| `bold`, `italic` | `bool \| None` | Run-level overrides from first run |
| `font_name` | `str \| None` | Font from first run |
| `font_size_pt` | `float \| None` | Size from first run |
| `color` | `str \| None` | Hex color from first run |

**Runtime-only field (NOT serialized):**

| Field | Description |
|---|---|
| `xml_proto` | `deepcopy` of the original `w:p` lxml element — the entire paragraph XML including all runs, inline formatting, numbering refs, hyperlinks, and content controls |

`xml_proto` is the primary vehicle for style preservation in DOCX-sourced documents.
Cloning it during rendering replays every formatting detail exactly.

---

### `ParaModel`

The fundamental unit of the IR — one paragraph.

| Field | Type | Description |
|---|---|---|
| `text` | `str` | Plain text content |
| `style` | `ParaStyle` | Formatting metadata |
| `semantic` | `str` | `section_heading \| role_header \| role_meta \| bullet \| paragraph \| empty` |
| `paragraph_profile` | `ParagraphProfile \| None` | Set for PDF source; `None` for DOCX source |

Key methods: `with_text(new_text)` (shallow copy), `clone_as(new_text, semantic)` (deep clone),
`to_dict() / from_dict()` (serialization, excludes xml_proto and runtime fields).

---

### `RoleEntry`

One job entry in an experience section.

| Field | Type | Description |
|---|---|---|
| `header` | `ParaModel` | Role header line (e.g. `"Senior Engineer \| Acme Corp"`) |
| `header_extra` | `list[ParaModel]` | Continuation lines when the header wraps (not rendered) |
| `meta_lines` | `list[ParaModel]` | Date/location paragraphs |
| `bullets` | `list[ParaModel]` | Achievement bullet points |
| `role_id` | `str` | Normalized header text; used to match LLM roles back to originals |

---

### `ResumeSection`

One section of the resume.

| Field | Type | Description |
|---|---|---|
| `title` | `str` | Raw heading text |
| `heading` | `ParaModel` | The heading paragraph |
| `semantic_type` | `str` | `experience \| summary \| skills \| education \| certifications \| languages \| websites \| other` |
| `body_paras` | `list[ParaModel]` | Flat content for non-experience sections |
| `roles` | `list[RoleEntry]` | Structured roles for experience sections |

---

### `LayoutProfile`

Page-level geometry.

| Field | Type | Description |
|---|---|---|
| `page_width_pt`, `page_height_pt` | `float` | Page dimensions in points |
| `margin_*_pt` | `float` | Top / bottom / left / right margins |
| `default_font_name` | `str` | Fallback font (from Normal style) |
| `default_font_size_pt` | `float` | Fallback font size |
| `column_split_x` | `float \| None` | X-coordinate of two-column split (PDF only) |
| `left_col_width_twips`, `right_col_width_twips` | `int \| None` | Column widths (PDF only) |
| `left_col_bg_color`, `right_col_bg_color` | `str \| None` | Hex RRGGBB column fills (PDF only) |

---

### `TableBlock`

An opaque table preserved as XML (DOCX source only).

| Field | Type | Description |
|---|---|---|
| `xml_proto` | lxml element | `deepcopy` of original `w:tbl` — structure, borders, shading fully preserved |
| `para_models` | `list[ParaModel]` | References to the ParaModel for each `w:p` inside the table, in document order |

During rendering, the table XML is cloned and each paragraph's text is patched in-place from
its `ParaModel.text`.  If the paragraph count changes (should not happen under normal LLM output),
the unmodified clone is inserted as a fallback.

---

### `ResumeDocument`

The root of the IR tree.

| Field | Type | Description |
|---|---|---|
| `header_paras` | `list[ParaModel]` | Paragraphs before the first section heading (name, contact) |
| `sections` | `list[ResumeSection]` | Structured section list |
| `layout` | `LayoutProfile` | Page geometry |
| `all_paras` | `list[ParaModel]` | Flat ordered list mirroring document order |
| `source_kind` | `str` | `docx` or `pdf` |
| `body_items` | `list[ParaModel \| TableBlock] \| None` | Top-level render order (populated for DOCX with tables; `None` for PDF / deserialized) |

---

## 2. Building the IR from a DOCX Template

**Entry point:** `parse_docx(path: str) -> ResumeDocument` in `src/tailor/compiler/docx_parser.py`

### Steps

1. **Load and extract** — python-docx opens the file; all `w:p` and `w:tbl` elements are
   iterated from `doc.element.body` in document order.

2. **Style map** — Word style IDs resolved to friendly names via `doc.styles`.

3. **Layout extraction** — Page dimensions and margins read from the first section's `w:pgSz`
   and `w:pgMar`; default font/size from the Normal style.

4. **Per-paragraph processing** — For each `w:p`:
   - Text extracted by iterating all `w:t` elements (`w:br` elements become `\n`).
   - `ParaStyle` populated from `w:pPr` (paragraph properties) and the first `w:r` run.
   - `xml_proto = deepcopy(w:p)` — the entire original XML is captured.
   - Semantic type inferred (see §2.1 below).

5. **Table handling** — For each `w:tbl`, all nested `w:p` elements are parsed as `ParaModel`
   objects and the table is wrapped in a `TableBlock(xml_proto=deepcopy(w:tbl))`.

6. **Section grouping** — Paragraphs before the first `section_heading` become `header_paras`.
   Each `section_heading` starts a new `ResumeSection`.  Two heuristics handle non-standard
   templates:
   - **Approach A**: bold-heading paragraphs inside an experience section that don't match
     known section names are kept as body content rather than creating a new section.
   - **Approach B**: consecutive `other`-typed sections that look like standalone job entries
     are consolidated into a single synthetic experience section.

7. **Role grouping** — Experience sections have their `body_paras` split into `RoleEntry`
   objects by `_group_roles()`: a state machine transitions through
   `init → header → meta → bullets` on each paragraph's semantic type.

### Semantic Inference Rules

| Semantic | Conditions |
|---|---|
| `section_heading` | Matches a `"Heading N"` Word style; OR title-case bold ≥2 words with specific spacing/size thresholds; OR matches known heading name list |
| `role_header` | Contains ` \| ` pipe separator |
| `role_meta` | Contains a 4-digit year, ≤80 chars, no pipe (dates/location lines) |
| `bullet` | Has Word numbering properties; starts with `List` style; or starts with a bullet character (`-`, `•`, `·`, `–`, …) |
| `paragraph` | Any other non-empty text |
| `empty` | No text or whitespace only |

---

## 3. Building the IR from a PDF

**Entry point:** `parse_pdf(pdf_bytes: bytes) -> ResumeDocument` in `src/tailor/compiler/pdf_parser.py`

PDF documents have no XML paragraph prototypes, so the IR is built from visual properties
extracted by PyMuPDF (fitz).

### Steps

1. **Scanned document guard** — Fewer than 50 total characters → `RuntimeError` (scanned image,
   no usable text).

2. **Header/footer fingerprinting** — Four complementary strategies suppress repeating elements:
   - Exact text appearing on ≥ half the pages.
   - Position bucket match (same Y ±2% across multiple pages).
   - Absolute zone exclusion on pages 2+ (top/bottom 8%).
   - Running header repetition (top 25% of pages 2+ matching page 1's top 20%).

3. **Two-column detection** — `_detect_column_split()` scans the set of distinct block x0
   positions for a gap ≥ 9% of page width that spans ≥ 30% of page height.  Full-width blocks
   do not veto the split.  When detected, paragraphs are reordered to left-column reading order
   followed by right-column reading order.

4. **Block extraction** — PyMuPDF raw blocks are coalesced into logical paragraphs via Y-gap
   thresholds and same-row fragment merging (font switches mid-line within 20 pt).

5. **`ParagraphProfile` construction** — For each paragraph:
   - Font name, size (modal), bold/italic flags read from span metadata.
   - `indent_left_pt` = block x0 minus minimum content x0 on that page.
   - `space_before_pt` = Y-gap to previous block.
   - `column_id` assigned based on position relative to `column_split_x`.
   - Mixed-bold role headers: per-run `(text, bold)` pairs stored in `text_runs`.

6. **Bullet detection** — Two mechanisms:
   - *PUA bullet pairs*: a line containing only a Private Use Area glyph (U+E000–U+F8FF)
     immediately before a text line is merged into a single bullet with hanging-indent geometry.
   - *Bullet dot shapes*: small filled circles/squares (3–5 pt) detected as vector drawings;
     the adjacent text line is promoted to `bullet` semantic.
   - *Bullet continuation merging*: PDF line-wraps are re-joined when the previous line doesn't
     end with sentence-final punctuation and the current line starts lowercase.

7. **Icon extraction** — Small vector drawings (6–20 pt) in the left column are rasterized to
   PNG at 3× scale and stored as `inline_image_bytes` on the adjacent paragraph.

8. **Font normalization** — ~70 custom/subset fonts mapped to standard Windows equivalents
   (Calibri, Arial, Times New Roman, …).  6-character subset prefixes (e.g. `ABCDEF+FontName`)
   stripped.  Unknown fonts fall back to document default.

9. **Section and role grouping** — Same logic as DOCX (§2, steps 6–7), adapted for
   bold/size-based semantic inference instead of Word style names.

---

## 4. LLM Output Merging (`apply_tailored`)

**Entry point:** `apply_tailored(original: ResumeDocument, llm_sections: list[LlmSection]) -> ResumeDocument`
in `src/tailor/compiler/updater.py`

### LLM Output Format

The LLM emits plain text parsed by `src/tailor/compiler/text_parser.py`:
- Section headings (bare line matching known names).
- Role headers with ` | ` separator.
- Date/location lines (contain 4-digit year; no pipe).
- Bullet lines prefixed with `- ` (or `•`, `–`, `●`).
- Plain body lines for summary and skills sections.

### Section Matching

1. **Pass 1**: exact heading match (case-insensitive).
2. **Pass 2**: semantic type match when headings differ.
3. **Hard fail**: if any LLM section is unmatched **and** any original content section is also
   unmatched → return `original` verbatim (spec §9).

**Locked sections** — never modified regardless of LLM output:

```
education, certifications, languages, websites
```

Only `summary`, `experience` (bullets only), and `skills` are editable.

### Updating Roles (experience sections)

- **Pipe-separated roles**: matched by position to originals.  Header and meta-line XML protos
  reused; bullet protos reused for existing bullets, cloned from an archetype (first bullet or
  header) for extras.  Surplus originals dropped.
- **Dash-format path**: when the LLM writes `"Title — Company"` instead of `"Title | Company"`,
  only bullets are updated; headers and meta lines kept verbatim from the template.

### Updating Body Sections (summary, skills, other)

- Decorative paragraphs (mixed run fonts, no alphanumerics) detected and preserved verbatim.
- Content paragraphs paired by position with LLM lines; archetype cloned for extras.
- Empty/spacer paragraphs preserved for visual spacing.

### Skills Sanitization

Lines removed automatically:
- Date markers (`CURRENT_DATE`, `"Generated on"`, etc.).
- Lines starting with `"Additional"`.
- Full prose sentences (≥ 6 tokens ending with `.`, `!`, or `?`).

### Extra Sections (LLM adds sections not in template)

- **Extra experience sections**: never created (spec §5).
- **Extra skills in a header column**: injected into `header_paras` rather than a new section.
- **Extra summary**: placed before existing sections in the right column.
- **Other extras**: inserted at LLM output position with styles cloned from the nearest existing
  section; forced to left-alignment.

---

## 5. Rendering Back to DOCX

**Entry point:** `render_docx(doc: ResumeDocument, template_path: str, output_path: str) -> None`
in `src/tailor/compiler/docx_renderer.py`

### DOCX-sourced documents (xml_proto path)

1. Template DOCX copied to output (preserves styles, page setup, numbering definitions,
   headers, footers).
2. All `w:p` and `w:tbl` children stripped from body (final `w:sectPr` retained).
3. For each item in `body_items`:
   - `ParaModel` → clone `xml_proto`, call `_set_para_text()`, insert before `w:sectPr`.
   - `TableBlock` → clone `xml_proto`, patch each nested `w:p` text from `para_models`,
     insert before `w:sectPr`.

**Text distribution in `_set_para_text()`:**

- Whitespace-only runs (NBSP, soft hyphen, zero-width chars, …) restored to original text.
- Content runs: new text distributed proportionally across runs based on original character
  lengths (`new_text.length × (cum_orig_len / total_orig_len)` per run).
- Residual `w:tab` elements stripped after distribution (alignment tabs become mid-word
  characters after content length changes).
- VML text boxes (`w:pict` with nested `w:t`) left untouched.

### PDF-sourced documents (para_builder path)

When `xml_proto` is `None`, `build_para_element()` in `src/tailor/compiler/para_builder.py`
constructs a fresh `w:p` lxml element from `ParagraphProfile`:

- Paragraph properties (`w:pPr`): style, alignment, indentation, spacing, shading.
- PUA-style bullets: three-run structure — Symbol glyph run + tab + body text run — recreating
  the original PDF bullet structure.
- Regular bullets: `ListParagraph` style with inline `"• "` prefix.
- Line spacing set to exact (pins layout across LibreOffice and Word).
- Inline images appended as `w:drawing` via `doc_part.new_pic_inline`.

### Two-column rendering

- **PDF two-column**: a single-row borderless `w:tbl` with negative `tblInd` fills the page.
  Left cell = `left_col_width_twips`; right cell = remainder.  Full-width above-table
  paragraphs rendered before the table; background-colored header bands reconstructed as a
  single-cell full-page-width table with cell shading.
- **DOCX native two-column**: when `w:cols num=2` has unequal widths (left < 60% of right),
  the section column definition is replaced with a two-cell table; the `w:cols` is removed from
  `w:sectPr`.

### Cleanup passes

| Pass | What it does |
|---|---|
| `_strip_section_break` | Removes stale `w:sectPr` inside paragraph `w:pPr` (except header-section boundaries) |
| `_strip_column_break` | Removes `w:br type="column"` — column placement determined by natural flow |
| `_strip_last_rendered_page_breaks` | Removes `w:lastRenderedPageBreak` — stale after content changes; LibreOffice treats them as hard breaks |
| `_patch_bullet_numbering` | Replaces Symbol `\uf0b7` glyphs with Unicode `•`; maps Symbol/Wingdings → Calibri (LibreOffice compatibility) |
| `_fix_anchor_layout_in_cell` | Sets `layoutInCell="0"` on floating anchors in table cells to prevent position shifts |

---

## 6. Serialization

The IR can be round-tripped through JSON for storage in the `structured_resumes.template_ir_jsonb`
database column.

**What serializes:**
- All `ParagraphProfile` fields except `body_text_x0_pt`, `inline_image_bytes`, `text_runs`.
- All `ParaModel` fields except `ParaStyle.xml_proto`.
- `LayoutProfile`, `ResumeSection`, `RoleEntry`, `ResumeDocument` metadata.

**What does NOT serialize:**
- `ParaStyle.xml_proto` (lxml element — not JSON-serializable).
- `TableBlock` (opaque XML — not serialized).
- Runtime fields on `ParagraphProfile` (see §1).
- `ResumeDocument.body_items` (always `None` after deserialization).

**Consequence:** After deserializing from the DB, `source_kind` is treated as `pdf` regardless
of original source — the rendering path always uses `para_builder` (no xml_proto available).
This is acceptable for the resume generation flow because the template IR was already parsed from
the original uploaded file; only a re-upload re-builds the DOCX xml_proto path.

---

## 7. What Is Preserved vs. Lost

### Preserved

| Category | Details |
|---|---|
| DOCX paragraph formatting | Fonts, sizes, colors, spacing, numbering, styles, tabs, line breaks, hyperlinks, content controls — via xml_proto deepcopy |
| DOCX table structure | Borders, cell widths, shading, merge spans — via TableBlock xml_proto |
| Page geometry | Dimensions, margins, column widths, default fonts |
| Two-column visual layout | Column widths, background colors, header bands |
| Bullet indentation | left/hanging indent, numbering level |
| PDF approximate formatting | Font name (normalized), size, bold/italic, alignment, spacing, color, column assignment |

### Lost / Transformed

| Category | Details |
|---|---|
| Paragraph text | Replaced by LLM output |
| Role continuation lines (`header_extra`) | Not rendered — merged into single LLM header |
| Dropped roles | Roles removed by LLM are not rendered |
| Mixed run-level formatting | New text distributed proportionally; per-word bold/color may shift |
| Inline images in template | Not reconstructed (no LLM output for images) |
| PDF custom fonts | Normalized to ~70 known Windows equivalents; truly exotic fonts fall back to document default |
| PDF drawing decorations | Captured as color/style metadata; not reproduced as vector drawings |
| Column breaks | Stripped; natural flow determines column placement after tailoring |
| Stale page break markers | Stripped; always stale after content changes |
| PDF headers/footers | Filtered out during parsing; not reconstructed |

---

## 8. Known Limitations

### Parsing

1. **Semantic inference is heuristic** — threshold-based (bold + spacing + word count + title-case
   ratio).  Ambiguous paragraphs (e.g. bold short lines that aren't headings) can be
   misclassified.

2. **Role grouping assumes clear progression** — `header → meta → bullets` state machine breaks
   on resumes where roles don't follow this structure (e.g. company name on its own line before
   the title).

3. **Two-column detection (PDF) is gap-based** — requires a visible white gap ≥ 9% of page width
   spanning ≥ 30% page height.  Tightly packed two-column layouts or three-column templates will
   not be detected correctly.

4. **Font normalization covers ~70 fonts** — custom or embedded fonts outside this list fall
   back to the document default.  The visual output may use the wrong typeface.

5. **PUA bullet pair merging** — only fuses consecutive `[empty PUA line] + [paragraph]` pairs.
   If separated by other content, the merge does not happen.

6. **Bullet continuation merging** — detects line-wraps only by `lowercase start + no sentence-end`
   heuristic.  Legitimately capitalized continuation lines (proper nouns) will split incorrectly.

7. **Header/footer detection** — 4-strategy heuristic; complex repeating decorative patterns or
   section titles that repeat across pages may bleed into content.

### LLM Output Merging

1. **Hard fail on section mismatch** — if the LLM significantly restructures sections and both
   LLM and original have unmatched entries, the entire document is returned verbatim with no
   partial recovery.

2. **Bullet count mismatch** — if the LLM writes more or fewer bullets than the template has
   paragraphs for, extras are cloned from an archetype or originals are dropped.  Important
   original bullets can be lost.

3. **Role identity by text** — `role_id` is the normalized header text.  If the LLM rephrases
   a job title, the match fails on a second pass and falls back to positional matching.

4. **Decorative paragraph detection (PDF)** — relies on mixed-font analysis of xml_proto; PDF
   paragraphs (no xml_proto) are never recognized as decorative and may be overwritten.

5. **Dash-format path limited** — only recognizes em-dash (`—`), en-dash (`–`), and figure-dash
   as separators.  Other dash variants are treated as regular body lines.

### Rendering

1. **Text distribution is proportional, not semantic** — if original runs have mixed bold/color
   (e.g. bold first word), the new text may apply the bold to a different portion after length
   changes.

2. **Tab stripping is universal** — all residual `w:tab` elements are removed after text
   distribution, including intentional alignment tabs in headings or address lines.

3. **Boundary paragraph line-height hack** — the section-break boundary paragraph is forced to
   1 pt exact line height.  If headers/footers reference this paragraph position, it may shift.

4. **Bottom margin clamping (PDF)** — for single-page PDFs, bottom margin is clamped to the top
   margin value (avoids negative margins from blank-space mis-measurement).

5. **Spec §5 enforcement is heuristic** — extra experience sections are blocked via
   `_is_experience_like()` which checks semantic_type and heading keywords.  Edge cases may
   slip through.

6. **Spec §6 skills sanitization over-fires** — sentence detection uses `≥ 6 tokens + terminal
   punctuation`.  Multi-token skill descriptions ending with a period may be incorrectly removed.

---

## 9. Data Flow Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  DOCX template                      PDF document                    │
│  parse_docx()                        parse_pdf()                    │
│  ↓                                   ↓                              │
│  ParaModel.style.xml_proto           ParaModel.paragraph_profile    │
│  (deepcopy of w:p)                   (font/size/spacing/color)      │
│  ResumeDocument (source_kind=docx)   ResumeDocument (source_kind=pdf)│
└──────────────────────────┬──────────────────────┬───────────────────┘
                           │                      │
                           │  [optional: to_dict → DB → from_dict]    
                           │                      │
                     apply_tailored(original, llm_sections)
                           │
                    Section matching + role updating
                    (text replaced, style protos reused)
                           │
                     ResumeDocument (updated)
                           │
              ┌────────────┴────────────┐
              │ xml_proto present?      │
              │ (DOCX path)             │ (PDF path)
              ↓                         ↓
      Clone xml_proto            build_para_element()
      _set_para_text()           from ParagraphProfile
              │                         │
              └────────────┬────────────┘
                           ↓
                   render_docx(updated, template, output_path)
                           ↓
                    Output DOCX file
```

"""Intermediate representation (IR) for a parsed resume document.

The IR is format-agnostic: it captures the content and structure of a resume
along with per-paragraph style metadata.  A separate renderer turns it back
into a DOCX.  The xml_proto field on ParaStyle is a deepcopy of the original
w:p element; cloning it during rendering preserves formatting exactly.

For PDF-sourced documents, xml_proto is always None; instead each ParaModel
carries a ParagraphProfile used by para_builder to construct DOCX paragraphs
programmatically.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PageImageBlock:
    """A raster image extracted from a PDF page, with page-relative position.

    All coordinates are in PDF points (72 pt per inch), measured from the
    page top-left corner (y increases downward).  Not serialised to JSON —
    runtime-only, like ``ParagraphProfile.inline_image_bytes``.
    """

    image_bytes: bytes   # raw PNG bytes
    x_pt: float          # left edge from page left
    y_pt: float          # top edge from page top
    width_pt: float      # display width on page (points)
    height_pt: float     # display height on page (points)
    # 'profile_photo' | 'header_footer_decor' | 'body_decor'
    category: str = "body_decor"
    page_index: int = 0


@dataclass
class ParagraphProfile:
    """Formatting profile for PDF-sourced paragraphs.

    Used when xml_proto is not available.  The renderer (para_builder) uses
    these fields to construct a w:p element via lxml directly.
    """

    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    indent_left_pt: float = 0.0   # points from the column's left edge
    hanging_indent_pt: float = 0.0  # hanging-indent (first-line outdent); used for PUA bullet pairs
    body_text_x0_pt: float = 0.0  # raw per-line x0 (runtime only; not serialised); used by _merge_pua_bullet_pairs
    space_before_pt: float = 0.0  # estimated from Y-gap to previous block
    space_after_pt: float = 0.0
    alignment: str | None = None  # 'left' | 'center' | 'right' | 'justify'
    text_color: str | None = None        # hex RRGGBB, no '#' (e.g. 'ffffff')
    background_color: str | None = None  # hex RRGGBB paragraph shading fill
    column_id: str | None = None         # 'left' | 'right' | None
    # Inline icon image prepended to the paragraph (PDF vector drawings rendered
    # to PNG).  Not serialised — runtime-only; ignored if None.
    inline_image_bytes: bytes | None = None  # raw PNG bytes
    inline_image_size_pt: float = 0.0        # icon square size in pt
    # Mixed-bold run list: list[tuple[str, bool]] — (text, bold) per run.
    # Set by pdf_parser for role headers with non-uniform bold across spans.
    # Not serialised to JSON (runtime-only, like inline_image_bytes).
    text_runs: list | None = None
    # Absolute Y position of the block top in PDF points (runtime-only; not serialised).
    # Set by _extract_paragraphs for section-label-column pairing in parse_pdf.
    y_top_pt: float = 0.0

    def to_dict(self) -> dict:
        return {
            "font_name": self.font_name,
            "font_size_pt": self.font_size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "indent_left_pt": self.indent_left_pt,
            "hanging_indent_pt": self.hanging_indent_pt,
            "space_before_pt": self.space_before_pt,
            "space_after_pt": self.space_after_pt,
            "alignment": self.alignment,
            "text_color": self.text_color,
            "background_color": self.background_color,
            "column_id": self.column_id,
            # inline_image_bytes and body_text_x0_pt are NOT serialised (runtime-only)
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParagraphProfile":
        return cls(
            font_name=d.get("font_name"),
            font_size_pt=d.get("font_size_pt"),
            bold=bool(d.get("bold", False)),
            italic=bool(d.get("italic", False)),
            indent_left_pt=float(d.get("indent_left_pt", 0.0)),
            hanging_indent_pt=float(d.get("hanging_indent_pt", 0.0)),
            space_before_pt=float(d.get("space_before_pt", 0.0)),
            space_after_pt=float(d.get("space_after_pt", 0.0)),
            alignment=d.get("alignment"),
            text_color=d.get("text_color"),
            background_color=d.get("background_color"),
            column_id=d.get("column_id"),
        )


@dataclass
class ParaStyle:
    """Formatting metadata for one paragraph.

    xml_proto is a deepcopy of the original lxml w:p element.  The renderer
    clones it and overwrites the text, so all paragraph and run formatting
    (font, indentation, spacing, numbering, colour …) is preserved verbatim.
    """

    style_name: str | None = None
    alignment: str | None = None
    indent_left: int | None = None       # twips
    indent_right: int | None = None
    hanging: int | None = None
    spacing_before: int | None = None    # twips
    spacing_after: int | None = None
    line_spacing: int | None = None
    keep_with_next: bool | None = None
    numbering: dict | None = None        # {"ilvl": int, "numId": int}
    bold: bool | None = None
    italic: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    color: str | None = None
    xml_proto: Any = None                # deepcopy of original w:p; not serialised

    def clone_proto(self) -> Any:
        """Return a fresh deepcopy of the XML prototype, or None."""
        return deepcopy(self.xml_proto) if self.xml_proto is not None else None


@dataclass
class ParaModel:
    """One paragraph: content, style, and semantic role."""

    text: str
    style: ParaStyle
    # Semantic values: section_heading | role_header | role_meta | bullet | paragraph | empty
    semantic: str
    # Set for PDF-sourced paragraphs; None for DOCX-sourced paragraphs.
    paragraph_profile: ParagraphProfile | None = None
    # Stable synthetic ID assigned by assign_stable_ids(); "" until assigned.
    para_id: str = ""

    def with_text(self, new_text: str) -> "ParaModel":
        """Return a copy sharing this paragraph's style but with different text.

        para_id is preserved so the layout_blocks renderer can match the updated
        paragraph back to its original XML prototype by stable ID.
        """
        p = ParaModel(
            text=new_text,
            style=self.style,
            semantic=self.semantic,
            paragraph_profile=self.paragraph_profile,
        )
        p.para_id = self.para_id
        # Preserve runtime-only y_top_pt so the PDF two-column Y-sort can place
        # updated paragraphs at their original template positions.
        if p.paragraph_profile is not None and self.paragraph_profile is not None:
            p.paragraph_profile.y_top_pt = self.paragraph_profile.y_top_pt
        return p

    def clone_as(self, new_text: str, semantic: str | None = None) -> "ParaModel":
        """Return a new ParaModel with a deep-copied style proto and new text.

        Use this when creating paragraphs that did not exist in the original
        (e.g. extra bullet points inserted by the LLM).
        """
        cloned = ParaStyle(
            style_name=self.style.style_name,
            alignment=self.style.alignment,
            indent_left=self.style.indent_left,
            indent_right=self.style.indent_right,
            hanging=self.style.hanging,
            spacing_before=self.style.spacing_before,
            spacing_after=self.style.spacing_after,
            line_spacing=self.style.line_spacing,
            keep_with_next=self.style.keep_with_next,
            numbering=self.style.numbering,
            bold=self.style.bold,
            italic=self.style.italic,
            font_name=self.style.font_name,
            font_size_pt=self.style.font_size_pt,
            color=self.style.color,
            xml_proto=self.style.clone_proto(),
        )
        pp_clone = (
            ParagraphProfile.from_dict(self.paragraph_profile.to_dict())
            if self.paragraph_profile is not None
            else None
        )
        # Preserve runtime-only y_top_pt so the PDF two-column Y-sort places
        # cloned paragraphs at their archetype's original template position.
        if pp_clone is not None and self.paragraph_profile is not None:
            pp_clone.y_top_pt = self.paragraph_profile.y_top_pt
        return ParaModel(
            text=new_text,
            style=cloned,
            semantic=semantic or self.semantic,
            paragraph_profile=pp_clone,
        )

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "semantic": self.semantic,
            "paragraph_profile": self.paragraph_profile.to_dict() if self.paragraph_profile else None,
            "para_id": self.para_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParaModel":
        pp_data = d.get("paragraph_profile")
        pp = ParagraphProfile.from_dict(pp_data) if pp_data else None
        return cls(
            text=d["text"],
            style=ParaStyle(),
            semantic=d["semantic"],
            paragraph_profile=pp,
            para_id=d.get("para_id", ""),
        )


@dataclass
class RoleEntry:
    """One experience role: header + optional date/location lines + bullets.

    header_extra holds any continuation paragraphs that Word wraps onto a
    second line for a long role header (e.g. "Engineer | Corp, St." + "Louis").
    They are not rendered in the output — the LLM produces a single merged
    header string — but they must be tracked so the state machine does not
    mis-classify them as bullets.
    """

    header: ParaModel
    header_extra: list[ParaModel] = field(default_factory=list)
    meta_lines: list[ParaModel] = field(default_factory=list)
    bullets: list[ParaModel] = field(default_factory=list)
    role_id: str = ""         # normalised header text; used as anchor for updater matching
    role_id_stable: str = ""  # synthetic positional ID assigned by assign_stable_ids()

    def to_dict(self) -> dict:
        return {
            "header": self.header.to_dict(),
            "header_extra": [p.to_dict() for p in self.header_extra],
            "meta_lines": [p.to_dict() for p in self.meta_lines],
            "bullets": [p.to_dict() for p in self.bullets],
            "role_id": self.role_id,
            "role_id_stable": self.role_id_stable,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoleEntry":
        return cls(
            header=ParaModel.from_dict(d["header"]),
            header_extra=[ParaModel.from_dict(p) for p in d.get("header_extra", [])],
            meta_lines=[ParaModel.from_dict(p) for p in d.get("meta_lines", [])],
            bullets=[ParaModel.from_dict(p) for p in d.get("bullets", [])],
            role_id=d.get("role_id", ""),
            role_id_stable=d.get("role_id_stable", ""),
        )


@dataclass
class ResumeSection:
    """One section of the resume (e.g. Experience, Technical Skills)."""

    title: str          # raw heading text from document
    heading: ParaModel
    semantic_type: str  # experience | summary | skills | education | other
    body_paras: list[ParaModel] = field(default_factory=list)   # non-experience sections
    roles: list[RoleEntry] = field(default_factory=list)        # experience sections only
    # Stable synthetic ID assigned by assign_stable_ids(); "" until assigned.
    section_id: str = ""
    # Container semantics assigned by infer_container_semantics().
    # Values: "independent_vertical_stack" | "synchronized_row" |
    #         "local_column_pair" | "paired_sidebar_region" |
    #         "rewriteable_region" | "preserve_region" | None
    container_type: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "semantic_type": self.semantic_type,
            "heading": self.heading.to_dict(),
            "body_paras": [p.to_dict() for p in self.body_paras],
            "roles": [r.to_dict() for r in self.roles],
            "section_id": self.section_id,
            "container_type": self.container_type,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeSection":
        return cls(
            title=d["title"],
            heading=ParaModel.from_dict(d["heading"]),
            semantic_type=d["semantic_type"],
            body_paras=[ParaModel.from_dict(p) for p in d.get("body_paras", [])],
            roles=[RoleEntry.from_dict(r) for r in d.get("roles", [])],
            section_id=d.get("section_id", ""),
            container_type=d.get("container_type"),
        )


@dataclass
class LayoutProfile:
    """Page-level layout extracted from the source document."""

    page_width_pt: float
    page_height_pt: float
    margin_top_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    margin_right_pt: float
    default_font_name: str
    default_font_size_pt: float
    # Two-column layout (PDF-sourced only; None for single-column or DOCX sources)
    column_split_x: float | None = None      # x-coordinate of column split (PDF pts)
    left_col_width_twips: int | None = None  # left column width in twips
    right_col_width_twips: int | None = None # right column width in twips
    left_col_bg_color: str | None = None     # hex RRGGBB fill for left column
    right_col_bg_color: str | None = None    # hex RRGGBB fill for right column
    section_row_table: bool = False          # True when left column is section-label only (one row per section)
    header_bg_color: str | None = None       # hex RRGGBB for full-width dark header band (single-col PDFs)
    footer_bg_color: str | None = None       # hex RRGGBB for full-width dark footer band (single-col PDFs)
    # Semantic table layout mode (B1): inferred from structural cues; None for non-two-column docs.
    # Values: "synchronized_rows" | "sidebar_layout" | "header_body_split" |
    #         "asymmetric_columns" | "independent_columns"
    table_layout_mode: str | None = None

    def to_dict(self) -> dict:
        return {
            "page_width_pt": self.page_width_pt,
            "page_height_pt": self.page_height_pt,
            "margin_top_pt": self.margin_top_pt,
            "margin_bottom_pt": self.margin_bottom_pt,
            "margin_left_pt": self.margin_left_pt,
            "margin_right_pt": self.margin_right_pt,
            "default_font_name": self.default_font_name,
            "default_font_size_pt": self.default_font_size_pt,
            "column_split_x": self.column_split_x,
            "left_col_width_twips": self.left_col_width_twips,
            "right_col_width_twips": self.right_col_width_twips,
            "left_col_bg_color": self.left_col_bg_color,
            "right_col_bg_color": self.right_col_bg_color,
            "section_row_table": self.section_row_table,
            "header_bg_color": self.header_bg_color,
            "footer_bg_color": self.footer_bg_color,
            "table_layout_mode": self.table_layout_mode,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutProfile":
        return cls(
            page_width_pt=d["page_width_pt"],
            page_height_pt=d["page_height_pt"],
            margin_top_pt=d["margin_top_pt"],
            margin_bottom_pt=d["margin_bottom_pt"],
            margin_left_pt=d["margin_left_pt"],
            margin_right_pt=d["margin_right_pt"],
            default_font_name=d["default_font_name"],
            default_font_size_pt=d["default_font_size_pt"],
            column_split_x=d.get("column_split_x"),
            left_col_width_twips=d.get("left_col_width_twips"),
            right_col_width_twips=d.get("right_col_width_twips"),
            left_col_bg_color=d.get("left_col_bg_color"),
            right_col_bg_color=d.get("right_col_bg_color"),
            section_row_table=bool(d.get("section_row_table", False)),
            header_bg_color=d.get("header_bg_color"),
            footer_bg_color=d.get("footer_bg_color"),
            table_layout_mode=d.get("table_layout_mode"),
        )


@dataclass
class TableBlock:
    """An opaque w:tbl element preserved for layout-faithful rendering.

    The xml_proto is a deepcopy of the original w:tbl lxml element.  The
    renderer clones it, finds all w:p elements in document order, and updates
    each paragraph's text from the corresponding ParaModel in para_models.

    para_models holds direct references to the ParaModel for every paragraph
    extracted from this table, in the same order as the w:p elements inside
    xml_proto.  apply_tailored remaps these references to the updated versions
    so the renderer always writes the latest text.
    """

    xml_proto: Any                      # deepcopy of the original w:tbl element
    para_models: list[Any]              # list[ParaModel], same order as w:p in xml_proto


@dataclass
class LayoutParagraphBlock:
    """Serializable layout node for a single top-level body paragraph.

    Stores the original ``w:p`` XML as a Unicode string so the renderer can
    faithfully reconstruct paragraph formatting (fonts, styles, spacing, column
    breaks, embedded sectPr) after deserialization — without requiring the
    original DOCX template file.

    para_id matches the stable ID assigned by assign_stable_ids().  A value of
    ``""`` means the paragraph is structural/orphan (e.g. a column-break
    paragraph that was split by the multicolumn fix); the renderer inserts it
    verbatim without text substitution.
    """

    para_id: str
    xml_proto_xml: str | None = None

    def to_dict(self) -> dict:
        d: dict = {"kind": "paragraph", "para_id": self.para_id}
        if self.xml_proto_xml is not None:
            d["xml_proto_xml"] = self.xml_proto_xml
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutParagraphBlock":
        return cls(para_id=d["para_id"], xml_proto_xml=d.get("xml_proto_xml"))


@dataclass
class LayoutTableBlock:
    """Serializable layout node for a top-level table.

    Stores the original ``w:tbl`` XML as a Unicode string.  ``para_ids`` is an
    ordered list of stable paragraph IDs corresponding to every ``w:p`` inside
    the table (in document order).  The renderer deserializes the XML, iterates
    the ``w:p`` elements, looks up each paragraph's updated text by ``para_id``,
    and writes it into the cloned XML before inserting the table.
    """

    table_id: str
    xml_proto_xml: str
    para_ids: list[str]

    def to_dict(self) -> dict:
        return {
            "kind": "table",
            "table_id": self.table_id,
            "xml_proto_xml": self.xml_proto_xml,
            "para_ids": self.para_ids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutTableBlock":
        return cls(
            table_id=d["table_id"],
            xml_proto_xml=d["xml_proto_xml"],
            para_ids=d.get("para_ids", []),
        )


@dataclass
class ResumeDocument:
    """Full parsed resume.

    header_paras  – paragraphs before the first section heading (name, contact).
    sections      – structured section list.
    layout        – document-level page geometry.
    all_paras     – flat ordered list mirroring doc.paragraphs; used for plain-text
                    serialisation and to drive the renderer in document order.
    body_items    – top-level rendering order: each item is either a ParaModel
                    (direct body paragraph) or a TableBlock (table preserved as
                    an opaque XML blob with per-paragraph text updates).
                    None when loaded from a serialised dict (PDF path).
    """

    header_paras: list[ParaModel]
    sections: list[ResumeSection]
    layout: LayoutProfile
    all_paras: list[ParaModel]
    # 'docx' for DOCX-sourced (xml_proto available); 'pdf' for PDF-sourced (para_builder path).
    source_kind: str = "docx"
    # Footer paragraphs (contact strip, icons) preserved from the original PDF
    # template and rendered as a dark band at the bottom.  Empty for most templates.
    footer_paras: list[ParaModel] = field(default_factory=list)
    # Raster images extracted from the source PDF (profile photos, decorative
    # headers/footers, etc.).  Runtime-only — not serialised to JSON.
    page_images: list["PageImageBlock"] = field(default_factory=list)
    # Diagnostics populated by parse_pdf() — runtime-only, not serialised.
    # Keys: raster_images_raw, vector_images_raw, page_images_final, image_categories,
    #       column_split_x, table_layout_mode, header_bg_color, left_col_bg_color,
    #       sidebar_detected, sidebar_inferred, page_bg_detected.
    pdf_diagnostics: "dict | None" = field(default=None, repr=False)
    body_items: list[Any] | None = None  # list[ParaModel | TableBlock]; None for PDF/deserialised
    label_column_fixed: bool = False     # True when label-column layout reordering was applied
    table_column_layout_fixed: bool = False  # True when newspaper/table multi-column fix applied
    # Serializable layout tree (Option B: XML prototypes).  Built by parse_docx
    # when USE_SERIALIZED_LAYOUT_TREE is True; None for PDF sources or when the
    # flag is off.  Carries the original w:p / w:tbl XML strings so the renderer
    # can faithfully reconstruct DOCX layout after DB round-trip without relying
    # on the runtime xml_proto / body_items fields (which are not serialised).
    layout_blocks: list[Any] | None = None  # list[LayoutParagraphBlock | LayoutTableBlock]

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict.  xml_proto is not included."""
        d: dict = {
            "source_kind": self.source_kind,
            "header_paras": [p.to_dict() for p in self.header_paras],
            "sections": [s.to_dict() for s in self.sections],
            "layout": self.layout.to_dict(),
        }
        if self.label_column_fixed:
            d["label_column_fixed"] = True
        if self.table_column_layout_fixed:
            d["table_column_layout_fixed"] = True
        if self.layout_blocks is not None:
            d["layout_blocks"] = [b.to_dict() for b in self.layout_blocks]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeDocument":
        """Reconstruct a ResumeDocument from a serialized dict.

        all_paras is rebuilt from header_paras + sections in document order.
        xml_proto fields are always None; when layout_blocks is present the
        renderer uses it to restore XML-fidelity formatting instead of para_builder.
        """
        header_paras = [ParaModel.from_dict(p) for p in d.get("header_paras", [])]
        sections = [ResumeSection.from_dict(s) for s in d.get("sections", [])]
        layout = LayoutProfile.from_dict(d["layout"])

        all_paras: list[ParaModel] = list(header_paras)
        for section in sections:
            all_paras.append(section.heading)
            if section.semantic_type == "experience" and section.roles:
                for role in section.roles:
                    all_paras.append(role.header)
                    all_paras.extend(role.meta_lines)
                    all_paras.extend(role.bullets)
            else:
                all_paras.extend(section.body_paras)

        layout_blocks = None
        lb_data = d.get("layout_blocks")
        if lb_data is not None:
            layout_blocks = [
                LayoutTableBlock.from_dict(b) if b.get("kind") == "table"
                else LayoutParagraphBlock.from_dict(b)
                for b in lb_data
            ]

        return cls(
            header_paras=header_paras,
            sections=sections,
            layout=layout,
            all_paras=all_paras,
            source_kind=d.get("source_kind", "pdf"),
            label_column_fixed=bool(d.get("label_column_fixed", False)),
            table_column_layout_fixed=bool(d.get("table_column_layout_fixed", False)),
            layout_blocks=layout_blocks,
        )


# ── Stable ID assignment ───────────────────────────────────────────────────

def assign_stable_ids(doc: "ResumeDocument") -> None:
    """Assign synthetic sequential IDs to every section, role, and paragraph.

    IDs are positional (assigned in document order) and stable for the same
    parsed document.  They are stored on the IR objects and round-trip through
    to_dict() / from_dict() so classification input built from a deserialized
    IR carries the same IDs as the original parse.

    Calling this function a second time on the same document re-assigns IDs
    from scratch (idempotent but not additive).

    ID formats:
        sections  → "sec_1", "sec_2", …
        roles     → "role_1", "role_2", … (globally sequential)
        paras     → "para_1", "para_2", … (globally sequential across doc)
    """
    para_counter = 0
    role_counter = 0

    def _assign_para(pm: "ParaModel") -> None:
        nonlocal para_counter
        para_counter += 1
        pm.para_id = f"para_{para_counter}"

    for pm in doc.header_paras:
        _assign_para(pm)

    for sec_idx, section in enumerate(doc.sections, start=1):
        section.section_id = f"sec_{sec_idx}"
        _assign_para(section.heading)
        for role in section.roles:
            role_counter += 1
            role.role_id_stable = f"role_{role_counter}"
            _assign_para(role.header)
            for pm in role.header_extra:
                _assign_para(pm)
            for pm in role.meta_lines:
                _assign_para(pm)
            for pm in role.bullets:
                _assign_para(pm)
        for pm in section.body_paras:
            _assign_para(pm)

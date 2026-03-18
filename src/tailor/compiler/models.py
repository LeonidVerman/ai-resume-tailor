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
class ParagraphProfile:
    """Formatting profile for PDF-sourced paragraphs.

    Used when xml_proto is not available.  The renderer (para_builder) uses
    these fields to construct a w:p element via lxml directly.
    """

    font_name: str | None = None
    font_size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    indent_left_pt: float = 0.0   # points from the page's left margin
    space_before_pt: float = 0.0  # estimated from Y-gap to previous block
    space_after_pt: float = 0.0
    alignment: str | None = None  # 'left' | 'center' | 'right' | 'justify'

    def to_dict(self) -> dict:
        return {
            "font_name": self.font_name,
            "font_size_pt": self.font_size_pt,
            "bold": self.bold,
            "italic": self.italic,
            "indent_left_pt": self.indent_left_pt,
            "space_before_pt": self.space_before_pt,
            "space_after_pt": self.space_after_pt,
            "alignment": self.alignment,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ParagraphProfile":
        return cls(
            font_name=d.get("font_name"),
            font_size_pt=d.get("font_size_pt"),
            bold=bool(d.get("bold", False)),
            italic=bool(d.get("italic", False)),
            indent_left_pt=float(d.get("indent_left_pt", 0.0)),
            space_before_pt=float(d.get("space_before_pt", 0.0)),
            space_after_pt=float(d.get("space_after_pt", 0.0)),
            alignment=d.get("alignment"),
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

    def with_text(self, new_text: str) -> "ParaModel":
        """Return a copy sharing this paragraph's style but with different text."""
        return ParaModel(
            text=new_text,
            style=self.style,
            semantic=self.semantic,
            paragraph_profile=self.paragraph_profile,
        )

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
    role_id: str = ""   # normalised header text; used as stable anchor for matching

    def to_dict(self) -> dict:
        return {
            "header": self.header.to_dict(),
            "header_extra": [p.to_dict() for p in self.header_extra],
            "meta_lines": [p.to_dict() for p in self.meta_lines],
            "bullets": [p.to_dict() for p in self.bullets],
            "role_id": self.role_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "RoleEntry":
        return cls(
            header=ParaModel.from_dict(d["header"]),
            header_extra=[ParaModel.from_dict(p) for p in d.get("header_extra", [])],
            meta_lines=[ParaModel.from_dict(p) for p in d.get("meta_lines", [])],
            bullets=[ParaModel.from_dict(p) for p in d.get("bullets", [])],
            role_id=d.get("role_id", ""),
        )


@dataclass
class ResumeSection:
    """One section of the resume (e.g. Experience, Technical Skills)."""

    title: str          # raw heading text from document
    heading: ParaModel
    semantic_type: str  # experience | summary | skills | education | other
    body_paras: list[ParaModel] = field(default_factory=list)   # non-experience sections
    roles: list[RoleEntry] = field(default_factory=list)        # experience sections only

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "semantic_type": self.semantic_type,
            "heading": self.heading.to_dict(),
            "body_paras": [p.to_dict() for p in self.body_paras],
            "roles": [r.to_dict() for r in self.roles],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeSection":
        return cls(
            title=d["title"],
            heading=ParaModel.from_dict(d["heading"]),
            semantic_type=d["semantic_type"],
            body_paras=[ParaModel.from_dict(p) for p in d.get("body_paras", [])],
            roles=[RoleEntry.from_dict(r) for r in d.get("roles", [])],
        )


@dataclass
class LayoutProfile:
    """Page-level layout extracted from the source DOCX."""

    page_width_pt: float
    page_height_pt: float
    margin_top_pt: float
    margin_bottom_pt: float
    margin_left_pt: float
    margin_right_pt: float
    default_font_name: str
    default_font_size_pt: float

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
    body_items: list[Any] | None = None  # list[ParaModel | TableBlock]; None for PDF/deserialised

    def to_dict(self) -> dict:
        """Serialize to a JSON-compatible dict.  xml_proto is not included."""
        return {
            "source_kind": self.source_kind,
            "header_paras": [p.to_dict() for p in self.header_paras],
            "sections": [s.to_dict() for s in self.sections],
            "layout": self.layout.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResumeDocument":
        """Reconstruct a ResumeDocument from a serialized dict.

        all_paras is rebuilt from header_paras + sections in document order.
        xml_proto fields are always None; the renderer uses para_builder.
        """
        header_paras = [ParaModel.from_dict(p) for p in d.get("header_paras", [])]
        sections = [ResumeSection.from_dict(s) for s in d.get("sections", [])]
        layout = LayoutProfile.from_dict(d["layout"])

        all_paras: list[ParaModel] = list(header_paras)
        for section in sections:
            all_paras.append(section.heading)
            if section.semantic_type == "experience":
                for role in section.roles:
                    all_paras.append(role.header)
                    all_paras.extend(role.meta_lines)
                    all_paras.extend(role.bullets)
            else:
                all_paras.extend(section.body_paras)

        return cls(
            header_paras=header_paras,
            sections=sections,
            layout=layout,
            all_paras=all_paras,
            source_kind=d.get("source_kind", "pdf"),
        )

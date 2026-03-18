"""Intermediate representation (IR) for a parsed resume document.

The IR is format-agnostic: it captures the content and structure of a resume
along with per-paragraph style metadata.  A separate renderer turns it back
into a DOCX.  The xml_proto field on ParaStyle is a deepcopy of the original
w:p element; cloning it during rendering preserves formatting exactly.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any


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

    def with_text(self, new_text: str) -> "ParaModel":
        """Return a copy sharing this paragraph's style but with different text."""
        return ParaModel(text=new_text, style=self.style, semantic=self.semantic)

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
        return ParaModel(text=new_text, style=cloned, semantic=semantic or self.semantic)


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


@dataclass
class ResumeSection:
    """One section of the resume (e.g. Experience, Technical Skills)."""

    title: str          # raw heading text from document
    heading: ParaModel
    semantic_type: str  # experience | summary | skills | education | other
    body_paras: list[ParaModel] = field(default_factory=list)   # non-experience sections
    roles: list[RoleEntry] = field(default_factory=list)        # experience sections only


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


@dataclass
class ResumeDocument:
    """Full parsed resume.

    header_paras  – paragraphs before the first section heading (name, contact).
    sections      – structured section list.
    layout        – document-level page geometry.
    all_paras     – flat ordered list mirroring doc.paragraphs; used for plain-text
                    serialisation and to drive the renderer in document order.
    """

    header_paras: list[ParaModel]
    sections: list[ResumeSection]
    layout: LayoutProfile
    all_paras: list[ParaModel]

"""Data models for structural DOCX representation.

These classes form an intermediate representation of a DOCX document that
preserves formatting metadata at the paragraph and run level, enabling
block-level editing without destroying layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunStyle:
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    font_name: str | None = None
    font_size_pt: float | None = None
    caps: bool | None = None
    small_caps: bool | None = None
    color: str | None = None


@dataclass
class RunModel:
    text: str
    style: RunStyle
    xml_ref: Any = None  # lxml element (w:r or hyperlink child)


@dataclass
class ParagraphFormat:
    style_id: str | None = None
    style_name: str | None = None
    alignment: str | None = None
    indent_left: int | None = None    # twips
    indent_right: int | None = None
    hanging: int | None = None
    spacing_before: int | None = None  # twips
    spacing_after: int | None = None
    line_spacing: int | None = None
    keep_together: bool | None = None
    keep_with_next: bool | None = None
    page_break_before: bool | None = None
    tabs: list[Any] = field(default_factory=list)  # lxml tab elements
    numbering: dict | None = None  # {"ilvl": int, "numId": int}


@dataclass
class ParagraphModel:
    text: str
    runs: list[RunModel]
    fmt: ParagraphFormat
    xml_ref: Any  # lxml element (w:p)
    semantic_type: str | None = None
    # semantic_type values:
    #   section_heading, role_header, role_meta, bullet, paragraph, empty


@dataclass
class RoleBlock:
    """One experience entry: header line(s) + date line + bullets + trailing blanks."""
    header_paragraphs: list[ParagraphModel]
    meta_paragraphs: list[ParagraphModel]      # date/location lines
    bullet_paragraphs: list[ParagraphModel]
    trailing_paragraphs: list[ParagraphModel]  # blank separator paragraphs
    role_id: str                               # text fingerprint for matching


@dataclass
class SectionBlock:
    heading: ParagraphModel | None
    body_paragraphs: list[ParagraphModel]  # all content paragraphs (flattened)
    roles: list[RoleBlock]                 # populated only for experience sections
    semantic_type: str                     # experience | summary | skills | education | other
    raw_heading_text: str


@dataclass
class DocumentModel:
    header_paragraphs: list[ParagraphModel]  # paragraphs before first section heading
    sections: list[SectionBlock]
    all_paragraphs: list[ParagraphModel]
    debug_info: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# LLM text structures (parsed from plain-text LLM output)
# ---------------------------------------------------------------------------

@dataclass
class LlmRole:
    header: str            # "Company | Title" line
    meta_lines: list[str]  # date/location lines between header and bullets
    bullets: list[str]     # bullet text (leading "- " stripped)


@dataclass
class LlmSection:
    heading: str
    body_lines: list[str]  # non-experience sections (flat lines)
    roles: list[LlmRole]   # experience sections
    semantic_type: str     # same classification as SectionBlock

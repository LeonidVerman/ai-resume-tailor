"""Data models for the PDF round-trip evaluator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SpanModel:
    text: str
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1
    font_size: float
    font_name: str
    is_bold: bool
    is_italic: bool


@dataclass
class LineModel:
    text: str
    bbox: tuple[float, float, float, float]
    spans: list[SpanModel]
    left_x: float
    right_x: float
    baseline_y: float
    is_heading_candidate: bool = False
    is_bullet_candidate: bool = False
    indent_class: str = "body"   # body | bullet | heading | header


@dataclass
class BlockModel:
    block_id: str
    block_type: str                           # paragraph | bullet_group | heading | header
    bbox: tuple[float, float, float, float]
    lines: list[LineModel]
    dominant_left_x: float
    spacing_before: float = 0.0
    spacing_after: float = 0.0
    section_guess: str = ""


@dataclass
class PageModel:
    page_number: int                          # 1-based
    width: float
    height: float
    lines: list[LineModel]
    blocks: list[BlockModel]
    content_bbox: tuple[float, float, float, float] | None = None


@dataclass
class DocumentFeatures:
    page_count: int
    column_count_estimate: int
    column_confidence: str                    # high | low | none
    dominant_left_margins: list[float]
    dominant_line_gap: float
    dominant_section_gap: float
    dominant_body_left_x: float              # content-relative reference point


@dataclass
class ExtractedDoc:
    path: str
    pages: list[PageModel]
    features: DocumentFeatures

    # Full normalized text (all pages concatenated)
    full_text_normalized: str = ""
    # Heading texts extracted from document
    headings: list[str] = field(default_factory=list)
    # Count of bullet candidate lines
    bullet_count: int = 0
    # Dominant bullet indent (absolute pt from page left)
    dominant_bullet_left_x: float | None = None


@dataclass
class TextMetrics:
    token_precision: float
    token_recall: float
    token_f1: float
    sequence_similarity: float
    missing_headings: list[str]
    extra_headings: list[str]
    bullet_count_source: int
    bullet_count_output: int
    bullet_count_delta: int


@dataclass
class LayoutMetrics:
    page_count_match: bool
    page_count_source: int
    page_count_output: int
    page_size_match: bool
    content_bbox_shift_norm: float | None     # normalized by page width/height
    mean_line_x_shift_pt: float | None
    mean_line_y_shift_pt: float | None
    bullet_indent_delta_norm: float | None    # content-relative, normalized by page width
    bullet_indent_source_pt: float | None
    bullet_indent_output_pt: float | None
    section_gap_delta_norm: float | None
    section_gap_source_pt: float | None
    section_gap_output_pt: float | None
    column_count_source: int
    column_count_output: int
    column_confidence: str


@dataclass
class IssueModel:
    issue_type: str
    severity: str                             # high | medium | low
    page: int
    section: str = ""
    source_value: Any = None
    output_value: Any = None
    delta: Any = None
    description: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "type": self.issue_type,
            "severity": self.severity,
            "page": self.page,
        }
        if self.section:
            d["section"] = self.section
        if self.source_value is not None:
            d["source_value"] = self.source_value
        if self.output_value is not None:
            d["output_value"] = self.output_value
        if self.delta is not None:
            d["delta"] = self.delta
        d["description"] = self.description
        return d


@dataclass
class ComparisonResult:
    text_metrics: TextMetrics
    layout_metrics: LayoutMetrics
    issues: list[IssueModel]
    text_score: float
    structure_score: float
    layout_score: float
    overall_score: float
    status: str                               # pass | fail | error
    error_message: str = ""

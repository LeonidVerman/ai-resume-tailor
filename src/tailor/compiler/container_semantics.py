"""Container semantics inference for PDF-origin resume IRs.

Introduces an explicit semantic layer between IR parsing and rendering.
The renderer already understands pages, sections, tables, and columns.
This module adds:

  - semantic container ownership (which sections belong to which region)
  - independent vertical stack detection (sidebar ≠ synchronized rows)
  - local paired-region detection (internal sub-columns within a section)
  - rewriteable vs preserve region classification

Container types
---------------
independent_vertical_stack
    Left and right column sections grow independently with no row-level
    synchronization.  Content in each stack is purely vertical and the
    stacks only happen to share a page.  Sample 11: left sidebar
    (Contact/Communication/Leadership) vs right body (Education/Experience).

synchronized_row
    Left and right column cells correspond at the row level: left holds a
    section label, right holds that section's body.  Sample 20, 28.
    Inferred when section_row_table=True.

local_column_pair
    A section that internally contains a two-sub-column structure: a narrow
    left label/date column and a wider right bullet/content column, all
    within a single outer column cell.  Sample 26: Education Summary with
    dates on the left and bullets on the right.

paired_sidebar_region
    The overall two-column layout where left is the main body and right is
    a fixed-width sidebar with its own independent sections.  Broad layout
    tag applied to the document; individual sections still get more specific
    types.

rewriteable_region
    Experience / work-history sections whose bullet content the LLM may
    replace.  Role header + meta_lines are preserved; bullets are the
    injection target.

preserve_region
    Sections whose content must be kept verbatim: contact info, skills
    rating grids, social links, tech-stack labels, certification lists.
    The LLM may not inject free-form bullets here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tailor.compiler.models import ResumeDocument, ResumeSection

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Container node model
# ---------------------------------------------------------------------------

@dataclass
class ContainerNode:
    """One container region in the document's semantic layout."""

    container_type: str
    section_ids: list[str] = field(default_factory=list)
    column_side: str | None = None        # "left" | "right" | None (above/full-width)
    rewriteable: bool = True
    children: "list[ContainerNode]" = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "container_type": self.container_type,
            "section_ids": self.section_ids,
            "column_side": self.column_side,
            "rewriteable": self.rewriteable,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class ContainerTree:
    """Top-level container tree for a resume document."""

    document_mode: str | None           # mirrors layout.table_layout_mode
    containers: list[ContainerNode] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_mode": self.document_mode,
            "containers": [c.to_dict() for c in self.containers],
        }


# ---------------------------------------------------------------------------
# Inference helpers
# ---------------------------------------------------------------------------

_PRESERVE_SEMANTIC_TYPES = frozenset({
    "contact", "skills", "certifications", "other",
})

_EXPERIENCE_SEMANTIC_TYPES = frozenset({
    "experience",
})

_SECTION_LABEL_ONLY = frozenset({
    "contact", "references", "links",
})

# Keywords that mark a section as preserve-only (skills grids, links, etc.)
_PRESERVE_TITLE_KEYWORDS = frozenset({
    "contact", "phone", "email", "address", "website", "link",
    "skill", "skills", "technology", "technologies", "tools",
    "certification", "certifications", "language", "languages",
    "reference", "references", "profile", "social",
})


def _section_is_preserve(sec: "ResumeSection") -> bool:
    """True when a section should not be freely rewritten by the LLM."""
    title_lower = sec.title.lower()
    if any(kw in title_lower for kw in _PRESERVE_TITLE_KEYWORDS):
        return True
    if sec.semantic_type in _PRESERVE_SEMANTIC_TYPES:
        return True
    # Sections with very few short paragraphs and no bullets (contact cards).
    if not sec.roles and len(sec.body_paras) <= 6:
        texts = [pm.text.strip() for pm in sec.body_paras]
        avg_len = sum(len(t) for t in texts) / max(len(texts), 1)
        if avg_len < 50:
            return True
    return False


def _has_local_column_pair(sec: "ResumeSection") -> bool:
    """True when the section's body_paras span two distinct x-clusters.

    Signals that an Education-Summary-style section has an internal
    two-sub-column structure (dates | bullets) within a single outer cell.
    """
    left_xs: list[float] = []
    right_xs: list[float] = []
    for pm in sec.body_paras:
        pp = pm.paragraph_profile
        if pp is None:
            continue
        x = pp.indent_left_pt
        # Use relative indent; paragraphs with indent > 60 pt from the
        # section heading are in the right sub-column.
        if x <= 30.0:
            left_xs.append(x)
        else:
            right_xs.append(x)
    # Need ≥2 in each sub-column to call it a local pair.
    return len(left_xs) >= 2 and len(right_xs) >= 2


def _pairing_rate(
    left_secs: "list[ResumeSection]",
    right_secs: "list[ResumeSection]",
    tolerance_pt: float = 15.0,
) -> float:
    """Fraction of left-column section headings that have a right-column
    section heading at approximately the same y-position.

    High pairing rate → synchronized_row.
    Low pairing rate → independent_vertical_stack.
    """
    if not left_secs or not right_secs:
        return 0.0

    right_ys: list[float] = []
    for rs in right_secs:
        y = rs.heading.paragraph_profile.y_top_pt if rs.heading.paragraph_profile else 0.0
        if y > 0:
            right_ys.append(y)

    if not right_ys:
        return 0.0

    paired = 0
    total = 0
    for ls in left_secs:
        y = ls.heading.paragraph_profile.y_top_pt if ls.heading.paragraph_profile else 0.0
        if y <= 0:
            continue
        total += 1
        if any(abs(y - ry) <= tolerance_pt for ry in right_ys):
            paired += 1

    return paired / total if total else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def infer_container_semantics(doc: "ResumeDocument") -> ContainerTree:
    """Infer container semantics from the IR's geometry and layout profile.

    Attaches `container_type` to each `ResumeSection` in-place and returns
    a `ContainerTree` summary for use by the renderer and diagnostics.
    """
    layout = doc.layout
    mode = layout.table_layout_mode
    tree = ContainerTree(document_mode=mode)

    # --- Synchronized rows (section-label-column layout) ---
    if layout.section_row_table:
        for sec in doc.sections:
            sec.container_type = "synchronized_row"
            node = ContainerNode(
                container_type="synchronized_row",
                section_ids=[sec.section_id or sec.title],
                column_side=sec.heading.paragraph_profile.column_id
                    if sec.heading.paragraph_profile else None,
                rewriteable=sec.semantic_type in _EXPERIENCE_SEMANTIC_TYPES,
            )
            tree.containers.append(node)
        log.debug(
            "CONTAINER_TYPE_DETECTED: synchronized_row for all %d sections",
            len(doc.sections),
        )
        return tree

    # --- Two-column documents ---
    if layout.column_split_x is not None:
        left_secs = [
            s for s in doc.sections
            if s.heading.paragraph_profile
            and s.heading.paragraph_profile.column_id == "left"
        ]
        right_secs = [
            s for s in doc.sections
            if s.heading.paragraph_profile
            and s.heading.paragraph_profile.column_id == "right"
        ]
        above_secs = [
            s for s in doc.sections
            if s not in left_secs and s not in right_secs
        ]

        # Decide left-column container type.
        rate = _pairing_rate(left_secs, right_secs)
        if rate >= 0.50:
            col_type = "synchronized_row"
            log.debug(
                "SYNCHRONIZED_ROW_DETECTED: pairing_rate=%.2f left=%d right=%d",
                rate, len(left_secs), len(right_secs),
            )
        else:
            col_type = "independent_vertical_stack"
            log.debug(
                "INDEPENDENT_STACK_DETECTED: pairing_rate=%.2f left=%d right=%d",
                rate, len(left_secs), len(right_secs),
            )

        # Above-column (header) sections.
        for sec in above_secs:
            sec.container_type = "preserve_region"
            tree.containers.append(ContainerNode(
                container_type="preserve_region",
                section_ids=[sec.section_id or sec.title],
                column_side=None,
                rewriteable=False,
            ))

        # Left-column sections.
        left_node = ContainerNode(
            container_type=col_type,
            section_ids=[s.section_id or s.title for s in left_secs],
            column_side="left",
            rewriteable=True,
        )
        for sec in left_secs:
            if sec.semantic_type in _EXPERIENCE_SEMANTIC_TYPES:
                sec.container_type = "rewriteable_region"
                log.debug(
                    "REGION_OWNERSHIP_ASSIGNED: section=%r → rewriteable_region",
                    sec.title,
                )
            elif _has_local_column_pair(sec):
                sec.container_type = "local_column_pair"
                log.debug(
                    "CONTAINER_TYPE_DETECTED: local_column_pair section=%r",
                    sec.title,
                )
            elif _section_is_preserve(sec):
                sec.container_type = "preserve_region"
            else:
                sec.container_type = col_type
        tree.containers.append(left_node)

        # Right-column sections.
        right_node = ContainerNode(
            container_type=col_type,
            section_ids=[s.section_id or s.title for s in right_secs],
            column_side="right",
            rewriteable=False,
        )
        for sec in right_secs:
            if _section_is_preserve(sec):
                sec.container_type = "preserve_region"
                log.debug(
                    "REGION_OWNERSHIP_ASSIGNED: section=%r → preserve_region (right sidebar)",
                    sec.title,
                )
            elif sec.semantic_type in _EXPERIENCE_SEMANTIC_TYPES:
                sec.container_type = "rewriteable_region"
            else:
                sec.container_type = col_type
        tree.containers.append(right_node)

        log.debug(
            "CONTAINER_TYPE_DETECTED: mode=%r left=%d right=%d above=%d",
            mode, len(left_secs), len(right_secs), len(above_secs),
        )
        return tree

    # --- Single-column documents ---
    for sec in doc.sections:
        if sec.semantic_type in _EXPERIENCE_SEMANTIC_TYPES:
            sec.container_type = "rewriteable_region"
        elif _section_is_preserve(sec):
            sec.container_type = "preserve_region"
        else:
            sec.container_type = None
        if sec.container_type:
            log.debug(
                "REGION_OWNERSHIP_ASSIGNED: section=%r → %s",
                sec.title, sec.container_type,
            )

    return tree

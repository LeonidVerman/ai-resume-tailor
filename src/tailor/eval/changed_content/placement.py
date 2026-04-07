"""Section placement detection for changed-content layout evaluation.

For each target section type (summary / experience / education / skills /
languages / certifications), locates the section heading in both the source
and output PDF using block geometry from the existing extractor, then scores
placement across four dimensions:

  page_score      — section lands on the same page
  vertical_score  — similar vertical position (normalized y)
  region_score    — same visual region (main / sidebar)
  order_score     — same relative order among all detected sections

Reuse inventory
---------------
- ExtractedDoc / PageModel / BlockModel  tailor.eval.models   unchanged
- block_type == "heading" scan           existing extractor    unchanged
- No changes to extractor.py, comparator.py, or same-text pipeline

All logic here is deterministic: no ML, no randomness, table-driven aliases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tailor.eval.models import ExtractedDoc


# ---------------------------------------------------------------------------
# Section alias table
# ---------------------------------------------------------------------------

SECTION_ALIASES: dict[str, list[str]] = {
    "summary": [
        "professional summary", "summary", "profile", "professional profile",
        "career summary", "about me", "executive summary", "objective",
        "career objective", "professional objective", "about", "overview",
    ],
    "experience": [
        "experience", "professional experience", "employment history",
        "work experience", "career history", "work history",
        "professional background", "employment",
    ],
    "education": [
        "education", "academic background", "education and training",
        "educational background", "degrees", "academic credentials",
        "education and credentials",
    ],
    "skills": [
        "technical skills", "skills", "skill", "core competencies",
        "technical proficiencies", "expertise", "competencies",
        "key skills", "areas of expertise", "technologies", "tech stack",
        "technical expertise", "technology stack",
    ],
    "languages": [
        "languages", "language", "spoken languages", "languages and frameworks",
    ],
    "certifications": [
        "certifications", "certifications and training", "training",
        "licenses", "certification", "licences", "certificates",
    ],
}

_TARGET_TYPES: frozenset[str] = frozenset(SECTION_ALIASES.keys())

# Sub-score weights (must sum to 1.0)
_PLACEMENT_WEIGHTS: dict[str, float] = {
    "page":     0.20,
    "vertical": 0.35,
    "region":   0.30,
    "order":    0.15,
}

# Vertical tolerance by region type (normalized page-height units)
_VERTICAL_TOLERANCE: dict[str, float] = {
    "main":          0.20,
    "sidebar_left":  0.12,
    "sidebar_right": 0.12,
    "unknown":       0.20,
}

# Expected-zone thresholds for missing-input heuristics
_SUMMARY_EXPECTED_MAX_Y  = 0.40   # summary should be in top 40% of page 1
_SUMMARY_EXPECTED_PAGE   = 1


# ---------------------------------------------------------------------------
# Build reverse alias lookup at import time
# ---------------------------------------------------------------------------

def _build_alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            key = normalize_heading(alias)
            lookup[key] = canonical
        # The canonical name itself is also a valid alias
        lookup[normalize_heading(canonical)] = canonical
    return lookup


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SectionAnchor:
    """Detected heading with normalized geometry."""

    canonical_type: str
    raw_heading: str
    page: int                                       # 1-based
    bbox: tuple[float, float, float, float]         # (nx0, ny0, nx1, ny1), normalized 0-1
    region: str                                     # "main" | "sidebar_left" | "sidebar_right" | "unknown"

    @property
    def nx0(self) -> float:
        return self.bbox[0]

    @property
    def ny0(self) -> float:
        return self.bbox[1]

    @property
    def nx1(self) -> float:
        return self.bbox[2]

    @property
    def ny1(self) -> float:
        return self.bbox[3]

    @property
    def center_x(self) -> float:
        return (self.bbox[0] + self.bbox[2]) / 2.0

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]


@dataclass
class SectionPlacementResult:
    """Per-section placement comparison between source and output."""

    canonical_type: str
    input_found: bool
    output_found: bool
    input_page: int | None
    output_page: int | None
    input_y: float | None           # normalized top y of heading
    output_y: float | None
    input_region: str | None
    output_region: str | None
    page_score: float               # 0-1
    vertical_score: float           # 0-1
    region_score: float             # 0-1
    order_score: float              # 0-1
    placement_score: float          # weighted composite 0-1
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------

_STRIP_PUNCT_RE  = re.compile(r"[^\w\s]")
_STRIP_LEADING_N = re.compile(r"^\d+[\.\s]+")
_COLLAPSE_WS     = re.compile(r"\s+")


def normalize_heading(text: str) -> str:
    """Normalize a heading string for alias matching.

    Steps:
      - lowercase + strip
      - & → and
      - collapse whitespace
      - strip punctuation
      - strip leading number prefix ("1. Experience" → "experience")
    """
    t = text.lower().strip()
    t = t.replace("&", "and")
    t = _STRIP_LEADING_N.sub("", t)
    t = _STRIP_PUNCT_RE.sub("", t)
    t = _COLLAPSE_WS.sub(" ", t)
    return t.strip()


# Reverse lookup built once at import time
_ALIAS_TO_CANONICAL: dict[str, str] = _build_alias_lookup()


def match_canonical_type(text: str) -> str | None:
    """Return canonical section type for *text*, or None if not a target section."""
    return _ALIAS_TO_CANONICAL.get(normalize_heading(text))


# ---------------------------------------------------------------------------
# Region classifier
# ---------------------------------------------------------------------------

def classify_region(nx0: float, ny0: float, nx1: float, ny1: float) -> str:
    """Classify a normalized block bbox into a visual region.

    Returns one of: "main" | "sidebar_left" | "sidebar_right" | "unknown".

    Heuristic:
      - width < 0.45 (narrow block) AND center < 0.35 → sidebar_left
      - width < 0.45 (narrow block) AND center > 0.65 → sidebar_right
      - otherwise → main
    """
    if nx1 <= nx0:
        return "unknown"
    width  = nx1 - nx0
    center = (nx0 + nx1) / 2.0
    if width < 0.45:
        if center < 0.35:
            return "sidebar_left"
        if center > 0.65:
            return "sidebar_right"
    return "main"


# ---------------------------------------------------------------------------
# Anchor extraction
# ---------------------------------------------------------------------------

def extract_section_anchors(doc: ExtractedDoc) -> list[SectionAnchor]:
    """Return a list of SectionAnchor for all target-section headings in *doc*.

    Scans blocks with block_type == "heading" across all pages.
    Normalizes bbox coordinates to [0,1] using page dimensions.
    Results are sorted in reading order (page asc, y asc, x asc).

    Returns an empty list for documents whose extractor output has no blocks
    (e.g., synthetic test fixtures).
    """
    anchors: list[SectionAnchor] = []

    for page in doc.pages:
        pw = page.width
        ph = page.height
        if pw <= 0 or ph <= 0:
            continue

        for block in page.blocks:
            if block.block_type != "heading":
                continue

            # Collect text from block lines
            text = " ".join(
                ln.text.strip() for ln in block.lines if ln.text.strip()
            ).strip()
            if not text:
                continue

            canonical = match_canonical_type(text)
            if canonical is None:
                continue  # not a target section

            x0, y0, x1, y1 = block.bbox
            nx0 = max(0.0, min(1.0, x0 / pw))
            ny0 = max(0.0, min(1.0, y0 / ph))
            nx1 = max(0.0, min(1.0, x1 / pw))
            ny1 = max(0.0, min(1.0, y1 / ph))

            region = classify_region(nx0, ny0, nx1, ny1)
            anchors.append(SectionAnchor(
                canonical_type=canonical,
                raw_heading=text,
                page=page.page_number,
                bbox=(round(nx0, 4), round(ny0, 4), round(nx1, 4), round(ny1, 4)),
                region=region,
            ))

    # Sort into reading order: page asc, y asc, x asc
    anchors.sort(key=lambda a: (a.page, a.bbox[1], a.bbox[0]))
    return anchors


# ---------------------------------------------------------------------------
# Per-section scoring helpers
# ---------------------------------------------------------------------------

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _page_score(inp_page: int, out_page: int) -> float:
    delta = abs(out_page - inp_page)
    return _clamp(1.0 - 0.5 * delta)


def _vertical_score(inp_y: float, out_y: float, region: str) -> float:
    tol = _VERTICAL_TOLERANCE.get(region, 0.20)
    dy = abs(out_y - inp_y)
    return _clamp(1.0 - dy / tol) if tol > 0 else 1.0


def _region_score(inp_region: str, out_region: str) -> float:
    if inp_region == out_region:
        return 1.0
    # main ↔ sidebar is a strong mismatch
    inp_is_main = inp_region == "main"
    out_is_main = out_region == "main"
    if inp_is_main != out_is_main:
        return 0.0
    # sidebar side changed (left ↔ right) — minor mismatch
    return 0.5


def _order_score(inp_idx: int | None, out_idx: int | None) -> float:
    if inp_idx is None or out_idx is None:
        return 0.5
    delta = abs(inp_idx - out_idx)
    return _clamp(1.0 - delta * 0.25)


def _weighted_placement(pg: float, vt: float, rg: float, od: float) -> float:
    return _clamp(
        pg * _PLACEMENT_WEIGHTS["page"]
        + vt * _PLACEMENT_WEIGHTS["vertical"]
        + rg * _PLACEMENT_WEIGHTS["region"]
        + od * _PLACEMENT_WEIGHTS["order"]
    )


# ---------------------------------------------------------------------------
# Expected-zone heuristics for absent-input sections
# ---------------------------------------------------------------------------

def _expected_zone_score(canonical_type: str, out: SectionAnchor) -> float | None:
    """Return a score when the section is present in output but absent in input.

    Returns a float if the output position is in the 'expected' zone for that
    section type, or None to fall back to neutral (0.5).
    """
    if canonical_type == "summary":
        if out.page == _SUMMARY_EXPECTED_PAGE and out.region == "main" and out.ny0 < _SUMMARY_EXPECTED_MAX_Y:
            return 0.70   # reasonable placement for an inserted summary
    return None


# ---------------------------------------------------------------------------
# Score a single section pair
# ---------------------------------------------------------------------------

def _score_one(
    canonical_type: str,
    inp: SectionAnchor | None,
    out: SectionAnchor | None,
    inp_order_idx: int | None,
    out_order_idx: int | None,
) -> SectionPlacementResult:
    """Score placement for one canonical section type."""
    notes: list[str] = []

    # ── Case C: both missing ─────────────────────────────────────────────
    if inp is None and out is None:
        notes.append(f"'{canonical_type}' absent in both source and output")
        return SectionPlacementResult(
            canonical_type=canonical_type,
            input_found=False, output_found=False,
            input_page=None, output_page=None,
            input_y=None, output_y=None,
            input_region=None, output_region=None,
            page_score=0.5, vertical_score=0.5,
            region_score=0.5, order_score=0.5,
            placement_score=0.5,
            notes=notes,
        )

    # ── Case B: output found, input missing ──────────────────────────────
    if inp is None and out is not None:
        ez = _expected_zone_score(canonical_type, out)
        if ez is not None:
            notes.append(
                f"'{canonical_type}' absent in source; output placed in expected zone "
                f"(page={out.page}, y={out.ny0:.2f}, region={out.region}) — scored {ez:.2f}"
            )
            ps = ez
        else:
            notes.append(
                f"'{canonical_type}' absent in source but present in output "
                f"(page={out.page}, region={out.region}) — neutral score"
            )
            ps = 0.5
        return SectionPlacementResult(
            canonical_type=canonical_type,
            input_found=False, output_found=True,
            input_page=None, output_page=out.page,
            input_y=None, output_y=round(out.ny0, 4),
            input_region=None, output_region=out.region,
            page_score=0.5, vertical_score=0.5,
            region_score=0.5, order_score=0.5,
            placement_score=round(ps, 3),
            notes=notes,
        )

    # ── Case A: input found, output missing ──────────────────────────────
    if inp is not None and out is None:
        notes.append(
            f"'{canonical_type}' found in source (page={inp.page}, region={inp.region}) "
            f"but missing from output"
        )
        return SectionPlacementResult(
            canonical_type=canonical_type,
            input_found=True, output_found=False,
            input_page=inp.page, output_page=None,
            input_y=round(inp.ny0, 4), output_y=None,
            input_region=inp.region, output_region=None,
            page_score=0.0, vertical_score=0.0,
            region_score=0.0, order_score=0.0,
            placement_score=0.0,
            notes=notes,
        )

    # ── Both found: full scoring ─────────────────────────────────────────
    pg = _page_score(inp.page, out.page)
    vt = _vertical_score(inp.ny0, out.ny0, inp.region)
    rg = _region_score(inp.region, out.region)
    od = _order_score(inp_order_idx, out_order_idx)
    ps = _weighted_placement(pg, vt, rg, od)

    if pg < 1.0:
        notes.append(
            f"Page drift: source page={inp.page}, output page={out.page}"
        )
    if vt < 0.60:
        notes.append(
            f"Vertical drift: source y={inp.ny0:.2f}, output y={out.ny0:.2f} "
            f"(tolerance={_VERTICAL_TOLERANCE.get(inp.region, 0.20):.2f})"
        )
    if rg == 0.0:
        notes.append(
            f"Region mismatch: source={inp.region}, output={out.region}"
        )
    elif rg < 1.0:
        notes.append(
            f"Sidebar side changed: source={inp.region}, output={out.region}"
        )
    if od < 0.75 and inp_order_idx is not None and out_order_idx is not None:
        notes.append(
            f"Order drift: source rank={inp_order_idx}, output rank={out_order_idx}"
        )

    return SectionPlacementResult(
        canonical_type=canonical_type,
        input_found=True, output_found=True,
        input_page=inp.page, output_page=out.page,
        input_y=round(inp.ny0, 4), output_y=round(out.ny0, 4),
        input_region=inp.region, output_region=out.region,
        page_score=round(pg, 3), vertical_score=round(vt, 3),
        region_score=round(rg, 3), order_score=round(od, 3),
        placement_score=round(ps, 3),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_placement(
    src: ExtractedDoc,
    out: ExtractedDoc,
) -> tuple[list[SectionPlacementResult], float | None]:
    """Compute per-section placement scores by comparing source and output PDFs.

    Returns
    -------
    (results, overall_score)
        results       — one SectionPlacementResult per target section type
        overall_score — mean placement_score across all results, or None if
                        no heading anchors were found in either document
                        (signals "no geometric data available — caller should
                        fall back to heading-count heuristic").
    """
    src_anchors = extract_section_anchors(src)
    out_anchors = extract_section_anchors(out)

    # If neither document has any recognized anchors, signal no geometric data
    if not src_anchors and not out_anchors:
        return [], None

    # Best anchor per canonical type (first in reading order wins)
    def _best(anchors: list[SectionAnchor], ctype: str) -> SectionAnchor | None:
        for a in anchors:
            if a.canonical_type == ctype:
                return a
        return None

    # Order index per canonical type in each doc
    src_order = {a.canonical_type: i for i, a in enumerate(src_anchors)}
    out_order = {a.canonical_type: i for i, a in enumerate(out_anchors)}

    results: list[SectionPlacementResult] = []
    for ctype in sorted(_TARGET_TYPES):
        inp = _best(src_anchors, ctype)
        out_a = _best(out_anchors, ctype)
        result = _score_one(
            canonical_type=ctype,
            inp=inp,
            out=out_a,
            inp_order_idx=src_order.get(ctype),
            out_order_idx=out_order.get(ctype),
        )
        results.append(result)

    # Overall: mean of placement_score over sections where at least one was found
    active = [r for r in results if r.input_found or r.output_found]
    if not active:
        return results, None

    overall = sum(r.placement_score for r in active) / len(active)
    return results, round(overall, 3)

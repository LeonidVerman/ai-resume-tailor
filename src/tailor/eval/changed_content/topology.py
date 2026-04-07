"""Topology classifier for rendered resume PDFs.

Classifies a document into one of five layout classes based purely on
the horizontal distribution and persistence of extracted PDF blocks:

  linear_single_column  — most content spans full page width
  sidebar_left          — narrow persistent left region + wider right main body
  sidebar_right         — narrow persistent right region + wider left main body
  two_column_balanced   — two substantial columns of similar width
  mixed_or_ambiguous    — conflicting signals or low confidence

Algorithm
---------
1. Normalize block coordinates to [0, 1] using page dimensions.
2. Filter noise blocks (empty, tiny, whitespace-only).
3. Classify each block as narrow (width < 0.42) or wide.
4. Signal A — side persistence: divide page into 6 vertical bands;
   count how many bands contain ≥1 narrow block on each side.
5. Signal B — area fractions in left / right halves.
6. Signal C — wide-block area fraction (linear-body dominance).
7. Classify each page by deterministic threshold rules; prefer
   mixed_or_ambiguous over brittle wrong classification.
8. Aggregate page results into a document-level classification with
   confidence and evidence.

Reuse inventory
---------------
- ExtractedDoc / PageModel / BlockModel  tailor.eval.models   unchanged
- No changes to extractor, comparator, or same-text pipeline
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from tailor.eval.models import ExtractedDoc, PageModel


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

_NARROW_WIDTH          = 0.42    # block width threshold (normalized) → "narrow"
_LEFT_BOUNDARY         = 0.35    # block center_x < this → left-side candidate
_RIGHT_BOUNDARY        = 0.65    # block center_x > this → right-side candidate
_N_VERTICAL_BANDS      = 6       # vertical bands for persistence calculation
_MIN_BLOCK_TEXT_LEN    = 3       # minimum non-whitespace chars to keep a block
_MIN_BLOCK_WIDTH_NORM  = 0.03    # minimum normalized width
_MIN_BLOCK_HEIGHT_NORM = 0.008   # minimum normalized height

# Sidebar thresholds
_SIDEBAR_PERSIST_MIN   = 0.40    # ≥ 40 % of vertical bands covered
_SIDEBAR_SIDE_MIN_AREA = 0.05    # sidebar side ≥ 5 % of total block area
_SIDEBAR_SIDE_MAX_AREA = 0.45    # sidebar side < 45 % (else balanced)
_SIDEBAR_MAIN_MIN_AREA = 0.30    # opposite (main) side ≥ 30 %

# Two-column balanced thresholds
_BALANCED_MIN_EACH     = 0.28    # each half ≥ 28 % of total area
_BALANCED_MAX_IMBAL    = 0.25    # |left_frac − right_frac| < 25 %

# Linear single-column thresholds
_LINEAR_WIDE_MIN       = 0.55    # ≥ 55 % of area from wide (width > 0.50) blocks
_LINEAR_SIDE_MAX_PERSIST = 0.40  # neither side persistence ≥ 40 %

# Topology class string constants
TOPOLOGY_LINEAR   = "linear_single_column"
TOPOLOGY_SB_LEFT  = "sidebar_left"
TOPOLOGY_SB_RIGHT = "sidebar_right"
TOPOLOGY_BALANCED = "two_column_balanced"
TOPOLOGY_AMBIG    = "mixed_or_ambiguous"

# Preservation score for every cross-class transition
_PRESERVATION_SCORES: dict[tuple[str, str], float] = {
    (TOPOLOGY_SB_LEFT,  TOPOLOGY_SB_RIGHT): 0.25,
    (TOPOLOGY_SB_RIGHT, TOPOLOGY_SB_LEFT):  0.25,
    (TOPOLOGY_SB_LEFT,  TOPOLOGY_BALANCED): 0.50,
    (TOPOLOGY_SB_RIGHT, TOPOLOGY_BALANCED): 0.50,
    (TOPOLOGY_BALANCED, TOPOLOGY_SB_LEFT):  0.50,
    (TOPOLOGY_BALANCED, TOPOLOGY_SB_RIGHT): 0.50,
    (TOPOLOGY_LINEAR,   TOPOLOGY_SB_LEFT):  0.10,
    (TOPOLOGY_LINEAR,   TOPOLOGY_SB_RIGHT): 0.10,
    (TOPOLOGY_LINEAR,   TOPOLOGY_BALANCED): 0.25,
    (TOPOLOGY_SB_LEFT,  TOPOLOGY_LINEAR):   0.10,
    (TOPOLOGY_SB_RIGHT, TOPOLOGY_LINEAR):   0.10,
    (TOPOLOGY_BALANCED, TOPOLOGY_LINEAR):   0.25,
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PageTopologyResult:
    """Topology classification for one page."""
    topology: str
    signals: dict[str, float]
    notes: list[str] = field(default_factory=list)


@dataclass
class TopologyClassification:
    """Document-level topology classification.

    Attributes
    ----------
    doc_topology:     One of the five topology class strings.
    confidence:       0–1 scalar; higher = more evidence, more consistent.
    page_topologies:  Per-page topology string (index 0 = page 1).
    notes:            Human-readable signal / conflict notes.
    signals:          Averaged page-level signals, keyed by signal name.
    """
    doc_topology: str
    confidence: float
    page_topologies: list[str]
    notes: list[str]
    signals: dict[str, float]


# ---------------------------------------------------------------------------
# Internal normalized-block model
# ---------------------------------------------------------------------------

@dataclass
class _NormBlock:
    nx0: float
    ny0: float
    nx1: float
    ny1: float
    width: float     # nx1 − nx0
    height: float    # ny1 − ny0
    center_x: float  # (nx0 + nx1) / 2
    area: float      # width × height
    is_narrow: bool  # width < _NARROW_WIDTH


def _filter_and_normalize(page: PageModel) -> list[_NormBlock]:
    """Return noise-filtered, normalized blocks for one page.

    Drops blocks that are empty, whitespace-only, or geometrically tiny.
    """
    pw, ph = page.width, page.height
    if pw <= 0 or ph <= 0:
        return []

    result: list[_NormBlock] = []
    for block in page.blocks:
        text = " ".join(ln.text.strip() for ln in block.lines if ln.text.strip())
        if len(text.replace(" ", "")) < _MIN_BLOCK_TEXT_LEN:
            continue

        x0, y0, x1, y1 = block.bbox
        nx0 = max(0.0, min(1.0, x0 / pw))
        ny0 = max(0.0, min(1.0, y0 / ph))
        nx1 = max(0.0, min(1.0, x1 / pw))
        ny1 = max(0.0, min(1.0, y1 / ph))

        w = nx1 - nx0
        h = ny1 - ny0
        if w < _MIN_BLOCK_WIDTH_NORM or h < _MIN_BLOCK_HEIGHT_NORM:
            continue

        center_x = (nx0 + nx1) / 2.0
        result.append(_NormBlock(
            nx0=nx0, ny0=ny0, nx1=nx1, ny1=ny1,
            width=w, height=h, center_x=center_x,
            area=w * h,
            is_narrow=(w < _NARROW_WIDTH),
        ))
    return result


# ---------------------------------------------------------------------------
# Public signal helpers (exposed for testing)
# ---------------------------------------------------------------------------

def compute_horizontal_occupancy(
    blocks: list[_NormBlock],
    bins: int = 20,
) -> list[float]:
    """Build a horizontal occupancy histogram (length *bins*).

    Each bin represents an equal-width vertical strip of the page [0, 1].
    For every block, its area contribution is distributed across all bins
    it overlaps, weighted by the overlap width × block height.

    Returns a list of length *bins*; values are in [0, 1] (fraction of
    the maximum possible occupancy for each bin column).
    """
    hist = [0.0] * bins
    bin_w = 1.0 / bins
    for b in blocks:
        for i in range(bins):
            bin_left  = i * bin_w
            bin_right = bin_left + bin_w
            overlap   = max(0.0, min(b.nx1, bin_right) - max(b.nx0, bin_left))
            if overlap > 0.0:
                hist[i] += overlap * b.height
    # Normalise: max contribution per bin = bin_w * 1.0
    return [min(1.0, v / bin_w) for v in hist]


def compute_sidebar_persistence(
    blocks: list[_NormBlock],
    side: str = "left",
    n_bands: int = _N_VERTICAL_BANDS,
) -> float:
    """Fraction of vertical bands (0–1) containing ≥1 narrow block on *side*.

    A sidebar usually distributes narrow blocks across most of the page
    height.  A value ≥ 0.4 is strong sidebar evidence.
    One isolated top-left contact block produces a value of ~0.17 (1/6)
    which is well below the detection threshold.
    """
    if side == "left":
        side_blocks = [b for b in blocks if b.is_narrow and b.center_x < _LEFT_BOUNDARY]
    else:
        side_blocks = [b for b in blocks if b.is_narrow and b.center_x > _RIGHT_BOUNDARY]

    if not side_blocks:
        return 0.0

    bands_hit: set[int] = set()
    for b in side_blocks:
        mid_y    = (b.ny0 + b.ny1) / 2.0
        band_idx = min(n_bands - 1, int(mid_y * n_bands))
        bands_hit.add(band_idx)

    return len(bands_hit) / n_bands


# ---------------------------------------------------------------------------
# Page-level signal computation
# ---------------------------------------------------------------------------

def _compute_page_signals(blocks: list[_NormBlock]) -> dict[str, float]:
    """Compute all topology signals for a filtered, normalized block list."""
    if not blocks:
        return {
            "left_persistence": 0.0,
            "right_persistence": 0.0,
            "left_area_frac": 0.0,
            "right_area_frac": 0.0,
            "left_narrow_area_frac": 0.0,
            "right_narrow_area_frac": 0.0,
            "wide_area_frac": 0.0,
            "total_blocks": 0.0,
        }

    total_area = sum(b.area for b in blocks) or 1e-9

    left_blocks   = [b for b in blocks if b.center_x < 0.50]
    right_blocks  = [b for b in blocks if b.center_x >= 0.50]
    left_narrow   = [b for b in blocks if b.is_narrow and b.center_x < _LEFT_BOUNDARY]
    right_narrow  = [b for b in blocks if b.is_narrow and b.center_x > _RIGHT_BOUNDARY]
    wide_blocks   = [b for b in blocks if b.width > 0.50]

    return {
        "left_persistence":       compute_sidebar_persistence(blocks, "left"),
        "right_persistence":      compute_sidebar_persistence(blocks, "right"),
        "left_area_frac":         sum(b.area for b in left_blocks)  / total_area,
        "right_area_frac":        sum(b.area for b in right_blocks) / total_area,
        "left_narrow_area_frac":  sum(b.area for b in left_narrow)  / total_area,
        "right_narrow_area_frac": sum(b.area for b in right_narrow) / total_area,
        "wide_area_frac":         sum(b.area for b in wide_blocks)  / total_area,
        "total_blocks":           float(len(blocks)),
    }


def _classify_from_signals(
    signals: dict[str, float],
) -> tuple[str, float, list[str]]:
    """Map topology signals to a class and confidence score.

    Rules are checked in priority order.  mixed_or_ambiguous is the
    fallback — preferred over a brittle wrong classification.

    Returns (topology_class, confidence_0_1, notes).
    """
    notes: list[str] = []
    lp  = signals["left_persistence"]
    rp  = signals["right_persistence"]
    laf = signals["left_area_frac"]
    raf = signals["right_area_frac"]
    lnf = signals["left_narrow_area_frac"]
    rnf = signals["right_narrow_area_frac"]
    waf = signals["wide_area_frac"]

    # ── sidebar_left ─────────────────────────────────────────────────────
    if (
        lp  >= _SIDEBAR_PERSIST_MIN
        and lnf >= _SIDEBAR_SIDE_MIN_AREA
        and lnf <= _SIDEBAR_SIDE_MAX_AREA
        and raf >= _SIDEBAR_MAIN_MIN_AREA
    ):
        confidence = min(1.0, lp * 1.2) * min(1.0, raf / 0.50)
        notes.append(
            f"sidebar_left: left_persistence={lp:.2f}, "
            f"left_narrow_area={lnf:.2f}, right_area={raf:.2f}"
        )
        return TOPOLOGY_SB_LEFT, round(confidence, 3), notes

    # ── sidebar_right ────────────────────────────────────────────────────
    if (
        rp  >= _SIDEBAR_PERSIST_MIN
        and rnf >= _SIDEBAR_SIDE_MIN_AREA
        and rnf <= _SIDEBAR_SIDE_MAX_AREA
        and laf >= _SIDEBAR_MAIN_MIN_AREA
    ):
        confidence = min(1.0, rp * 1.2) * min(1.0, laf / 0.50)
        notes.append(
            f"sidebar_right: right_persistence={rp:.2f}, "
            f"right_narrow_area={rnf:.2f}, left_area={laf:.2f}"
        )
        return TOPOLOGY_SB_RIGHT, round(confidence, 3), notes

    # ── two_column_balanced ──────────────────────────────────────────────
    if (
        laf >= _BALANCED_MIN_EACH
        and raf >= _BALANCED_MIN_EACH
        and abs(laf - raf) < _BALANCED_MAX_IMBAL
        and waf < 0.60    # not dominated by full-width blocks
    ):
        confidence = min(laf, raf) / 0.50 * (1.0 - abs(laf - raf))
        notes.append(
            f"two_column_balanced: left_area={laf:.2f}, "
            f"right_area={raf:.2f}, wide_area={waf:.2f}"
        )
        return TOPOLOGY_BALANCED, round(min(1.0, confidence), 3), notes

    # ── linear_single_column ─────────────────────────────────────────────
    if (
        waf >= _LINEAR_WIDE_MIN
        and lp  < _LINEAR_SIDE_MAX_PERSIST
        and rp  < _LINEAR_SIDE_MAX_PERSIST
    ):
        confidence = min(1.0, waf) * (1.0 - max(lp, rp))
        notes.append(
            f"linear_single_column: wide_area={waf:.2f}, "
            f"left_persist={lp:.2f}, right_persist={rp:.2f}"
        )
        return TOPOLOGY_LINEAR, round(confidence, 3), notes

    # ── mixed_or_ambiguous ───────────────────────────────────────────────
    notes.append(
        f"mixed_or_ambiguous: no class threshold met "
        f"(lp={lp:.2f}, rp={rp:.2f}, laf={laf:.2f}, raf={raf:.2f}, waf={waf:.2f})"
    )
    return TOPOLOGY_AMBIG, 0.40, notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_page_topology(page: PageModel) -> PageTopologyResult:
    """Classify the topology of a single PDF page from its blocks."""
    blocks = _filter_and_normalize(page)
    if not blocks:
        return PageTopologyResult(
            topology=TOPOLOGY_AMBIG,
            signals={"total_blocks": 0.0},
            notes=["No usable blocks on page"],
        )

    signals = _compute_page_signals(blocks)
    topology, _conf, notes = _classify_from_signals(signals)
    return PageTopologyResult(topology=topology, signals=signals, notes=notes)


def classify_topology(doc: ExtractedDoc) -> TopologyClassification:
    """Classify document-level topology by aggregating per-page results.

    Strategy
    --------
    1. Classify each page independently.
    2. Find the most common non-ambiguous topology (the "winner").
    3. Confidence = fraction of pages agreeing × penalty for competing classes.
    4. Return mixed_or_ambiguous when all pages are ambiguous or signals conflict.
    """
    if not doc.pages:
        return TopologyClassification(
            doc_topology=TOPOLOGY_AMBIG,
            confidence=0.0,
            page_topologies=[],
            notes=["No pages in document"],
            signals={},
        )

    page_results = [classify_page_topology(p) for p in doc.pages]
    page_topologies = [r.topology for r in page_results]

    # If all pages ambiguous — no useful signal
    if all(t == TOPOLOGY_AMBIG for t in page_topologies):
        agg = _aggregate_signals([r.signals for r in page_results])
        return TopologyClassification(
            doc_topology=TOPOLOGY_AMBIG,
            confidence=0.30,
            page_topologies=page_topologies,
            notes=["All pages returned ambiguous topology"],
            signals=agg,
        )

    counts = Counter(page_topologies)
    total  = len(page_topologies)

    non_ambig = {t: c for t, c in counts.items() if t != TOPOLOGY_AMBIG}
    winner       = max(non_ambig, key=lambda t: non_ambig[t])
    winner_count = non_ambig[winner]

    # Confidence: agreement fraction penalised for each competing non-ambig class
    competing  = sum(1 for t in non_ambig if t != winner and non_ambig[t] > 0)
    confidence = (winner_count / total) * (1.0 - 0.15 * competing)
    confidence = round(min(1.0, max(0.0, confidence)), 3)

    notes: list[str] = []
    if winner_count < total:
        others = Counter(t for t in page_topologies if t != winner)
        notes.append(f"Page mix: {winner}×{winner_count}, others: {dict(others)}")

    agg = _aggregate_signals([r.signals for r in page_results])
    return TopologyClassification(
        doc_topology=winner,
        confidence=confidence,
        page_topologies=page_topologies,
        notes=notes,
        signals=agg,
    )


def _aggregate_signals(signal_list: list[dict[str, float]]) -> dict[str, float]:
    """Average signal dicts across pages, rounding to 3 dp."""
    if not signal_list:
        return {}
    keys = signal_list[0].keys()
    n    = len(signal_list)
    return {k: round(sum(s.get(k, 0.0) for s in signal_list) / n, 3) for k in keys}


def topology_preservation_score(
    src: TopologyClassification,
    out: TopologyClassification,
) -> tuple[float, list[str]]:
    """Compare source and output topology classifications.

    Returns
    -------
    (score, evidence_list)
        score: 0.0 (collapsed) – 1.0 (perfectly preserved)

    Scoring table
    -------------
    Same class          0.70 + 0.30 × min(src_conf, out_conf)
    Both ambiguous      0.60 flat
    One ambiguous       0.55 flat  (uncertain, soft penalty)
    sidebar flip        0.25
    sidebar↔balanced    0.50
    linear↔sidebar      0.10
    linear↔balanced     0.25
    other cross-class   0.40
    """
    s, o   = src.doc_topology, out.doc_topology
    evidence: list[str] = []

    if s == o:
        if s == TOPOLOGY_AMBIG:
            evidence.append("Both source and output topology are ambiguous")
            return 0.60, evidence
        conf_factor = min(src.confidence, out.confidence)
        score       = 0.70 + 0.30 * conf_factor
        evidence.append(f"Topology preserved: {s} (min_confidence={conf_factor:.2f})")
        return round(score, 3), evidence

    if s == TOPOLOGY_AMBIG or o == TOPOLOGY_AMBIG:
        evidence.append(
            f"Topology comparison uncertain: src={s} (conf={src.confidence:.2f}), "
            f"out={o} (conf={out.confidence:.2f})"
        )
        return 0.55, evidence

    score = _PRESERVATION_SCORES.get((s, o), 0.40)
    evidence.append(f"Topology changed: {s} → {o} (preservation score={score:.2f})")
    return score, evidence

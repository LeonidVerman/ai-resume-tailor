"""Section coherence and stale-content detection for changed-content evaluation.

Evaluates whether the content under each detected section heading matches
the expected structural profile for that section type, and whether old/stale
source content has survived where generated content was expected.

Reuse inventory
---------------
- ExtractedDoc / PageModel / BlockModel  tailor.eval.models     unchanged
- extract_section_anchors()              placement.py            unchanged
- classify_region()                      placement.py            unchanged
- _significant_tokens() pattern          scorer.py               duplicated
  locally to avoid importing a private function across modules
- No changes to extractor, comparator, or same-text pipeline

Algorithm overview
------------------
1. Run extract_section_anchors() on both source and output docs to get
   section heading positions.
2. For each anchor, collect body blocks from that anchor's y-position to
   the next anchor's y-position, within the same visual region.
3. Compute structural features from the collected text lines:
   bullet density, date density, role-marker density, comma density,
   education keyword density, tech keyword density, line length ratios.
4. Score each section against an expected profile for its canonical type.
5. Compute per-section source↔output token overlap as a stale signal.
6. Return SectionCoherenceResult per anchor + overall mean score.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from tailor.eval.models import ExtractedDoc
from tailor.eval.changed_content.placement import (
    SectionAnchor,
    classify_region,
    extract_section_anchors,
)


# ---------------------------------------------------------------------------
# Regex / keyword constants
# ---------------------------------------------------------------------------

_DATE_RE        = re.compile(r"\b(19|20)\d{2}\b")
_ROLE_MARKER_RE = re.compile(r"[|–—]|\bat\b")
_BULLET_RE      = re.compile(r"^\s*[-•·▪▸]\s")
_WORD_RE        = re.compile(r"\b[a-z]+\b")
_SIG_TOKEN_RE   = re.compile(r"\b[a-zA-Z]{6,}\b")

_EDUCATION_KW: frozenset[str] = frozenset({
    "bachelor", "master", "phd", "mba", "degree", "diploma",
    "university", "college", "institute", "faculty",
    "honours", "honors", "graduate", "undergraduate",
})

_TECH_KW: frozenset[str] = frozenset({
    "python", "javascript", "typescript", "golang", "scala",
    "kotlin", "docker", "kubernetes", "terraform", "ansible",
    "postgres", "postgresql", "mongodb", "elasticsearch",
    "kafka", "rabbitmq", "kinesis",
    "serverless", "lambda",
    "react", "angular", "nextjs", "nodejs", "django", "fastapi", "flask",
    "spring", "graphql", "restful",
    "tensorflow", "pytorch", "sklearn", "pandas", "numpy", "airflow",
    "devops", "microservice", "microservices",
})

# Minimum lines for a meaningful coherence score (sections with fewer lines
# return a neutral 0.5 to avoid spurious penalties on tiny sections).
_MIN_LINES = 2

# Token-overlap fraction above which a section is flagged as likely stale.
_STALE_THRESHOLD = 0.70


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SectionFeatures:
    """Structural and content features extracted from a section's text lines."""
    num_lines: int
    char_count: int
    avg_line_length: float
    bullet_count: int
    bullet_density: float         # bullets / num_lines
    date_count: int
    date_density: float           # date occurrences / num_lines
    role_marker_count: int
    role_marker_density: float    # role markers / num_lines
    education_kw_count: int       # distinct education keywords found
    education_kw_density: float   # education_kw_count / num_lines
    tech_kw_count: int            # distinct tech keywords found
    tech_kw_density: float        # tech_kw_count / num_lines
    comma_count: int
    comma_density: float          # commas / num_lines
    semicolon_count: int
    short_line_ratio: float       # fraction of lines < 60 chars
    long_line_ratio: float        # fraction of lines > 120 chars


@dataclass
class SectionCoherenceResult:
    """Coherence analysis for one detected section in the output document."""
    canonical_type: str
    coherence_score: float              # 0-1; how well content matches section type
    feature_scores: dict[str, float]    # per-signal sub-scores that compose the score
    stale_signal: bool                  # True if high token overlap with source section
    stale_overlap: float                # raw src↔out significant-token overlap ratio
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def _significant_tokens(text: str) -> Counter:
    """Counter of significant tokens (length ≥ 6) for overlap comparison."""
    return Counter(t.lower() for t in _SIG_TOKEN_RE.findall(text))


def compute_section_features(lines: list[str]) -> SectionFeatures:
    """Compute structural + content features for a list of text lines.

    All density metrics use num_lines as the denominator so they stay
    comparable across sections of different sizes.
    """
    n = len(lines)
    if n == 0:
        return SectionFeatures(
            num_lines=0, char_count=0, avg_line_length=0.0,
            bullet_count=0, bullet_density=0.0,
            date_count=0, date_density=0.0,
            role_marker_count=0, role_marker_density=0.0,
            education_kw_count=0, education_kw_density=0.0,
            tech_kw_count=0, tech_kw_density=0.0,
            comma_count=0, comma_density=0.0,
            semicolon_count=0,
            short_line_ratio=0.0, long_line_ratio=0.0,
        )

    total_chars = sum(len(ln) for ln in lines)

    bullet_count   = sum(1 for ln in lines if _BULLET_RE.match(ln))
    date_count     = sum(len(_DATE_RE.findall(ln)) for ln in lines)
    role_count     = sum(len(_ROLE_MARKER_RE.findall(ln)) for ln in lines)
    comma_count    = sum(ln.count(",") for ln in lines)
    semi_count     = sum(ln.count(";") for ln in lines)
    short_count    = sum(1 for ln in lines if len(ln) < 60)
    long_count     = sum(1 for ln in lines if len(ln) > 120)

    # Education and tech keyword detection (word-boundary matching)
    full_lower = " ".join(lines).lower()
    words = set(_WORD_RE.findall(full_lower))
    edu_kw_count  = len(words & _EDUCATION_KW)
    tech_kw_count = len(words & _TECH_KW)

    return SectionFeatures(
        num_lines=n,
        char_count=total_chars,
        avg_line_length=round(total_chars / n, 1),
        bullet_count=bullet_count,
        bullet_density=round(bullet_count / n, 3),
        date_count=date_count,
        date_density=round(date_count / n, 3),
        role_marker_count=role_count,
        role_marker_density=round(role_count / n, 3),
        education_kw_count=edu_kw_count,
        education_kw_density=round(edu_kw_count / n, 3),
        tech_kw_count=tech_kw_count,
        tech_kw_density=round(tech_kw_count / n, 3),
        comma_count=comma_count,
        comma_density=round(comma_count / n, 3),
        semicolon_count=semi_count,
        short_line_ratio=round(short_count / n, 3),
        long_line_ratio=round(long_count / n, 3),
    )


# ---------------------------------------------------------------------------
# Per-section-type coherence scorers
# ---------------------------------------------------------------------------

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _score_experience(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Experience: should have dates, role markers, bullets; not skills-list-like."""
    fs: dict[str, float] = {}

    # Dates: expect 0.05–0.40 per line (year ranges for job periods)
    if f.date_density >= 0.05:
        fs["date"] = _clamp(1.0 - max(0.0, f.date_density - 0.40) / 0.60)
    else:
        fs["date"] = f.date_density / 0.05

    # Bullets: expect 0.20–0.70 (achievement statements)
    if f.bullet_density >= 0.20:
        fs["bullet"] = min(1.0, f.bullet_density / 0.30)
    else:
        fs["bullet"] = f.bullet_density / 0.20

    # Role markers: company | role separators (expect ≥ 0.15 per line)
    fs["role_marker"] = min(1.0, f.role_marker_density / 0.15)

    # Not a skills-list: low comma density (< 1.0 per line is fine)
    fs["not_skills"] = _clamp(1.0 - f.comma_density / 1.5)

    # Not education-heavy: penalise edu keyword density
    fs["not_edu"] = _clamp(1.0 - f.education_kw_density * 5)

    # Has depth (≥ 6 lines for a real experience section)
    fs["content_depth"] = min(1.0, f.num_lines / 6)

    weights = {
        "date": 0.28, "bullet": 0.22, "role_marker": 0.20,
        "not_skills": 0.12, "not_edu": 0.08, "content_depth": 0.10,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


def _score_education(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Education: should have edu keywords, some dates; not bullet-heavy."""
    fs: dict[str, float] = {}

    # Education keywords (strongest signal)
    fs["edu_keywords"] = min(1.0, f.education_kw_count / 2.0)

    # Some dates (graduation year): expect 0.05–0.35 per line
    if f.date_density <= 0.35:
        fs["date"] = min(1.0, f.date_density / 0.15)
    else:
        fs["date"] = _clamp(1.5 - f.date_density / 0.30)

    # Not bullet-heavy (education sections use prose / short entries)
    fs["not_bullets"] = _clamp(1.0 - f.bullet_density / 0.30)

    # Not a skills list
    fs["not_skills"] = _clamp(1.0 - f.comma_density / 1.5)

    # Not experience-like (few role markers)
    fs["not_experience"] = _clamp(1.0 - f.role_marker_density * 4)

    weights = {
        "edu_keywords": 0.42, "date": 0.20, "not_bullets": 0.15,
        "not_skills": 0.13, "not_experience": 0.10,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


def _score_skills(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Technical skills: comma-separated lists, short lines, tech keywords, no dates."""
    fs: dict[str, float] = {}

    # High comma density (comma-separated skill items)
    fs["comma"] = min(1.0, f.comma_density / 2.0)

    # Tech keywords present
    fs["tech_kw"] = min(1.0, f.tech_kw_density / 0.5)

    # Short lines (categories or bullet items)
    fs["short_lines"] = f.short_line_ratio

    # Minimal dates
    fs["no_dates"] = _clamp(1.0 - f.date_density / 0.10)

    # Minimal role markers
    fs["no_role_markers"] = _clamp(1.0 - f.role_marker_density / 0.10)

    weights = {
        "comma": 0.30, "tech_kw": 0.28, "short_lines": 0.20,
        "no_dates": 0.12, "no_role_markers": 0.10,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


def _score_languages(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Languages: very compact, short lines, no dates, no bullets, no role markers."""
    fs: dict[str, float] = {}

    # Compact section (typically 2–6 lines)
    fs["compact"] = min(1.0, 8 / max(1, f.num_lines))

    # Short lines (language + level pairs)
    fs["short_lines"] = f.short_line_ratio

    # No dates
    fs["no_dates"] = _clamp(1.0 - f.date_density / 0.10)

    # No role markers
    fs["no_role_markers"] = _clamp(1.0 - f.role_marker_density / 0.10)

    # No bullets
    fs["no_bullets"] = _clamp(1.0 - f.bullet_density / 0.20)

    weights = {
        "compact": 0.28, "short_lines": 0.27, "no_dates": 0.20,
        "no_role_markers": 0.13, "no_bullets": 0.12,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


def _score_certifications(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Certifications: compact, short entries, occasional year, no role markers."""
    fs: dict[str, float] = {}

    # Compact (2–8 lines typically)
    fs["compact"] = min(1.0, 10 / max(1, f.num_lines))

    # Occasional year (0.0–0.50 per line)
    if f.date_density <= 0.50:
        fs["date"] = min(1.0, f.date_density / 0.25)
    else:
        fs["date"] = 0.5

    # Short lines (cert name, issuer, year)
    fs["short_lines"] = f.short_line_ratio

    # No role markers (not experience-like)
    fs["no_role_markers"] = _clamp(1.0 - f.role_marker_density / 0.10)

    # Not a skills comma-list
    fs["not_skills_list"] = _clamp(1.0 - f.comma_density / 2.0)

    weights = {
        "compact": 0.25, "date": 0.20, "short_lines": 0.20,
        "no_role_markers": 0.20, "not_skills_list": 0.15,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


def _score_summary(f: SectionFeatures) -> tuple[float, dict[str, float]]:
    """Summary / profile: prose paragraphs, no bullets, no dates, no role markers."""
    fs: dict[str, float] = {}

    # Has some content
    fs["content"] = min(1.0, f.num_lines / 3) if f.num_lines <= 8 else 0.7

    # Prose lines (long lines preferred)
    fs["long_lines"] = 1.0 - f.short_line_ratio

    # Not bullet-heavy
    fs["not_bullets"] = _clamp(1.0 - f.bullet_density / 0.30)

    # Minimal role markers
    fs["no_role_markers"] = _clamp(1.0 - f.role_marker_density * 4)

    # Not date-heavy (a few dates OK)
    fs["not_date_heavy"] = _clamp(1.0 - f.date_density / 0.30)

    weights = {
        "content": 0.20, "long_lines": 0.28, "not_bullets": 0.22,
        "no_role_markers": 0.15, "not_date_heavy": 0.15,
    }
    score = sum(fs[k] * weights[k] for k in weights)
    return _clamp(score), fs


_SCORERS = {
    "experience":    _score_experience,
    "education":     _score_education,
    "skills":        _score_skills,
    "languages":     _score_languages,
    "certifications": _score_certifications,
    "summary":       _score_summary,
}


def score_section_coherence(
    features: SectionFeatures,
    canonical_type: str,
) -> tuple[float, dict[str, float]]:
    """Score how well *features* match the expected profile for *canonical_type*.

    Returns (coherence_score_0_1, per_signal_sub_scores).
    Returns neutral (0.5, {}) for unknown section types or sections with
    too few lines to score meaningfully.
    """
    if features.num_lines < _MIN_LINES:
        return 0.5, {"note_insufficient_lines": 0.5}

    scorer = _SCORERS.get(canonical_type)
    if scorer is None:
        return 0.5, {}

    return scorer(features)


# ---------------------------------------------------------------------------
# Section content extraction
# ---------------------------------------------------------------------------

def _extract_section_content(
    doc: ExtractedDoc,
    anchor: SectionAnchor,
    next_anchor: SectionAnchor | None,
) -> list[str]:
    """Collect text lines for the section starting at *anchor*.

    Spans from the heading block (excluded) to the next anchor (excluded),
    filtering blocks to the same visual region as the heading.

    Returns an empty list for documents whose extractor output has no blocks,
    or for sections whose body has no text.
    """
    lines: list[str] = []

    for page in doc.pages:
        pn = page.page_number
        if pn < anchor.page:
            continue
        if next_anchor and pn > next_anchor.page:
            break  # past the boundary page — stop scanning pages

        pw, ph = page.width, page.height
        if pw <= 0 or ph <= 0:
            continue

        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox
            ny0 = y0 / ph
            nx0 = x0 / pw
            nx1 = x1 / pw
            ny1 = y1 / ph

            # Skip blocks at or above the heading on the heading's page
            if pn == anchor.page and ny0 <= anchor.ny0:
                continue

            # Skip blocks at or beyond the next anchor on its page
            if next_anchor and pn == next_anchor.page and ny0 >= next_anchor.ny0:
                continue

            # Region filter: include only blocks in the same visual region
            block_region = classify_region(nx0, ny0, nx1, ny1)
            if anchor.region == "main":
                # Main section: skip sidebar blocks to avoid contamination
                if block_region in ("sidebar_left", "sidebar_right"):
                    continue
            else:
                # Sidebar section: require exact region match
                if block_region != anchor.region:
                    continue

            for line in block.lines:
                text = line.text.strip()
                if text:
                    lines.append(text)

    return lines


# ---------------------------------------------------------------------------
# Stale overlap helper
# ---------------------------------------------------------------------------

def _section_overlap(src_lines: list[str], out_lines: list[str]) -> float:
    """Fraction of significant source tokens that appear in the output section.

    Returns 0.0 if source section is empty.
    A value ≥ 0.70 indicates the output likely contains stale source content.
    """
    if not src_lines:
        return 0.0

    src_tok = _significant_tokens(" ".join(src_lines))
    out_tok = _significant_tokens(" ".join(out_lines))

    total = sum(src_tok.values())
    if total == 0:
        return 0.0

    matching = sum(min(cnt, out_tok.get(tok, 0)) for tok, cnt in src_tok.items())
    return matching / total


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_coherence(
    src_extracted: ExtractedDoc,
    out_extracted: ExtractedDoc,
) -> tuple[list[SectionCoherenceResult], float]:
    """Compute per-section coherence scores and stale-content signals.

    Parameters
    ----------
    src_extracted:  ExtractedDoc for the source/template PDF.
    out_extracted:  ExtractedDoc for the generated output PDF.

    Returns
    -------
    (results, overall_score)
        results        — one SectionCoherenceResult per detected output section
        overall_score  — mean coherence_score across all results, or 1.0 when
                         no section anchors are found (no geometric data)
    """
    src_anchors = extract_section_anchors(src_extracted)
    out_anchors = extract_section_anchors(out_extracted)

    # No heading blocks in output → no geometric section data available
    if not out_anchors:
        return [], 1.0

    # Collect source section content (for stale detection)
    src_content: dict[str, list[str]] = {}
    for i, anchor in enumerate(src_anchors):
        next_a = src_anchors[i + 1] if i + 1 < len(src_anchors) else None
        content = _extract_section_content(src_extracted, anchor, next_a)
        src_content.setdefault(anchor.canonical_type, []).extend(content)

    # Score each detected output section
    results: list[SectionCoherenceResult] = []
    for i, anchor in enumerate(out_anchors):
        next_a = out_anchors[i + 1] if i + 1 < len(out_anchors) else None
        out_lines = _extract_section_content(out_extracted, anchor, next_a)

        features  = compute_section_features(out_lines)
        coherence, feature_scores = score_section_coherence(features, anchor.canonical_type)

        # Stale detection: compare with source section by token overlap
        src_lines = src_content.get(anchor.canonical_type, [])
        overlap   = _section_overlap(src_lines, out_lines)
        stale     = (
            overlap >= _STALE_THRESHOLD
            and len(src_lines) >= _MIN_LINES
            and len(out_lines) >= _MIN_LINES
        )

        notes: list[str] = []
        if stale:
            notes.append(
                f"High source overlap ({overlap:.0%}) — possible stale content"
            )
        if coherence < 0.50 and features.num_lines >= _MIN_LINES:
            notes.append(
                f"Low coherence ({coherence:.2f}) — content may not match "
                f"'{anchor.canonical_type}' profile"
            )

        results.append(SectionCoherenceResult(
            canonical_type=anchor.canonical_type,
            coherence_score=round(coherence, 3),
            feature_scores={k: round(v, 3) for k, v in feature_scores.items()},
            stale_signal=stale,
            stale_overlap=round(overlap, 3),
            notes=notes,
        ))

    overall = sum(r.coherence_score for r in results) / len(results) if results else 1.0
    return results, round(overall, 3)

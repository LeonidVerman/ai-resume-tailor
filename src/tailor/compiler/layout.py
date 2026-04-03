"""Layout-aware post-processing for DOCX resume generation.

Sits between parse_llm_output() and apply_tailored().  Normalises the LLM
section list so it fits the source template's visual topology without
requiring changes to the renderer or the core IR.

Pipeline integration (pipeline.py):
    original     = parse_docx(template_path)
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(original, llm_sections)   # ← new
    updated      = apply_tailored(original, llm_sections)
    render_docx(updated, template_path, output_path)

Phases implemented
------------------
1. Template classification: "linear" | "table_sidebar"
2. Container topology extraction (capacity, region, subkind)
3. Additional redistribution — extracts labeled lines (Languages: …,
   Certifications: …) from Technical Skills and routes them to dedicated
   source sections when those sections exist in the template.
4. Fit estimation — rough char/para ratio per section.
5. Compaction — summary (sentence-level), skills (line-level).
6. Validation / guardrails — internal-marker leaks, duplicate sections,
   Additional-inside-Skills when dedicated containers exist.

Deferred
--------
- Sidebar column-width measurement from table cell XML (uses para-count
  proxy for now).
- Bullet-level experience compaction (experience is left untouched;
  the LLM is expected to output the right bullet count).
- Education overflow handling (typically compact naturally).
"""
from __future__ import annotations

import logging
import re
import sys
from dataclasses import dataclass, field
from typing import Literal

from tailor.compiler.models import ResumeDocument, ResumeSection, TableBlock
from tailor.compiler.text_parser import LlmRole, LlmSection

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template classification
# ---------------------------------------------------------------------------

TemplateClass = Literal["linear", "table_sidebar"]


def classify_template(original: ResumeDocument) -> TemplateClass:
    """Return 'table_sidebar' when the document body uses table-based layout."""
    if original.body_items and any(
        isinstance(i, TableBlock) for i in original.body_items
    ):
        return "table_sidebar"
    return "linear"


# ---------------------------------------------------------------------------
# Container model
# ---------------------------------------------------------------------------

_LANG_NAMES: frozenset[str] = frozenset({"languages", "language"})
_CERT_NAMES: frozenset[str] = frozenset({
    "certifications", "certification", "certifications and training",
    "licenses", "licences",
})
_LINK_NAMES: frozenset[str] = frozenset({
    "websites", "profiles", "links", "online", "portfolio",
    "social", "github",
})
# Word-level fallback for compound headings like "Websites, Portfolios, Profiles".
_LINK_WORDS: frozenset[str] = frozenset({
    "website", "websites", "portfolio", "portfolios",
    "profile", "profiles", "links", "online", "social", "github",
})
_AWARD_NAMES: frozenset[str] = frozenset({"awards", "honors", "honours", "achievements"})


def _section_subkind(title: str) -> str:
    """Classify a source section into a finer subkind for topology mapping.

    Returns one of: "languages", "certifications", "links", "awards", "".
    """
    t = title.strip().lower()
    if t in _LANG_NAMES:
        return "languages"
    if t in _CERT_NAMES:
        return "certifications"
    if t in _LINK_NAMES:
        return "links"
    if t in _AWARD_NAMES:
        return "awards"
    # Word-level fallback for compound headings like "Websites, Portfolios, Profiles"
    words = re.split(r"[\s/&,;]+", t)
    if any(w in _LINK_WORDS for w in words if w):
        return "links"
    return ""


@dataclass
class TemplateContainer:
    """Capacity metadata for one source section.

    Used to guide compaction and fit estimation; never mutated.
    """
    section_idx: int        # index in original.sections
    title: str              # raw heading text
    semantic_type: str      # 'experience'|'summary'|'skills'|'education'|'other'
    subkind: str            # ""  |  "languages" | "certifications" | "links" | "awards"
    region: str             # "main" | "sidebar" | "unknown"
    orig_para_count: int    # number of non-empty body paragraphs
    orig_char_count: int    # total character count of non-empty body text
    is_narrow: bool         # heuristic: few/short paras → narrow / sidebar


def _is_narrow(section: ResumeSection) -> bool:
    """Heuristic: a section with few short body lines is likely narrow/sidebar."""
    non_empty = [p for p in section.body_paras if p.text.strip()]
    if not non_empty:
        return False
    total_chars = sum(len(p.text) for p in non_empty)
    return len(non_empty) <= 8 and total_chars <= 300


def extract_containers(original: ResumeDocument) -> list[TemplateContainer]:
    """Extract per-section capacity metadata from the source document."""
    containers: list[TemplateContainer] = []
    for idx, section in enumerate(original.sections):
        non_empty = [p for p in section.body_paras if p.text.strip()]
        total_chars = sum(len(p.text) for p in non_empty)
        subkind = _section_subkind(section.title)
        narrow = _is_narrow(section)

        if section.semantic_type == "experience":
            region = "main"
        elif section.semantic_type == "summary":
            region = "main"
        elif subkind:
            # Dedicated blocks (Languages / Certs / Links / Awards) are sidebar
            region = "sidebar"
        elif section.semantic_type == "skills":
            region = "sidebar" if narrow else "main"
        else:
            region = "unknown"

        containers.append(TemplateContainer(
            section_idx=idx,
            title=section.title,
            semantic_type=section.semantic_type,
            subkind=subkind,
            region=region,
            orig_para_count=len(non_empty),
            orig_char_count=total_chars,
            is_narrow=narrow,
        ))
    return containers


# ---------------------------------------------------------------------------
# Fit estimation
# ---------------------------------------------------------------------------

@dataclass
class FitScore:
    char_ratio: float   # new_chars / max(1, orig_chars)
    para_ratio: float   # new_paras / max(1, orig_paras)
    risk: str           # "low" | "medium" | "high"


# Ratio thresholds; narrow containers are penalised more aggressively.
_NARROW_MEDIUM_RATIO = 1.2
_NARROW_HIGH_RATIO   = 1.5
_WIDE_MEDIUM_RATIO   = 1.8
_WIDE_HIGH_RATIO     = 2.5


def estimate_fit(container: TemplateContainer, llm_section: LlmSection) -> FitScore:
    """Rough fit estimate for placing *llm_section* into *container*."""
    if llm_section.roles:
        # Experience: flatten to bullet-level line count + chars
        new_paras = sum(1 + len(r.bullets) for r in llm_section.roles)
        new_chars = sum(
            len(r.header) + sum(len(b) for b in r.bullets)
            for r in llm_section.roles
        )
    else:
        non_empty = [l for l in llm_section.body_lines if l.strip()]
        new_paras = len(non_empty)
        new_chars = sum(len(l) for l in non_empty)

    char_ratio = new_chars / max(1, container.orig_char_count)
    para_ratio = new_paras / max(1, container.orig_para_count)

    if container.is_narrow:
        med, hi = _NARROW_MEDIUM_RATIO, _NARROW_HIGH_RATIO
    else:
        med, hi = _WIDE_MEDIUM_RATIO, _WIDE_HIGH_RATIO

    if char_ratio >= hi or para_ratio >= hi:
        risk = "high"
    elif char_ratio >= med or para_ratio >= med:
        risk = "medium"
    else:
        risk = "low"

    return FitScore(char_ratio=char_ratio, para_ratio=para_ratio, risk=risk)


# ---------------------------------------------------------------------------
# Compactors
# ---------------------------------------------------------------------------

_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+')


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences on '. ', '! ', '? '."""
    return [s.strip() for s in _SENTENCE_END_RE.split(text.strip()) if s.strip()]


def compact_summary(body_lines: list[str], max_sentences: int = 3) -> list[str]:
    """Reduce summary body lines to at most *max_sentences* sentences.

    Joins all lines into a single prose string, splits on sentence
    boundaries, keeps the first (max_sentences - 1) sentences plus the
    final sentence (preserves opening hook and closing positioning line).
    Returns *body_lines* unchanged when already within limit.
    """
    full_text = " ".join(l for l in body_lines if l.strip())
    sentences = _split_sentences(full_text)
    if len(sentences) <= max_sentences:
        return body_lines
    # First N-1 sentences + last sentence
    kept = sentences[: max_sentences - 1] + [sentences[-1]]
    return [" ".join(kept)]


def compact_skills(body_lines: list[str], target_count: int) -> list[str]:
    """Reduce skills body lines to *target_count* non-empty lines.

    Drops trailing lines first (lowest-priority tail items).
    Returns *body_lines* unchanged when already within target.
    """
    non_empty = [l for l in body_lines if l.strip()]
    if len(non_empty) <= target_count:
        return body_lines
    return non_empty[:target_count]


# ---------------------------------------------------------------------------
# Additional redistribution
# ---------------------------------------------------------------------------

# Patterns that signal "this line belongs to an Additional subgroup" when
# found inside the Technical Skills section.
_LANG_LABEL_RE = re.compile(
    r'^(?:languages?|spoken\s+languages?)\s*[:：]\s*', re.IGNORECASE
)
_CERT_LABEL_RE = re.compile(
    r'^(?:certifications?(?:\s+and\s+training)?|licenses?|licences?)\s*[:：]\s*',
    re.IGNORECASE,
)
_LINK_LABEL_RE = re.compile(
    r'^(?:websites?|profiles?|links?|github|linkedin|portfolio|online)\s*[:：]\s*',
    re.IGNORECASE,
)
_AWARD_LABEL_RE = re.compile(
    r'^(?:awards?|honors?|honours?|achievements?)\s*[:：]\s*',
    re.IGNORECASE,
)

_LABEL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (_LANG_LABEL_RE,   "languages"),
    (_CERT_LABEL_RE,   "certifications"),
    (_LINK_LABEL_RE,   "links"),
    (_AWARD_LABEL_RE,  "awards"),
]


def _classify_line_subkind(line: str) -> str:
    """Return the subkind if the line starts with a recognized label, else ''."""
    for pattern, subkind in _LABEL_PATTERNS:
        if pattern.match(line.strip()):
            return subkind
    return ""


def extract_additional_subgroups(
    body_lines: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    """Split *body_lines* into (clean_skills_lines, subgroups_dict).

    Lines that start with a recognized Additional label
    (``Languages: …``, ``Certifications: …``, etc.) are extracted into
    *subgroups_dict* keyed by subkind, and removed from clean_skills_lines.

    Example::

        clean, subs = extract_additional_subgroups([
            "Python, Java",
            "Languages: English, French",
            "Certifications: AWS",
        ])
        # clean  = ["Python, Java"]
        # subs   = {"languages": ["English, French"], "certifications": ["AWS"]}
    """
    clean: list[str] = []
    subgroups: dict[str, list[str]] = {}

    for line in body_lines:
        for pattern, subkind in _LABEL_PATTERNS:
            if pattern.match(line.strip()):
                value = pattern.sub("", line.strip()).strip()
                if value:
                    subgroups.setdefault(subkind, []).append(value)
                break
        else:
            clean.append(line)

    return clean, subgroups


def _find_container_for_subkind(
    subkind: str,
    containers: list[TemplateContainer],
) -> TemplateContainer | None:
    for c in containers:
        if c.subkind == subkind:
            return c
    return None


def _find_llm_section_idx(heading: str, sections: list[LlmSection]) -> int | None:
    """Return index of the section whose heading matches *heading* (case-insensitive)."""
    t = heading.strip().lower()
    for i, s in enumerate(sections):
        if s.heading.strip().lower() == t:
            return i
    return None


def redistribute_additional(
    llm_sections: list[LlmSection],
    containers: list[TemplateContainer],
) -> list[LlmSection]:
    """Move Additional subgroup content from Technical Skills to dedicated sections.

    If the source template has dedicated blocks for Languages / Certifications /
    Links / Awards, and those items appear (with label prefixes) inside the
    LLM-generated Technical Skills section, this function:

    1. Strips the labeled lines from Technical Skills.
    2. Injects or merges them into the matching LLM section (if already
       present) or inserts a new LlmSection (using the source container title
       as the heading) immediately after Technical Skills.

    When no dedicated source container exists for a subkind, the extracted
    value is placed back in Technical Skills as a plain (label-free) line.

    Returns a modified list; input is never mutated.
    """
    skills_idx: int | None = None
    for i, s in enumerate(llm_sections):
        if s.semantic_type == "skills":
            skills_idx = i
            break
    if skills_idx is None:
        return llm_sections

    skills_section = llm_sections[skills_idx]
    clean_lines, subgroups = extract_additional_subgroups(skills_section.body_lines)

    if not subgroups:
        return llm_sections

    result = list(llm_sections)

    # Replace Technical Skills with the clean (label-stripped) version
    result[skills_idx] = LlmSection(
        heading=skills_section.heading,
        semantic_type=skills_section.semantic_type,
        body_lines=clean_lines,
        roles=skills_section.roles,
    )

    insert_after = skills_idx  # cursor for sequential inserts

    # Locked section types are preserved verbatim by apply_tailored and never
    # accept LLM content.  Routing extracted subgroup lines to a locked container
    # would silently discard them (e.g. "Languages: Java, Python" routed to the
    # natural-language "Languages" section which is locked).  Keep them in
    # Technical Skills instead, exactly like the "no dedicated container" case.
    _LOCKED_TYPES = frozenset({"education", "certifications", "languages", "websites"})

    for subkind, lines in subgroups.items():
        dedicated = _find_container_for_subkind(subkind, containers)
        if dedicated is None or dedicated.semantic_type in _LOCKED_TYPES:
            # No dedicated container, or the container is locked (verbatim-only).
            # Return the content to Technical Skills as plain lines.
            cur = result[skills_idx]
            result[skills_idx] = LlmSection(
                heading=cur.heading,
                semantic_type=cur.semantic_type,
                body_lines=cur.body_lines + lines,
                roles=cur.roles,
            )
            continue

        existing_idx = _find_llm_section_idx(dedicated.title, result)
        if existing_idx is not None:
            # Merge extracted lines into the existing LLM section
            existing = result[existing_idx]
            result[existing_idx] = LlmSection(
                heading=existing.heading,
                semantic_type=existing.semantic_type,
                body_lines=existing.body_lines + lines,
                roles=existing.roles,
            )
        else:
            # Inject a new LlmSection using the source container's title
            insert_after += 1
            result.insert(
                insert_after,
                LlmSection(
                    heading=dedicated.title,
                    semantic_type=dedicated.semantic_type,
                    body_lines=lines,
                    roles=[],
                ),
            )

    return result


# ---------------------------------------------------------------------------
# Validation / guardrails
# ---------------------------------------------------------------------------

_INTERNAL_MARKERS: list[str] = [
    "CURRENT_DATE",
    "{{",
    "}}",
    "__TEMPLATE__",
    "Generated on",
]

# Trigger ratio for proactive compaction (new / orig > threshold → compact)
_COMPACT_TRIGGER_RATIO = 1.4

# Maximum sentences for a summary in a narrow container
_SUMMARY_MAX_SENTENCES_NARROW = 3


def validate_llm_sections(
    llm_sections: list[LlmSection],
    containers: list[TemplateContainer],
) -> list[str]:
    """Return a list of warning strings (empty list = clean).

    Checks
    ------
    * Internal marker leakage (CURRENT_DATE, {{ … }} etc.)
    * Duplicate semantic sections (two experience sections, etc.)
    * Additional subgroup content still embedded in Technical Skills when
      a dedicated source container exists (leakage after redistribution).
    """
    warnings: list[str] = []

    # Internal marker leak
    for section in llm_sections:
        all_text_lines = list(section.body_lines)
        for role in section.roles:
            all_text_lines.append(role.header)
            all_text_lines.extend(role.bullets)
        for line in all_text_lines:
            for marker in _INTERNAL_MARKERS:
                if marker in line:
                    warnings.append(
                        f"Internal marker leak: '{marker}' in section "
                        f"'{section.heading}'"
                    )

    # Duplicate semantic types
    seen: dict[str, str] = {}
    for section in llm_sections:
        st = section.semantic_type
        if st in ("experience", "summary", "skills", "education"):
            if st in seen:
                warnings.append(
                    f"Duplicate '{st}' section: '{seen[st]}' and "
                    f"'{section.heading}'"
                )
            else:
                seen[st] = section.heading

    # Additional content still inside Technical Skills after redistribution
    has_lang_container  = any(c.subkind == "languages"      for c in containers)
    has_cert_container  = any(c.subkind == "certifications"  for c in containers)
    has_link_container  = any(c.subkind == "links"           for c in containers)
    has_award_container = any(c.subkind == "awards"          for c in containers)

    for section in llm_sections:
        if section.semantic_type != "skills":
            continue
        for line in section.body_lines:
            if has_lang_container and _LANG_LABEL_RE.match(line.strip()):
                warnings.append(
                    "Languages content embedded in Technical Skills despite "
                    "dedicated Languages container"
                )
            if has_cert_container and _CERT_LABEL_RE.match(line.strip()):
                warnings.append(
                    "Certifications content embedded in Technical Skills despite "
                    "dedicated Certifications container"
                )
            if has_link_container and _LINK_LABEL_RE.match(line.strip()):
                warnings.append(
                    "Links content embedded in Technical Skills despite "
                    "dedicated Links container"
                )
            if has_award_container and _AWARD_LABEL_RE.match(line.strip()):
                warnings.append(
                    "Awards content embedded in Technical Skills despite "
                    "dedicated Awards container"
                )

    return warnings


# ---------------------------------------------------------------------------
# A: Implicit Summary anchoring
# ---------------------------------------------------------------------------

_YEAR_RE_LOCAL = re.compile(r"\b(19|20)\d{2}\b")


def _has_intro_prose_content(section: "ResumeSection") -> bool:
    """Return True when a section's body looks like a prose intro (not a skills list).

    A section qualifies as an intro-prose candidate when:
    - It has at least 30 characters of non-empty body text.
    - Content is not exclusively URLs / short tokens (no spaces).
    - Comma density is low (< 0.15 commas per character) — rules out skills lists.
    """
    non_empty = [p.text.strip() for p in section.body_paras if p.text.strip()]
    if not non_empty:
        return False
    total_text = " ".join(non_empty)
    if len(total_text) < 30:
        return False
    # Exclude sections whose every non-empty line is a URL or has no whitespace
    # (e.g. a Websites section containing only "linkedin.com/in/…" links).
    if all("://" in p or " " not in p for p in non_empty):
        return False
    comma_density = total_text.count(",") / max(1, len(total_text))
    return comma_density < 0.15


def _find_summary_anchor(
    original: "ResumeDocument",
    containers: list[TemplateContainer],
) -> "tuple[str, int] | None":
    """Return (anchor_type, section_idx) for implicit summary placement, or None.

    anchor_type values:
    - 'intro_prose': section at section_idx should be replaced by the summary.
    - 'hero': insert summary as first section (header_paras act as the hero block).

    Sidebar sections are excluded from consideration.
    """
    sidebar_idxs = {c.section_idx for c in containers if c.region == "sidebar"}

    # Rule 1: first 'other'-type top section that contains prose (not skills-like).
    # Stop searching once we reach a real content section (summary/experience/etc.)
    # so we only look at the header/intro zone.
    for idx, section in enumerate(original.sections):
        if idx in sidebar_idxs:
            continue
        if section.semantic_type != "other":
            break  # passed the header zone into main content
        if _has_intro_prose_content(section):
            return ("intro_prose", idx)

    # Rule 2: hero/title block exists (non-empty header_paras).
    if original.header_paras:
        return ("hero", -1)

    return None


def _anchor_implicit_summary(
    llm_sections: list[LlmSection],
    original: "ResumeDocument",
    containers: list[TemplateContainer],
) -> list[LlmSection]:
    """Ensure an LLM summary is anchored to a sensible location in the template.

    No-op when:
    - The template already has an explicit summary section.
    - The LLM output has no summary section.
    - No suitable anchor is found in the template.

    When anchor_type is 'intro_prose': the LLM summary heading is renamed to
    match the intro-prose section's title so that _match_sections pairs them
    via exact heading match (pass 1).  _update_body_section then replaces the
    intro-prose body with the generated summary content.

    When anchor_type is 'hero': the LLM summary is moved to position 0 in the
    list so it appears first in LLM-output order after any verbatim sections.

    Input list is never mutated; a new list is returned.
    """
    # No-op if template already has an explicit summary section
    if any(s.semantic_type == "summary" for s in original.sections):
        return llm_sections

    # Find LLM summary
    sum_idx = next(
        (i for i, s in enumerate(llm_sections) if s.semantic_type == "summary"),
        None,
    )
    if sum_idx is None:
        return llm_sections

    anchor = _find_summary_anchor(original, containers)
    if anchor is None:
        return llm_sections

    anchor_type, section_idx = anchor
    result = list(llm_sections)
    llm_sum = result[sum_idx]

    if anchor_type == "intro_prose":
        # Rename the LLM summary heading to match the intro-prose section title
        # exactly.  _match_sections will pair them in pass 1 (exact heading match),
        # and _update_body_section will replace the prose body with the summary.
        orig_title = original.sections[section_idx].title
        result[sum_idx] = LlmSection(
            heading=orig_title,
            semantic_type=llm_sum.semantic_type,
            body_lines=llm_sum.body_lines,
            roles=llm_sum.roles,
        )
        log.debug(
            "anchor_implicit_summary: renamed LLM summary heading to %r "
            "(intro_prose replacement)",
            orig_title,
        )
    elif anchor_type == "hero":
        # Move summary to position 0 so it appears first after any verbatim sections.
        result.pop(sum_idx)
        result.insert(0, llm_sum)
        log.debug("anchor_implicit_summary: moved summary to position 0 (hero anchor)")

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_source_section(
    llm_s: LlmSection,
    sections: list[ResumeSection],
) -> ResumeSection | None:
    """Find the best matching source section for an LLM section."""
    # Pass 1: exact heading match (case-insensitive)
    for s in sections:
        if s.title.lower() == llm_s.heading.lower():
            return s
    # Pass 2: semantic type match
    if llm_s.semantic_type != "other":
        for s in sections:
            if s.semantic_type == llm_s.semantic_type:
                return s
    return None


def _find_container_by_title(
    section: ResumeSection,
    containers: list[TemplateContainer],
) -> TemplateContainer | None:
    for c in containers:
        if c.title.lower() == section.title.lower():
            return c
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_layout_fitting(
    original: ResumeDocument,
    llm_sections: list[LlmSection],
) -> list[LlmSection]:
    """Normalise *llm_sections* for layout-aware rendering against *original*.

    Steps
    -----
    1. Classify template (linear / table_sidebar).
    2. Extract source container capacities.
    3. Redistribute Additional subgroup content (Languages / Certifications /
       Links) from Technical Skills to dedicated source containers.
    4. Compact oversized sections for narrow containers:
       - Summary → sentence-level compaction.
       - Skills   → line-level (tail-drop) compaction.
    5. Validate and log warnings.

    Returns a (possibly modified) list of LlmSection objects.
    The *original* ResumeDocument is never modified.
    """
    tpl_class  = classify_template(original)
    containers = extract_containers(original)

    log.debug("apply_layout_fitting: template_class=%s, containers=%d",
              tpl_class, len(containers))

    # Step 2.5: A — implicit summary anchoring for templates without an
    # explicit summary section.
    result = _anchor_implicit_summary(llm_sections, original, containers)

    # Step 3: Additional redistribution
    result = redistribute_additional(result, containers)

    # Step 4: Compaction for narrow containers
    compacted: list[LlmSection] = []
    for llm_s in result:
        orig_section = _find_source_section(llm_s, original.sections)
        if orig_section is None:
            compacted.append(llm_s)
            continue

        container = _find_container_by_title(orig_section, containers)
        if container is None:
            compacted.append(llm_s)
            continue

        fit = estimate_fit(container, llm_s)

        if llm_s.semantic_type == "summary" and container.is_narrow:
            if fit.risk in ("medium", "high"):
                new_lines = compact_summary(llm_s.body_lines, _SUMMARY_MAX_SENTENCES_NARROW)
                log.debug(
                    "compact_summary: '%s' %d→%d lines (fit=%s)",
                    llm_s.heading, len(llm_s.body_lines), len(new_lines), fit.risk,
                )
                compacted.append(LlmSection(
                    heading=llm_s.heading,
                    semantic_type=llm_s.semantic_type,
                    body_lines=new_lines,
                    roles=llm_s.roles,
                ))
                continue

        elif llm_s.semantic_type == "skills" and (
            container.is_narrow or tpl_class == "table_sidebar"
        ):
            if fit.risk in ("medium", "high"):
                target = max(container.orig_para_count, 3)
                new_lines = compact_skills(llm_s.body_lines, target)
                log.debug(
                    "compact_skills: '%s' %d→%d lines (fit=%s, target=%d)",
                    llm_s.heading, len(llm_s.body_lines), len(new_lines),
                    fit.risk, target,
                )
                compacted.append(LlmSection(
                    heading=llm_s.heading,
                    semantic_type=llm_s.semantic_type,
                    body_lines=new_lines,
                    roles=llm_s.roles,
                ))
                continue

        compacted.append(llm_s)

    # Step 4.5: Skills content sanitization (spec §6).
    # Remove internal-marker lines, "Additional" lines, and full-sentence lines
    # from skills sections.  This complements the updater-level sanitization so
    # the validation step below sees clean content.
    compacted = _sanitize_skills_sections(compacted)

    # Step 5: Validation
    warnings = validate_llm_sections(compacted, containers)
    for w in warnings:
        log.warning("layout: %s", w)

    return compacted


# ---------------------------------------------------------------------------
# Skills content sanitization (spec §6)
# ---------------------------------------------------------------------------

_SKILLS_MARKER_RE = re.compile(
    r"CURRENT_DATE|Generated\s+on|__TEMPLATE__|\{\{|\}\}",
    re.IGNORECASE,
)
_SKILLS_ADDITIONAL_RE = re.compile(r"^additional\b", re.IGNORECASE)


def _sanitize_skills_body(body_lines: list[str]) -> list[str]:
    """Filter out lines that must not appear in a rendered Skills section (spec §6).

    Removes:
    - Lines containing internal markers (CURRENT_DATE, Generated on, etc.).
    - Lines starting with "Additional".
    - Full sentences: 8+ space-separated tokens ending with sentence punctuation.
    """
    clean: list[str] = []
    for line in body_lines:
        stripped = line.strip()
        if not stripped:
            clean.append(line)
            continue
        if _SKILLS_MARKER_RE.search(stripped):
            log.debug("sanitize_skills: dropping marker line %r", stripped[:80])
            continue
        if _SKILLS_ADDITIONAL_RE.match(stripped):
            log.debug("sanitize_skills: dropping 'Additional' line %r", stripped[:80])
            continue
        tokens = stripped.split()
        if len(tokens) >= 8 and stripped[-1] in ".!?":
            log.debug("sanitize_skills: dropping full-sentence line %r", stripped[:80])
            continue
        clean.append(line)
    return clean


def _sanitize_skills_sections(llm_sections: list[LlmSection]) -> list[LlmSection]:
    """Apply _sanitize_skills_body to every skills section. Returns a new list."""
    result: list[LlmSection] = []
    for s in llm_sections:
        if s.semantic_type == "skills":
            result.append(LlmSection(
                heading=s.heading,
                semantic_type=s.semantic_type,
                body_lines=_sanitize_skills_body(s.body_lines),
                roles=s.roles,
            ))
        else:
            result.append(s)
    return result

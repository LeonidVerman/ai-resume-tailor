"""
src/tailor/compiler/semantic_enricher.py

Deterministic semantic enrichment layer.

Runs AFTER normalize_classification_input() and BEFORE the LLM classifier.
Promotes ambiguous parser_semantic="paragraph" entries to more specific types
using semantic_hint values set by the normalizer and text/context heuristics.

Guardrails
----------
- Modifies paragraphs (parser_semantic == "paragraph").
- Also promotes bullet entries whose semantic_hint == "tech_stack_candidate"
  (normalizer has already validated these as tech stacks).
- Also promotes role_meta entries in Projects sections to project_entry
  (does not change role ownership, only the semantic label).
- Never touches role_header, section_heading, or empty entries.
- Conservative: keeps original semantic when confidence < 0.80.
"""
from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tailor.compiler.classification_models import (
        ClassificationInput,
        ClassificationParaInput,
        ClassificationSectionInput,
    )

# ---------------------------------------------------------------------------
# Diagnostic event names (exported so callers can filter by constant)
# ---------------------------------------------------------------------------

ENRICH_TECH_STACK                 = "ENRICH_TECH_STACK"
ENRICH_TECH_STACK_DETAIL          = "ENRICH_TECH_STACK_DETAIL"
ENRICH_HIGHLIGHT_HEADER           = "ENRICH_HIGHLIGHT_HEADER"
ENRICH_PROJECT_HEADER             = "ENRICH_PROJECT_HEADER"
ENRICH_PROJECT_ENTRY              = "ENRICH_PROJECT_ENTRY"
ENRICH_INTERNAL_PROJECT           = "ENRICH_INTERNAL_PROJECT"
ENRICH_ROLE_INTRO                 = "ENRICH_ROLE_INTRO"
ENRICH_SPECIALIZATION_HEADER      = "ENRICH_SPECIALIZATION_HEADER"
ENRICH_PROJECT_INTRO_CONTINUATION = "ENRICH_PROJECT_INTRO_CONTINUATION"

# ---------------------------------------------------------------------------
# Internal patterns
# ---------------------------------------------------------------------------

_BULLET_GLYPHS: frozenset[str] = frozenset("•◦▪▸●►*-")
_SENTENCE_END: frozenset[str] = frozenset(".!?,;")
_ROLE_INTRO_TERMINATORS: frozenset[str] = frozenset(".!?")

_PROJECT_KW_RE = re.compile(r"\bproject\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")
_A_LINK_RE = re.compile(r"^a link\s", re.IGNORECASE)
_PROJECTS_SECTION_RE = re.compile(r"project", re.IGNORECASE)
# PDF-converted fake bullet prefix: "f " followed by uppercase letter
_PDF_FAKE_BULLET_RE = re.compile(r"^f [A-Z]")
# Year at end of line: "2024" or "2025-2026" or "2019-20"
_YEAR_AT_END_RE = re.compile(r"\b(?:19|20)\d{2}(?:-(?:19|20)?\d{2})?\s*$")

# Technical domain keywords — presence is required for specialization_header.
_DOMAIN_KW_RE = re.compile(
    r"\b(?:backend|front.?end|full.?stack|devops|infrastructure|cloud|"
    r"architecture|engineering|development|platform|security|data|api|"
    r"microservices|embedded|mobile|web|systems|integration|distributed)\b",
    re.IGNORECASE,
)

# Tech-layer key words for the extended tech_stack detection.
# Covers patterns NOT already caught by the normalizer's _TECH_STACK_RE:
#   "Application level:", "Persistence level:", "Containers:", "Monitoring and metrics:",
#   "Cache:", "Message:", "Queue:", "Deploy:", "Logging:", "Auth:", "ELK:",
#   "DevOps tools:", "Databases:"
_TECH_LAYER_FIRST_WORDS: frozenset[str] = frozenset({
    "application", "persistence",
    "container", "containers",
    "monitoring", "monitor",
    "metric", "metrics",
    "storage",
    "cache", "caching",
    "message", "messaging",
    "queue",
    "deployment", "deploy",
    "logging", "log", "logs",
    "authentication", "authorization", "auth",
    "elk",
    "devops",
    "databases", "database",
})

# parser_semantic values considered "role structural" for role_intro context.
# project_entry is included so that project descriptions following a project
# entry are promoted to role_intro (chained enrichment in Projects sections).
_ROLE_STRUCTURAL_SEMS = frozenset({
    "role_header", "role_meta", "role_intro", "project_entry",
})

_MIN_CONFIDENCE = 0.80


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_tech_layer_line(text: str) -> bool:
    """True for tech-layer description lines not caught by the normalizer.

    Detects patterns like:
      "Application level: Java 8-11, Spring Boot"
      "Monitoring and metrics: Prometheus, Grafana"
      "Containers: Amazon EKS, Kubernetes, Docker"
      "Persistence level: PostgreSQL, Hibernate"
      "DevOps tools: GitLab, Jenkins, Docker"
      "Databases: MySQL, Oracle, PostgreSQL"
    """
    colon_idx = text.find(":")
    if colon_idx < 1 or colon_idx > 40:
        return False
    # The key is everything before the first colon; check its first word.
    key = text[:colon_idx].strip().lower()
    first_word = key.split()[0] if key.split() else ""
    return first_word in _TECH_LAYER_FIRST_WORDS


# ---------------------------------------------------------------------------
# Per-type confidence scorers
# Return 0.0 when the rule definitely does not apply.
# ---------------------------------------------------------------------------


def _conf_tech_stack(para: "ClassificationParaInput") -> float:
    """Promote tech-stack lines already identified by the normalizer."""
    if para.semantic_hint == "tech_stack_candidate":
        return 0.95
    return 0.0


def _conf_tech_stack_detail(para: "ClassificationParaInput") -> float:
    """Promote tech-layer lines not caught by the normalizer's primary pattern."""
    if _is_tech_layer_line(para.text.strip()):
        return 0.90
    return 0.0


def _conf_highlight_header(para: "ClassificationParaInput") -> float:
    """Promote highlight/summary lines already identified by the normalizer."""
    if para.semantic_hint == "highlight_candidate":
        return 0.95
    return 0.0


def _conf_project_entry(
    para: "ClassificationParaInput",
    is_projects_section: bool,
    is_unowned: bool,
) -> float:
    """Unowned project-section entry (paragraph type only).

    Catches two patterns:
    1. Pre-normalization "a link <name>" prefix still present in text.
    2. Post-normalization: entry ending with a year (normalizer strips "a link").
    """
    if not is_projects_section:
        return 0.0
    if not is_unowned:
        return 0.0
    t = para.text.strip()
    if _A_LINK_RE.match(t):
        return 0.90
    # Year-ending project entry: "Name, description, 2020" or "Name, tool, 2025-2026"
    if _YEAR_AT_END_RE.search(t):
        return 0.85
    return 0.0


def _conf_project_header(
    para: "ClassificationParaInput",
    next_para: "ClassificationParaInput | None",
) -> float:
    """Short line containing 'project' keyword, no sentence punctuation."""
    t = para.text.strip()
    if not _PROJECT_KW_RE.search(t):
        return 0.0
    if len(t) > 60:
        return 0.0
    if t[-1:] in _SENTENCE_END:
        return 0.0
    if t and t[0] in _BULLET_GLYPHS:
        return 0.0
    if ":" in t:  # tech_stack lines handled at higher priority
        return 0.0
    conf = 0.85
    if next_para and next_para.parser_semantic == "bullet":
        conf = min(conf + 0.09, 0.99)
    return conf


def _conf_internal_project(
    para: "ClassificationParaInput",
    in_role: bool,
) -> float:
    """Long project description within a role body.

    Targets patterns like:
      "Contribution to the internal project - a self service portal..."
      "Migration project for the payments platform..."
    """
    if not in_role:
        return 0.0
    t = para.text.strip()
    if not _PROJECT_KW_RE.search(t):
        return 0.0
    # Must be longer than project_header threshold (project_header ≤ 60)
    if len(t) <= 60:
        return 0.0
    # Guard against very long bullets masking as descriptions
    if len(t) > 250:
        return 0.0
    # Exclude PDF-fake bullets and real bullet markers
    if t and t[0] in _BULLET_GLYPHS:
        return 0.0
    if _PDF_FAKE_BULLET_RE.match(t):
        return 0.0
    # Exclude tech_stack lines (colon in first 40 chars handled earlier)
    if ":" in t[:20]:
        return 0.0
    return 0.84


def _conf_role_intro(
    para: "ClassificationParaInput",
    prev_sem: str,
    in_meta_ids: bool,
) -> float:
    """Descriptive sentence immediately inside a role block."""
    # intro_candidate hint (italic *…* notation from some exporters)
    if para.semantic_hint == "intro_candidate":
        return 0.90

    t = para.text.strip()
    # Must be a complete sentence
    if t[-1:] not in _ROLE_INTRO_TERMINATORS:
        return 0.0
    # Minimum substance: at least 25 chars and 4 words
    if len(t) < 25 or t.count(" ") < 3:
        return 0.0
    # Require positional context: directly follows role structural element
    # OR the role structure places it in meta_para_ids.
    if prev_sem not in _ROLE_STRUCTURAL_SEMS and not in_meta_ids:
        return 0.0
    return 0.82


def _conf_specialization_header(
    para: "ClassificationParaInput",
    next_para: "ClassificationParaInput | None",
) -> float:
    """Short domain-keyword title line with no sentence punctuation or digits."""
    t = para.text.strip()
    if not _DOMAIN_KW_RE.search(t):
        return 0.0
    if len(t) > 80:
        return 0.0
    if t[-1:] in _SENTENCE_END:
        return 0.0
    if _DIGIT_RE.search(t):
        return 0.0
    if t and t[0] in _BULLET_GLYPHS:
        return 0.0
    if ":" in t:  # tech_stack lines handled at higher priority
        return 0.0
    conf = 0.80
    if next_para and next_para.parser_semantic in ("bullet", "paragraph"):
        conf = min(conf + 0.07, 0.95)
    return conf


def _conf_project_intro_continuation(
    para: "ClassificationParaInput",
    prev_sem: str,
    in_role: bool,
) -> float:
    """Continuation paragraph immediately after a project_intro.

    Targets split project descriptions like:
      para_160: "Contribution to the internal project..."  → project_intro
      para_162: "technical growth and helps manage it..."  → project_intro (continuation)

    Requires the text to start with a lowercase letter: genuine mid-sentence
    splits always continue lowercase.  Standalone responsibilities ("Mentoring
    junior specialists.", "Implemented REST API.") start uppercase and must not
    be promoted.
    """
    if prev_sem != "project_intro":
        return 0.0
    if not in_role:
        return 0.0
    t = para.text.strip()
    if not t:
        return 0.0
    if t[0] in _BULLET_GLYPHS:
        return 0.0
    if _PDF_FAKE_BULLET_RE.match(t):
        return 0.0
    if len(t) < 10:
        return 0.0
    # Standalone responsibilities start with uppercase; continuations start lowercase.
    if t[0].isupper():
        return 0.0
    return 0.82


# ---------------------------------------------------------------------------
# Core per-paragraph enrichment
# ---------------------------------------------------------------------------


def _enrich_para(
    para: "ClassificationParaInput",
    prev_sem: str,
    next_para: "ClassificationParaInput | None",
    meta_para_id_set: set[str],
    bullet_para_id_set: set[str],
    is_projects_section: bool,
) -> "tuple[ClassificationParaInput, float, str]":
    """Return (result, confidence, event_name).  result is original when no enrichment."""
    # Bullets with an explicit normalizer tech-stack hint are promoted directly.
    # The normalizer already validated the pattern; the bullet label is incidental.
    if para.parser_semantic == "bullet" and para.semantic_hint == "tech_stack_candidate":
        if para.text.strip():
            return dataclasses.replace(para, parser_semantic="tech_stack"), 0.95, ENRICH_TECH_STACK
        return para, 0.0, ""

    if para.parser_semantic != "paragraph" or not para.text.strip():
        return para, 0.0, ""

    in_meta = para.para_id in meta_para_id_set
    in_bullet_slot = para.para_id in bullet_para_id_set
    in_role = in_meta or in_bullet_slot
    is_unowned = not in_role

    candidates: list[tuple[float, str, str]] = []

    # Hint-based (highest confidence, always wins)
    c = _conf_tech_stack(para)
    if c:
        candidates.append((c, "tech_stack", ENRICH_TECH_STACK))

    c = _conf_highlight_header(para)
    if c:
        candidates.append((c, "highlight_header", ENRICH_HIGHLIGHT_HEADER))

    # Extended tech-stack layer lines
    c = _conf_tech_stack_detail(para)
    if c:
        candidates.append((c, "tech_stack", ENRICH_TECH_STACK_DETAIL))

    # Project section entry (hyperlinked or year-ending, unowned)
    c = _conf_project_entry(para, is_projects_section, is_unowned)
    if c:
        candidates.append((c, "project_entry", ENRICH_PROJECT_ENTRY))

    # Short project title
    c = _conf_project_header(para, next_para)
    if c:
        candidates.append((c, "project_header", ENRICH_PROJECT_HEADER))

    # Role intro (positional or hint-based)
    c = _conf_role_intro(para, prev_sem, in_meta)
    if c:
        candidates.append((c, "role_intro", ENRICH_ROLE_INTRO))

    # Long project description in role body
    c = _conf_internal_project(para, in_role)
    if c:
        candidates.append((c, "project_intro", ENRICH_INTERNAL_PROJECT))

    # Continuation of a project_intro block
    c = _conf_project_intro_continuation(para, prev_sem, in_role)
    if c:
        candidates.append((c, "project_intro", ENRICH_PROJECT_INTRO_CONTINUATION))

    # Specialization header (domain keyword, short, no punct)
    c = _conf_specialization_header(para, next_para)
    if c:
        candidates.append((c, "specialization_header", ENRICH_SPECIALIZATION_HEADER))

    if not candidates:
        return para, 0.0, ""

    conf, new_sem, event = max(candidates, key=lambda x: x[0])

    if conf < _MIN_CONFIDENCE:
        return para, 0.0, ""

    return dataclasses.replace(para, parser_semantic=new_sem), conf, event


# ---------------------------------------------------------------------------
# role_meta promotion in Projects sections
# ---------------------------------------------------------------------------


def _try_enrich_projects_meta(
    para: "ClassificationParaInput",
    is_projects_section: bool,
) -> "tuple[ClassificationParaInput, float, str]":
    """Promote role_meta → project_entry inside a Projects section.

    The parser groups project name lines as role_meta (mirroring experience
    structure). In Projects sections these are project entries, not role
    metadata. This does not alter role ownership — the para_id remains in
    meta_para_ids; only the semantic label changes.
    """
    if not is_projects_section:
        return para, 0.0, ""
    if para.parser_semantic != "role_meta":
        return para, 0.0, ""
    if not para.text.strip():
        return para, 0.0, ""
    return dataclasses.replace(para, parser_semantic="project_entry"), 0.90, ENRICH_PROJECT_ENTRY


# ---------------------------------------------------------------------------
# Section and document level
# ---------------------------------------------------------------------------


def _enrich_section(
    sec: "ClassificationSectionInput",
) -> "tuple[ClassificationSectionInput, list[dict]]":
    meta_para_id_set: set[str] = set()
    bullet_para_id_set: set[str] = set()
    for role in sec.roles:
        meta_para_id_set.update(role.meta_para_ids)
        bullet_para_id_set.update(role.bullet_para_ids)

    is_projects_section = bool(_PROJECTS_SECTION_RE.search(sec.raw_title or ""))

    diagnostics: list[dict] = []
    new_paras: list["ClassificationParaInput"] = []
    prev_sem = ""

    for i, para in enumerate(sec.paragraphs):
        next_para = sec.paragraphs[i + 1] if i + 1 < len(sec.paragraphs) else None

        # Special case: role_meta in Projects sections → project_entry
        if para.parser_semantic == "role_meta" and is_projects_section:
            enriched, conf, event = _try_enrich_projects_meta(para, is_projects_section)
        else:
            enriched, conf, event = _enrich_para(
                para, prev_sem, next_para,
                meta_para_id_set, bullet_para_id_set, is_projects_section,
            )

        new_paras.append(enriched)
        prev_sem = enriched.parser_semantic
        if event:
            diagnostics.append({
                "event": event,
                "para_id": para.para_id,
                "text": para.text[:120],
                "confidence": round(conf, 2),
                "original_semantic": para.parser_semantic,
                "enriched_semantic": enriched.parser_semantic,
            })

    return dataclasses.replace(sec, paragraphs=new_paras), diagnostics


def enrich_semantics(
    ci: "ClassificationInput",
) -> "tuple[ClassificationInput, list[dict]]":
    """Promote ambiguous paragraph semantics before LLM classification.

    Parameters
    ----------
    ci:
        Post-normalization ClassificationInput (semantic_hint fields populated).

    Returns
    -------
    (enriched_input, diagnostics)
        enriched_input  — new ClassificationInput; parser_semantic may differ
                          on paragraph-typed paras and role_meta paras in
                          Projects sections.
        diagnostics     — list of {event, para_id, text, confidence,
                          original_semantic, enriched_semantic} records.
    """
    all_diagnostics: list[dict] = []
    new_sections = []

    for sec in ci.sections:
        new_sec, sec_diag = _enrich_section(sec)
        new_sections.append(new_sec)
        all_diagnostics.extend(sec_diag)

    return dataclasses.replace(ci, sections=new_sections), all_diagnostics

"""
src/tailor/compiler/semantic_enricher.py

Deterministic semantic enrichment layer.

Runs AFTER normalize_classification_input() and BEFORE the LLM classifier.
Promotes ambiguous parser_semantic="paragraph" entries to more specific types
using semantic_hint values set by the normalizer and text/context heuristics.

Guardrails
----------
- Only modifies paragraphs whose parser_semantic == "paragraph".
- Never touches role boundaries, section boundaries, or structural elements
  (role_header, role_meta, bullet, section_heading, empty).
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

ENRICH_TECH_STACK            = "ENRICH_TECH_STACK"
ENRICH_HIGHLIGHT_HEADER      = "ENRICH_HIGHLIGHT_HEADER"
ENRICH_PROJECT_HEADER        = "ENRICH_PROJECT_HEADER"
ENRICH_ROLE_INTRO            = "ENRICH_ROLE_INTRO"
ENRICH_SPECIALIZATION_HEADER = "ENRICH_SPECIALIZATION_HEADER"

# ---------------------------------------------------------------------------
# Internal patterns
# ---------------------------------------------------------------------------

_BULLET_GLYPHS: frozenset[str] = frozenset("•◦▪▸●►*-")
_SENTENCE_END: frozenset[str] = frozenset(".!?,;")
_ROLE_INTRO_TERMINATORS: frozenset[str] = frozenset(".!?")

_PROJECT_KW_RE = re.compile(r"\bproject\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")

# Technical domain keywords — presence is required for specialization_header.
_DOMAIN_KW_RE = re.compile(
    r"\b(?:backend|front.?end|full.?stack|devops|infrastructure|cloud|"
    r"architecture|engineering|development|platform|security|data|api|"
    r"microservices|embedded|mobile|web|systems|integration|distributed)\b",
    re.IGNORECASE,
)

# parser_semantic values considered "role structural" for role_intro context
_ROLE_STRUCTURAL_SEMS = frozenset({"role_header", "role_meta", "role_intro"})

_MIN_CONFIDENCE = 0.80

# ---------------------------------------------------------------------------
# Per-type confidence scorers
# Return 0.0 when the rule definitely does not apply.
# ---------------------------------------------------------------------------


def _conf_tech_stack(para: "ClassificationParaInput") -> float:
    """Promote tech-stack lines already identified by the normalizer."""
    if para.semantic_hint == "tech_stack_candidate":
        return 0.95
    return 0.0


def _conf_highlight_header(para: "ClassificationParaInput") -> float:
    """Promote highlight/summary lines already identified by the normalizer."""
    if para.semantic_hint == "highlight_candidate":
        return 0.95
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


# ---------------------------------------------------------------------------
# Core per-paragraph enrichment
# ---------------------------------------------------------------------------


def _enrich_para(
    para: "ClassificationParaInput",
    prev_sem: str,
    next_para: "ClassificationParaInput | None",
    meta_para_id_set: set[str],
) -> "tuple[ClassificationParaInput, float, str]":
    """Return (result, confidence, event_name).  result is original when no enrichment."""
    if para.parser_semantic != "paragraph" or not para.text.strip():
        return para, 0.0, ""

    in_meta = para.para_id in meta_para_id_set

    # Collect all candidates; pick highest confidence.
    # Priority expressed via insertion order for tie-breaking.
    candidates: list[tuple[float, str, str]] = []

    c = _conf_tech_stack(para)
    if c:
        candidates.append((c, "tech_stack", ENRICH_TECH_STACK))

    c = _conf_highlight_header(para)
    if c:
        candidates.append((c, "highlight_header", ENRICH_HIGHLIGHT_HEADER))

    c = _conf_project_header(para, next_para)
    if c:
        candidates.append((c, "project_header", ENRICH_PROJECT_HEADER))

    c = _conf_role_intro(para, prev_sem, in_meta)
    if c:
        candidates.append((c, "role_intro", ENRICH_ROLE_INTRO))

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
# Section and document level
# ---------------------------------------------------------------------------


def _enrich_section(
    sec: "ClassificationSectionInput",
) -> "tuple[ClassificationSectionInput, list[dict]]":
    meta_para_id_set: set[str] = set()
    for role in sec.roles:
        meta_para_id_set.update(role.meta_para_ids)

    diagnostics: list[dict] = []
    new_paras: list["ClassificationParaInput"] = []
    prev_sem = ""

    for i, para in enumerate(sec.paragraphs):
        next_para = sec.paragraphs[i + 1] if i + 1 < len(sec.paragraphs) else None
        enriched, conf, event = _enrich_para(para, prev_sem, next_para, meta_para_id_set)
        new_paras.append(enriched)
        prev_sem = enriched.parser_semantic
        if event:
            diagnostics.append({
                "event": event,
                "para_id": para.para_id,
                "text": para.text[:120],
                "confidence": round(conf, 2),
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
        enriched_input  — new ClassificationInput; only parser_semantic on
                          paragraph-typed paras may differ from the input.
        diagnostics     — list of {event, para_id, text, confidence} records.
    """
    all_diagnostics: list[dict] = []
    new_sections = []

    for sec in ci.sections:
        new_sec, sec_diag = _enrich_section(sec)
        new_sections.append(new_sec)
        all_diagnostics.extend(sec_diag)

    return dataclasses.replace(ci, sections=new_sections), all_diagnostics

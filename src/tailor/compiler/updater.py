"""Apply LLM-tailored sections to a ResumeDocument, producing an updated copy.

Matching strategy
-----------------
1. Exact heading match (case-insensitive).
2. Semantic-type fallback (e.g. first unmatched "experience" ↔ "experience").
3. Unmatched sections in the original are kept verbatim.

Hard-fail rules (raise ValueError)
-----------------------------------
- An LLM section cannot be matched to any original section.
- For experience sections: the LLM output has a different role count than the
  original (the LLM must preserve all roles).
"""
from __future__ import annotations

import copy
from typing import Sequence

from tailor.compiler.models import (
    ParaModel,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)
from tailor.compiler.text_parser import LlmRole, LlmSection


# ---------------------------------------------------------------------------
# Section matching
# ---------------------------------------------------------------------------

def _match_sections(
    orig: list[ResumeSection],
    llm: list[LlmSection],
) -> list[tuple[ResumeSection, LlmSection | None]]:
    """Return (original_section, llm_section_or_None) pairs for every original section.

    Raises ValueError if an LLM section cannot be matched to any original.
    """
    used_llm: set[int] = set()
    used_orig: set[int] = set()
    pairs: list[tuple[int, int]] = []   # (orig_idx, llm_idx)

    # Pass 1: exact heading match
    for li, ls in enumerate(llm):
        for oi, os_ in enumerate(orig):
            if oi in used_orig:
                continue
            if os_.title.lower() == ls.heading.lower():
                pairs.append((oi, li))
                used_orig.add(oi)
                used_llm.add(li)
                break

    # Pass 2: semantic type match
    for li, ls in enumerate(llm):
        if li in used_llm:
            continue
        for oi, os_ in enumerate(orig):
            if oi in used_orig:
                continue
            if os_.semantic_type == ls.semantic_type and ls.semantic_type != "other":
                pairs.append((oi, li))
                used_orig.add(oi)
                used_llm.add(li)
                break

    # Any LLM section still unmatched → hard fail
    for li, ls in enumerate(llm):
        if li not in used_llm:
            raise ValueError(
                f"LLM output contains section '{ls.heading}' that cannot be "
                f"matched to any section in the original document."
            )

    # Build result: original sections keep their order; unmatched originals kept as-is
    result: list[tuple[ResumeSection, LlmSection | None]] = []
    for oi, os_ in enumerate(orig):
        matched = next((p for p in pairs if p[0] == oi), None)
        if matched:
            result.append((os_, llm[matched[1]]))
        else:
            result.append((os_, None))

    return result


# ---------------------------------------------------------------------------
# Role updating
# ---------------------------------------------------------------------------

def _update_role(orig: RoleEntry, llm: LlmRole) -> RoleEntry:
    """Produce an updated RoleEntry from original + LLM data."""

    # Header: update text, keep style proto
    new_header = orig.header.with_text(llm.header)

    # Meta lines: reuse original protos, clone extra if needed
    new_meta: list[ParaModel] = []
    for i, meta_text in enumerate(llm.meta_lines):
        if i < len(orig.meta_lines):
            new_meta.append(orig.meta_lines[i].with_text(meta_text))
        else:
            src = orig.meta_lines[-1] if orig.meta_lines else orig.header
            new_meta.append(src.clone_as(meta_text, "role_meta"))

    # Bullets: reuse original protos, clone archetype for extras
    arch = orig.bullets[0] if orig.bullets else orig.header
    new_bullets: list[ParaModel] = []
    for i, bullet_text in enumerate(llm.bullets):
        if i < len(orig.bullets):
            new_bullets.append(orig.bullets[i].with_text(bullet_text))
        else:
            new_bullets.append(arch.clone_as(bullet_text, "bullet"))

    return RoleEntry(
        header=new_header,
        meta_lines=new_meta,
        bullets=new_bullets,
        role_id=orig.role_id,
    )


def _update_experience_section(orig: ResumeSection, llm: LlmSection) -> ResumeSection:
    if len(llm.roles) > len(orig.roles):
        raise ValueError(
            f"Experience section '{orig.title}': LLM output has {len(llm.roles)} role(s) "
            f"but the original only has {len(orig.roles)}. Cannot render extra roles "
            f"without a format prototype."
        )

    # Match by position; if LLM has fewer roles, surplus originals are dropped.
    updated_roles = [_update_role(o, l) for o, l in zip(orig.roles, llm.roles)]

    return ResumeSection(
        title=llm.heading,
        heading=orig.heading.with_text(llm.heading),
        semantic_type=orig.semantic_type,
        body_paras=orig.body_paras,  # kept for flat-list rendering order
        roles=updated_roles,
    )


def _update_body_section(orig: ResumeSection, llm: LlmSection) -> ResumeSection:
    """Update a non-experience section with LLM body lines."""
    llm_lines = [l for l in llm.body_lines if l.strip()]

    # Find non-empty body paragraphs to reuse protos from
    non_empty = [p for p in orig.body_paras if p.text.strip()]

    arch = non_empty[0] if non_empty else orig.heading

    new_body: list[ParaModel] = []
    for i, line in enumerate(llm_lines):
        if i < len(non_empty):
            new_body.append(non_empty[i].with_text(line))
        else:
            new_body.append(arch.clone_as(line, "paragraph"))

    # Preserve any leading/trailing empty paras from original for spacing
    leading_empty = []
    for p in orig.body_paras:
        if not p.text.strip():
            leading_empty.append(p)
        else:
            break

    return ResumeSection(
        title=llm.heading,
        heading=orig.heading.with_text(llm.heading),
        semantic_type=orig.semantic_type,
        body_paras=leading_empty + new_body,
        roles=[],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_tailored(
    original: ResumeDocument,
    llm_sections: list[LlmSection],
) -> ResumeDocument:
    """Apply LLM-tailored sections to the original document.

    Returns a new ResumeDocument with updated content; the original is not
    modified.  The all_paras flat list is rebuilt from the updated sections.

    Raises
    ------
    ValueError
        If sections or roles cannot be matched (see module docstring).
    """
    pairs = _match_sections(original.sections, llm_sections)

    new_sections: list[ResumeSection] = []
    for orig_section, llm_section in pairs:
        if llm_section is None:
            # No LLM content for this section — keep original verbatim
            new_sections.append(orig_section)
            continue

        if orig_section.semantic_type == "experience":
            new_sections.append(_update_experience_section(orig_section, llm_section))
        else:
            new_sections.append(_update_body_section(orig_section, llm_section))

    # Rebuild flat para list in document order
    all_paras: list[ParaModel] = list(original.header_paras)
    for section in new_sections:
        all_paras.append(section.heading)
        if section.semantic_type == "experience":
            for role in section.roles:
                all_paras.append(role.header)
                all_paras.extend(role.meta_lines)
                all_paras.extend(role.bullets)
        else:
            all_paras.extend(section.body_paras)

    return ResumeDocument(
        header_paras=original.header_paras,
        sections=new_sections,
        layout=original.layout,
        all_paras=all_paras,
    )

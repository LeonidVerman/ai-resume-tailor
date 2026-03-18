"""Apply LLM-tailored sections to a ResumeDocument, producing an updated copy.

Matching strategy
-----------------
1. Exact heading match (case-insensitive).
2. Semantic-type fallback (e.g. first unmatched "experience" ↔ "experience").
3. Unmatched sections in the original are kept verbatim.

Hard-fail rules (raise ValueError)
-----------------------------------
- An LLM section cannot be matched to any original section AND some original
  sections are also unmatched (the LLM simultaneously dropped and invented
  sections — almost certainly a structural error).

Soft handling for extra LLM sections
--------------------------------------
If the LLM outputs extra sections not present in the original, but ALL original
sections are matched, the extras are inserted into the output at the position
they appear in the LLM output.  Heading style is cloned from the nearest
existing section heading; body paragraph style is cloned from the nearest
existing body paragraph.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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

@dataclass
class _MatchResult:
    # (orig_section, llm_section_or_None) for each original, in original order
    pairs: list[tuple[ResumeSection, LlmSection | None]] = field(default_factory=list)
    # LLM sections that had no match in the original, in LLM output order.
    # Only populated when all original sections were matched.
    extras: list[LlmSection] = field(default_factory=list)
    # llm_idx for each pair entry (None when original had no LLM match);
    # parallel to pairs.
    llm_indices: list[int | None] = field(default_factory=list)


def _match_sections(
    orig: list[ResumeSection],
    llm: list[LlmSection],
) -> _MatchResult:
    """Match original sections to LLM sections.

    Raises ValueError only when an unmatched LLM section coexists with an
    unmatched original section (structural mismatch that cannot be recovered).
    When all originals are matched and extra LLM sections remain, those extras
    are returned in _MatchResult.extras for the caller to handle.
    """
    used_llm: set[int] = set()
    used_orig: set[int] = set()
    pairs: list[tuple[int, int]] = []   # (orig_idx, llm_idx)

    # Pass 1: exact heading match (case-insensitive)
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

    unmatched_llm = [li for li in range(len(llm)) if li not in used_llm]

    if unmatched_llm:
        all_orig_matched = len(used_orig) == len(orig)
        if not all_orig_matched:
            # Hard fail: LLM both dropped and invented sections.
            raise ValueError(
                f"LLM output contains section '{llm[unmatched_llm[0]].heading}' "
                f"that cannot be matched to any section in the original document."
            )
        # All originals matched — extras are new sections added by the LLM.
        extras = [llm[li] for li in unmatched_llm]
    else:
        extras = []

    # Build pairs_result in original section order
    result_pairs: list[tuple[ResumeSection, LlmSection | None]] = []
    llm_indices: list[int | None] = []
    for oi, os_ in enumerate(orig):
        matched = next((p for p in pairs if p[0] == oi), None)
        if matched:
            result_pairs.append((os_, llm[matched[1]]))
            llm_indices.append(matched[1])
        else:
            result_pairs.append((os_, None))
            llm_indices.append(None)

    return _MatchResult(pairs=result_pairs, extras=extras, llm_indices=llm_indices)


# ---------------------------------------------------------------------------
# Role updating
# ---------------------------------------------------------------------------

def _update_role(orig: RoleEntry, llm: LlmRole) -> RoleEntry:
    """Produce an updated RoleEntry from original + LLM data."""

    # Header: update text, keep style proto
    new_header = orig.header.with_text(llm.header)

    # If the template had a multi-line role header (e.g. "..., St." / "Petersburg"),
    # the LLM input included the continuation line as a separate paragraph, so the
    # LLM may echo it back as a meta line.  Strip any meta line whose text matches
    # a header_extra fragment so it doesn't appear in the rendered output.
    header_extra_texts = {pm.text.strip().lower() for pm in orig.header_extra}
    llm_meta = [m for m in llm.meta_lines if m.strip().lower() not in header_extra_texts]

    # Meta lines: reuse original protos, clone extra if needed
    new_meta: list[ParaModel] = []
    for i, meta_text in enumerate(llm_meta):
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
    # Match by position; surplus originals are dropped, extra LLM roles clone from last orig.
    updated_roles = [_update_role(o, l) for o, l in zip(orig.roles, llm.roles)]

    if len(llm.roles) > len(orig.roles) and orig.roles:
        last_orig = orig.roles[-1]
        for extra_llm in llm.roles[len(orig.roles):]:
            updated_roles.append(_update_role(last_orig, extra_llm))

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


def _make_extra_section(
    llm: LlmSection,
    heading_arch: ParaModel,
    body_arch: ParaModel,
) -> ResumeSection:
    """Create a new ResumeSection for an LLM section absent from the template.

    heading_arch is cloned for the section heading (preserves heading style).
    body_arch is cloned for each body line (preserves body paragraph style).
    """
    new_heading = heading_arch.clone_as(llm.heading, "section_heading")

    body_paras: list[ParaModel] = []
    for line in llm.body_lines:
        if line.strip():
            body_paras.append(body_arch.clone_as(line, "paragraph"))

    return ResumeSection(
        title=llm.heading,
        heading=new_heading,
        semantic_type=llm.semantic_type,
        body_paras=body_paras,
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

    When the LLM adds sections that are absent from the original (but all
    original sections are present), the extras are inserted at their LLM
    output position using cloned styles from the nearest existing sections.

    Raises
    ------
    ValueError
        If sections cannot be matched (see module docstring).
    """
    match = _match_sections(original.sections, llm_sections)

    if not match.extras:
        # ---- Fast path: no extras, keep original section order ----
        new_sections: list[ResumeSection] = []
        for orig_section, llm_section in match.pairs:
            if llm_section is None:
                new_sections.append(orig_section)
            elif orig_section.semantic_type == "experience":
                new_sections.append(_update_experience_section(orig_section, llm_section))
            else:
                new_sections.append(_update_body_section(orig_section, llm_section))

    else:
        # ---- Extras path: follow LLM output order, splicing in extras ----
        # When extras exist, all originals are matched so llm_section is never None.

        # Style archetypes for extra sections
        heading_arch: ParaModel = match.pairs[0][0].heading  # first section heading
        body_arch: ParaModel = heading_arch                   # fallback
        for orig_s, _ in match.pairs:
            for p in orig_s.body_paras:
                if p.text.strip():
                    body_arch = p
                    break
            else:
                continue
            break

        # Map llm heading (lower) → updated section for matched pairs
        heading_to_section: dict[str, ResumeSection] = {}
        for orig_section, llm_section in match.pairs:
            assert llm_section is not None  # guaranteed when extras exist
            if orig_section.semantic_type == "experience":
                updated = _update_experience_section(orig_section, llm_section)
            else:
                updated = _update_body_section(orig_section, llm_section)
            heading_to_section[llm_section.heading.lower()] = updated

        # Iterate LLM output order; emit matched or extra sections
        new_sections = []
        for llm_s in llm_sections:
            key = llm_s.heading.lower()
            if key in heading_to_section:
                new_sections.append(heading_to_section[key])
            else:
                new_sections.append(_make_extra_section(llm_s, heading_arch, body_arch))

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

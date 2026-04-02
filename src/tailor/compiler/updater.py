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
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
    TableBlock,
)
from tailor.compiler.text_parser import LlmRole, LlmSection

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# C: Freeze Education — set True to preserve source Education verbatim and
# ignore LLM-generated Education content during DOCX post-processing.
# To re-enable LLM Education rewrites: set this to False.
FREEZE_EDUCATION: bool = True


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
        # 'other'-type originals that the LLM omits (e.g. the name/contact header
        # block "Leonid Verman") are kept verbatim and don't count as "dropped".
        # Only real content sections (summary, experience, skills, education) must
        # be present for all_orig_matched to be True.
        unmatched_content_orig = [
            oi for oi in range(len(orig))
            if oi not in used_orig and orig[oi].semantic_type != "other"
        ]
        all_orig_matched = len(unmatched_content_orig) == 0
        if not all_orig_matched:
            # Hard fail: LLM both dropped a real content section and invented one.
            raise ValueError(
                f"LLM output contains section '{llm[unmatched_llm[0]].heading}' "
                f"that cannot be matched to any section in the original document."
            )
        # All content originals matched — extras are new sections added by the LLM.
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


def _is_decorative_para(pm: ParaModel) -> bool:
    """Return True when *pm* is a decorative ornament/divider that should be preserved verbatim.

    Both conditions must hold:
    1. No alphanumeric characters in the text — the paragraph is purely ornamental
       (e.g. the ◇—————————◇ separator in template 5).  Paragraphs with actual
       content text (role headers, sub-headings) are never decorative even if they
       use two fonts.
    2. Mixed run fonts — guards against treating plain dash-separator lines with a
       single font as decorative.

    Such paragraphs must never be used as LLM-text targets because _set_para_text
    would distribute new content across the wrong font runs and produce corrupted output.
    """
    import re
    if re.search(r"[A-Za-z0-9]", pm.text):
        return False  # has readable content → not a decorative divider
    xml = pm.style.xml_proto
    if xml is None:
        return False
    fonts: set[str] = set()
    for r_elem in xml.findall(f"{{{_W}}}r"):
        rPr = r_elem.find(f"{{{_W}}}rPr")
        if rPr is None:
            continue
        f_elem = rPr.find(f"{{{_W}}}rFonts")
        if f_elem is not None:
            fname = (
                f_elem.get(f"{{{_W}}}ascii")
                or f_elem.get(f"{{{_W}}}cs")
                or f_elem.get(f"{{{_W}}}hAnsi")
            )
            if fname:
                fonts.add(fname)
    return len(fonts) > 1


def _update_body_section(orig: ResumeSection, llm: LlmSection) -> ResumeSection:
    """Update a non-experience section with LLM body lines."""
    llm_lines = [l for l in llm.body_lines if l.strip()]

    # Separate body paragraphs into content targets and decorative preservations.
    # Decorative paras (mixed run fonts) are preserved verbatim and never used as
    # LLM-text targets; they act like empty spacers in the mapping.
    non_empty = [p for p in orig.body_paras if p.text.strip()]
    content_paras = [p for p in non_empty if not _is_decorative_para(p)]

    # Derive the cloning archetype from real content paragraphs (clean font).
    # Fall back to heading only when the section has no content paragraphs at all.
    arch = content_paras[0] if content_paras else orig.heading

    # Build updated versions of each content para (paired by position with LLM lines).
    updated: list[ParaModel] = []
    for i, line in enumerate(llm_lines):
        if i < len(content_paras):
            updated.append(content_paras[i].with_text(line))
        else:
            updated.append(arch.clone_as(line, "paragraph"))

    # Rebuild body_paras:
    # - empty paras → preserved (spacing)
    # - decorative paras → preserved verbatim (font integrity)
    # - content paras → replaced with updated LLM text (in order)
    new_body: list[ParaModel] = []
    content_cursor = 0
    for p in orig.body_paras:
        if not p.text.strip():
            new_body.append(p)
        elif _is_decorative_para(p):
            new_body.append(p)
        elif content_cursor < len(updated):
            new_body.append(updated[content_cursor])
            content_cursor += 1
        # else: LLM produced fewer lines — drop trailing content paras

    # Append extra LLM lines beyond the original content para count.
    for i in range(len(content_paras), len(llm_lines)):
        new_body.append(updated[i])

    return ResumeSection(
        title=llm.heading,
        heading=orig.heading.with_text(llm.heading),
        semantic_type=orig.semantic_type,
        body_paras=new_body,
        roles=[],
    )


def _find_body_prototype(
    pairs: "list[tuple[ResumeSection, LlmSection | None]]",
) -> ParaModel:
    """Return the best body-text prototype for inserted extra sections.

    B: Selection criteria (in priority order):
    1. Non-empty body paragraph from a non-'other' section.
    2. Not bold (avoids cloning heading-style paragraphs).
    3. Not explicitly center- or right-aligned (hard left-alignment rule).
    Falls back to any non-empty body para, then to the first section heading.
    """
    # Preferred: non-other, non-bold, non-center/right para
    for orig_section, _ in pairs:
        if orig_section.semantic_type == "other":
            continue
        for p in orig_section.body_paras:
            if not p.text.strip():
                continue
            if p.style.bold:
                continue
            if p.style.alignment in ("center", "right"):
                continue
            return p
    # Fallback: any non-empty body para
    for orig_section, _ in pairs:
        for p in orig_section.body_paras:
            if p.text.strip():
                return p
    return pairs[0][0].heading


def _make_left_aligned(pm: ParaModel) -> ParaModel:
    """Return a clone of *pm* with alignment forced to left.

    B: Strips ``w:jc`` from the cloned xml_proto's ``w:pPr`` so that Word
    defaults to left-alignment.  For PDF-sourced paragraphs (xml_proto=None),
    sets paragraph_profile.alignment = 'left'.

    This is the hard left-alignment rule for all inserted body paragraphs.
    """
    from copy import deepcopy
    from tailor.compiler.models import ParagraphProfile

    cloned_style = ParaStyle(
        style_name=pm.style.style_name,
        alignment=None,  # force left
        indent_left=pm.style.indent_left,
        indent_right=pm.style.indent_right,
        hanging=pm.style.hanging,
        spacing_before=pm.style.spacing_before,
        spacing_after=pm.style.spacing_after,
        line_spacing=pm.style.line_spacing,
        keep_with_next=pm.style.keep_with_next,
        numbering=pm.style.numbering,
        bold=pm.style.bold,
        italic=pm.style.italic,
        font_name=pm.style.font_name,
        font_size_pt=pm.style.font_size_pt,
        color=pm.style.color,
        xml_proto=pm.style.clone_proto(),
    )
    # Strip explicit alignment from XML so Word uses its default (left).
    if cloned_style.xml_proto is not None:
        pPr = cloned_style.xml_proto.find(f"{{{_W}}}pPr")
        if pPr is not None:
            jc = pPr.find(f"{{{_W}}}jc")
            if jc is not None:
                pPr.remove(jc)

    pp_clone: "ParagraphProfile | None" = None
    if pm.paragraph_profile is not None:
        pp_clone = ParagraphProfile.from_dict(pm.paragraph_profile.to_dict())
        pp_clone.alignment = "left"

    return ParaModel(
        text=pm.text,
        style=cloned_style,
        semantic=pm.semantic,
        paragraph_profile=pp_clone,
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
            elif FREEZE_EDUCATION and orig_section.semantic_type == "education":
                # C: Education freeze — preserve source Education verbatim.
                new_sections.append(orig_section)
            elif orig_section.semantic_type == "experience":
                if orig_section.roles or llm_section.roles:
                    new_sections.append(_update_experience_section(orig_section, llm_section))
                else:
                    # No roles on either side — treat as body section to avoid content loss
                    new_sections.append(_update_body_section(orig_section, llm_section))
            else:
                new_sections.append(_update_body_section(orig_section, llm_section))

    else:
        # ---- Extras path: follow LLM output order, splicing in extras ----
        # Content originals are all matched; 'other'-type originals (e.g. the
        # name/contact header block) may still have llm_section=None and are
        # kept verbatim, prepended before the LLM-ordered sections.

        # B: Style archetypes for extra sections.
        # heading_arch: first section heading (unchanged — heading style is fine).
        # body_arch: best left-aligned, non-bold, non-'other' body paragraph.
        heading_arch: ParaModel = match.pairs[0][0].heading
        body_arch: ParaModel = _make_left_aligned(_find_body_prototype(match.pairs))

        # Map llm heading (lower) → updated section; collect verbatim unmatched.
        heading_to_section: dict[str, ResumeSection] = {}
        verbatim_sections: list[ResumeSection] = []
        for orig_section, llm_section in match.pairs:
            if llm_section is None:
                # 'other'-type section not output by LLM — keep verbatim
                verbatim_sections.append(orig_section)
            elif FREEZE_EDUCATION and orig_section.semantic_type == "education":
                # C: Education freeze in extras path — keep original content but
                # register under the LLM heading key so it is placed at the correct
                # LLM output position (not prepended to verbatim_sections, which
                # would cause the LLM's education entry to be treated as an "extra"
                # section and duplicated in the output).
                heading_to_section[llm_section.heading.lower()] = orig_section
            elif orig_section.semantic_type == "experience":
                if orig_section.roles or llm_section.roles:
                    heading_to_section[llm_section.heading.lower()] = (
                        _update_experience_section(orig_section, llm_section)
                    )
                else:
                    # No roles on either side — treat as body section to avoid content loss
                    heading_to_section[llm_section.heading.lower()] = (
                        _update_body_section(orig_section, llm_section)
                    )
            else:
                heading_to_section[llm_section.heading.lower()] = (
                    _update_body_section(orig_section, llm_section)
                )

        # Iterate LLM output order; emit matched or extra sections
        llm_order_sections: list[ResumeSection] = []
        for llm_s in llm_sections:
            key = llm_s.heading.lower()
            if key in heading_to_section:
                llm_order_sections.append(heading_to_section[key])
            else:
                llm_order_sections.append(_make_extra_section(llm_s, heading_arch, body_arch))

        # Verbatim sections (name/contact block etc.) always precede the body.
        new_sections = verbatim_sections + llm_order_sections

    # Rebuild flat para list in document order
    all_paras: list[ParaModel] = list(original.header_paras)
    for section in new_sections:
        all_paras.append(section.heading)
        if section.semantic_type == "experience" and section.roles:
            # Emit pre-role orphan body_paras only when body_paras contains
            # an actual role_header paragraph (pipe-format resumes).  For
            # separate-line format resumes there is no role_header in
            # body_paras; skipping the orphan loop avoids duplicating content
            # that was already consumed into section.roles by _group_roles.
            if any(bp.semantic == "role_header" for bp in section.body_paras):
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break  # reached first role; stop collecting orphans
                    if bp.text.strip():
                        all_paras.append(bp)
            for role in section.roles:
                all_paras.append(role.header)
                all_paras.extend(role.meta_lines)
                all_paras.extend(role.bullets)
        else:
            all_paras.extend(section.body_paras)

    # When the original document uses table-based layout, carry body_items forward
    # so the renderer re-inserts tables as opaque blobs.
    # The TableBlock.para_models hold the ORIGINAL ParaModel objects; we update
    # their .text in-place so the renderer reads the latest tailored text without
    # needing index-based remapping (which breaks when apply_tailored drops
    # trailing/middle empty spacing paragraphs, shifting positions).
    #
    # For flat DOCX documents (body_items has no TableBlocks), we do NOT carry
    # body_items forward — the renderer must use all_paras so structural changes
    # (extra bullets, dropped roles) are reflected in the output.
    has_table_blocks = (
        original.body_items is not None
        and any(isinstance(i, TableBlock) for i in original.body_items)
    )
    if has_table_blocks and not match.extras:
        # Table in-place update: mutate ParaModel.text on the original objects
        # so _render_table_block picks up the new text from tb.para_models.
        # Skipped when extras exist because the extras path creates new
        # ParaModel objects (not the original table's para_models references),
        # so in-place mutation would have no effect.
        for orig_section, llm_section in match.pairs:
            if llm_section is None:
                continue
            orig_section.heading.text = llm_section.heading

            if orig_section.semantic_type == "experience":
                for o_role, n_role in zip(orig_section.roles, llm_section.roles):
                    o_role.header.text = n_role.header
                    for o_m, n_m in zip(o_role.meta_lines, n_role.meta_lines):
                        o_m.text = n_m
                    for o_b, n_b in zip(o_role.bullets, n_role.bullets):
                        o_b.text = n_b
            else:
                non_empty_orig = [p for p in orig_section.body_paras if p.text.strip()]
                llm_lines = [l for l in llm_section.body_lines if l.strip()]
                for o_p, new_text in zip(non_empty_orig, llm_lines):
                    o_p.text = new_text

    return ResumeDocument(
        header_paras=original.header_paras,
        sections=new_sections,
        layout=original.layout,
        all_paras=all_paras,
        source_kind=original.source_kind,
        # Return body_items only when the in-place table update actually ran
        # (i.e. no extras).  When extras exist the in-place update was skipped,
        # leaving body_items with stale original text.  Passing None here makes
        # the renderer fall back to all_paras (correctly rebuilt by the extras
        # path) instead of rendering the unchanged original table.
        body_items=original.body_items if (has_table_blocks and not match.extras) else None,
    )

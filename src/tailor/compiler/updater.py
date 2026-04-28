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
  apply_tailored catches this and returns the original document verbatim
  (spec §9 failure mode).

Soft handling for extra LLM sections
--------------------------------------
If the LLM outputs extra sections not present in the original, but ALL original
sections are matched, the extras are inserted into the output at the position
they appear in the LLM output.  Heading style is cloned from the nearest
existing section heading; body paragraph style is cloned from the nearest
existing body paragraph.  Extra experience sections are never created (spec §5).

Locked sections
---------------
Sections with semantic_type in _LOCKED_SEMANTIC_TYPES are never modified
regardless of LLM output (spec §3).  Only summary, experience (bullets), and
skills are editable (spec §1).

Classification-constrained path
--------------------------------
When a ClassificationOutput is passed to apply_tailored(), the updater consults
it for each section before applying changes:
- preserve         → section kept verbatim (no text changes at all)
- preserve_heading → heading ParaModel.text never changed
- experience sections → header and meta lines NEVER changed; bullets only
- preserve_body_structure → no add/remove of body paragraphs; text-only update

When classification is None the function behaves identically to before.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tailor.compiler.models import (
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
    TableBlock,
)
from tailor.compiler.text_parser import LlmRole, LlmSection

if TYPE_CHECKING:
    from tailor.compiler.classification_models import ClassificationOutput, ClassificationSection, ClassificationRole

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_log = logging.getLogger(__name__)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Semantic types that are NEVER modified regardless of LLM output (spec §3).
# Only "summary", "experience", and "skills" are editable (spec §1).
_LOCKED_SEMANTIC_TYPES: frozenset[str] = frozenset({
    "education", "certifications", "languages", "websites",
})

# C: Backward-compat alias — True because education is in _LOCKED_SEMANTIC_TYPES.
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
        # 'other'-type and locked-type originals that the LLM omits are kept
        # verbatim and don't count as "dropped".  Only the editable content
        # sections (summary, experience, skills) plus education must be present
        # for all_orig_matched to be True.  Locked types (certifications,
        # languages, websites) are treated like 'other' here: the LLM is never
        # expected to reproduce them.
        _verbatim_only = frozenset({"other"}) | _LOCKED_SEMANTIC_TYPES
        unmatched_content_orig = [
            oi for oi in range(len(orig))
            if oi not in used_orig and orig[oi].semantic_type not in _verbatim_only
        ]
        all_orig_matched = len(unmatched_content_orig) == 0
        if not all_orig_matched:
            # Hard fail: LLM both dropped a real content section and invented one.
            raise ValueError(
                f"LLM output contains section '{llm[unmatched_llm[0]].heading}' "
                f"that cannot be matched to any section in the original document."
            )
        # All content originals matched — extras are new sections added by the LLM.
        # Drop extras that belong to locked types (e.g. Education, Certifications):
        # these are verbatim in the template and LLM output of them should be ignored.
        extras = [
            llm[li] for li in unmatched_llm
            if llm[li].semantic_type not in _LOCKED_SEMANTIC_TYPES
        ]
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

def _update_role(orig: RoleEntry, llm: LlmRole, layout_bound: bool = False) -> RoleEntry:
    """Produce an updated RoleEntry from original + LLM data.

    When *layout_bound* is True, no new unbound ParaModels are created:
    - Extra LLM meta lines are dropped (logged as UPDATER_EXTRA_LLM_CONTENT_DROPPED).
    - Extra LLM bullets beyond the original count are dropped.
    - role_id_stable is preserved from the original entry.
    """
    # Header: update text, keep style proto; strip any column break (the role
    # header may inherit a column break from the section heading para in
    # consolidated templates — the section heading handles column placement).
    new_header = _strip_col_break_para(orig.header.with_text(llm.header))

    # If the template had a multi-line role header (e.g. "..., St." / "Petersburg"),
    # the LLM input included the continuation line as a separate paragraph, so the
    # LLM may echo it back as a meta line.  Strip any meta line whose text matches
    # a header_extra fragment so it doesn't appear in the rendered output.
    header_extra_texts = {pm.text.strip().lower() for pm in orig.header_extra}
    llm_meta = [m for m in llm.meta_lines if m.strip().lower() not in header_extra_texts]

    # Meta lines: reuse original protos; in layout-bound mode drop extras.
    new_meta: list[ParaModel] = []
    for i, meta_text in enumerate(llm_meta):
        if i < len(orig.meta_lines):
            new_meta.append(orig.meta_lines[i].with_text(meta_text))
        elif not layout_bound:
            src = orig.meta_lines[-1] if orig.meta_lines else orig.header
            new_meta.append(src.clone_as(meta_text, "role_meta"))
        else:
            _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: extra meta line %r", meta_text[:60])

    # Bullets: reuse original protos.
    # layout-bound mode maps 1:1 and drops overflow to preserve visual density.
    # Aggressive multi-bullet packing into one paragraph is avoided because it
    # destroys visual layout (one huge paragraph where the template has one bullet).
    # Conservative single-line merge is allowed only when the combined length stays
    # within 1.25× the original paragraph's text length and contains no newlines.
    arch = orig.bullets[0] if orig.bullets else orig.header
    new_bullets: list[ParaModel] = []

    if layout_bound and orig.bullets:
        n_orig = len(orig.bullets)
        n_llm = len(llm.bullets)
        # Map existing slots 1:1; strictly drop overflow (no merging).
        # Merging multiple LLM bullets into one paragraph overloads a template
        # slot designed for a single sentence and breaks visual density.
        for i in range(min(n_orig, n_llm)):
            new_bullets.append(orig.bullets[i].with_text(llm.bullets[i]))
        if n_llm > n_orig:
            _log.debug(
                "BULLET_OVERFLOW_DROPPED_FOR_LAYOUT: %d extra bullets for role %r",
                n_llm - n_orig, orig.role_id[:40],
            )
    else:
        for i, bullet_text in enumerate(llm.bullets):
            if i < len(orig.bullets):
                new_bullets.append(orig.bullets[i].with_text(bullet_text))
            elif not layout_bound:
                new_bullets.append(arch.clone_as(bullet_text, "bullet"))
            else:
                _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: extra bullet %r", bullet_text[:60])

    return RoleEntry(
        header=new_header,
        meta_lines=new_meta,
        bullets=new_bullets,
        role_id=orig.role_id,
        role_id_stable=orig.role_id_stable if layout_bound else "",
    )


def _update_experience_section(
    orig: ResumeSection,
    llm: LlmSection,
    layout_bound: bool = False,
) -> ResumeSection:
    # When the LLM wrote roles in dash format (no "|"), parse_llm_output returns
    # body_lines instead of roles.  Re-parse and update only the bullets, keeping
    # the template's role headers and meta verbatim (dates, company, title).
    if not llm.roles and llm.body_lines and orig.roles:
        reparsed = _reparse_body_lines_as_roles(llm.body_lines)
        if reparsed:
            updated_roles: list[RoleEntry] = []
            for o_role, r_role in zip(orig.roles, reparsed):
                updated_roles.append(
                    _update_role_bullets_only(o_role, r_role.bullets, layout_bound=layout_bound)
                )
            # Template roles with no LLM counterpart are kept verbatim
            for o_role in orig.roles[len(reparsed):]:
                updated_roles.append(o_role)
            _ROLE_SEMANTICS_D = frozenset({"role_header", "role_meta", "bullet"})
            if layout_bound and updated_roles:
                _rpids_d: set[str] = set()
                for _r in updated_roles:
                    for _pm in [_r.header] + _r.meta_lines + _r.bullets:
                        if _pm.para_id:
                            _rpids_d.add(_pm.para_id)
                clean_body_d = [
                    p for p in orig.body_paras
                    if not p.text.strip() or (
                        p.semantic not in _ROLE_SEMANTICS_D
                        and p.para_id not in _rpids_d
                    )
                ]
            else:
                clean_body_d = orig.body_paras
            return ResumeSection(
                title=llm.heading,
                heading=_strip_col_break_para(orig.heading.with_text(llm.heading)),
                semantic_type=orig.semantic_type,
                body_paras=clean_body_d,
                roles=updated_roles,
                section_id=orig.section_id,
            )

    # Normal path: pipe-separated LLM roles matched by position.
    # In layout-bound mode:
    #   - surplus LLM roles are dropped (no unbound clones)
    #   - unmatched original roles are PRESERVED verbatim (Invariant 3: cardinality)
    updated_roles = [
        _update_role(o, l, layout_bound=layout_bound)
        for o, l in zip(orig.roles, llm.roles)
    ]

    if len(llm.roles) > len(orig.roles) and orig.roles and not layout_bound:
        last_orig = orig.roles[-1]
        for extra_llm in llm.roles[len(orig.roles):]:
            updated_roles.append(_update_role(last_orig, extra_llm))
    elif len(llm.roles) > len(orig.roles) and layout_bound:
        _log.debug(
            "UPDATER_EXTRA_LLM_CONTENT_DROPPED: %d extra LLM roles beyond template",
            len(llm.roles) - len(orig.roles),
        )

    # Invariant 3 (layout-bound): preserve unmatched original roles verbatim
    # so len(updated_roles) == len(orig.roles).  This prevents role collapse
    # when the LLM produces fewer roles than the template defines.
    if layout_bound and len(updated_roles) < len(orig.roles):
        for o_role in orig.roles[len(updated_roles):]:
            updated_roles.append(o_role)
            _log.debug(
                "ROLE_COLLAPSE_DETECTED: original role %r kept verbatim (no LLM match)",
                o_role.role_id,
            )

    # In layout-bound mode, when roles are the canonical representation,
    # remove role-like paragraphs AND paragraphs whose para_id is already
    # used by a role component from body_paras.  This prevents split-brain IR
    # where the same para_id carries two different texts (e.g. a template
    # bullet slot para_39 appears in both role.bullets with new text and in
    # body_paras with the original lorem ipsum).  The renderer processes
    # body_paras after roles so the old text would overwrite the update.
    _ROLE_SEMANTICS = frozenset({"role_header", "role_meta", "bullet"})
    if layout_bound and updated_roles:
        _role_para_ids: set[str] = set()
        for _r in updated_roles:
            if _r.header.para_id:
                _role_para_ids.add(_r.header.para_id)
            for _m in _r.meta_lines:
                if _m.para_id:
                    _role_para_ids.add(_m.para_id)
            for _b in _r.bullets:
                if _b.para_id:
                    _role_para_ids.add(_b.para_id)
        clean_body = [
            p for p in orig.body_paras
            if not p.text.strip() or (
                p.semantic not in _ROLE_SEMANTICS
                and p.para_id not in _role_para_ids
            )
        ]
        _log.debug(
            "split_brain_fix: cleaned %d role-claimed paras from body_paras of %r",
            len(orig.body_paras) - len(clean_body), orig.title,
        )
    else:
        clean_body = orig.body_paras

    return ResumeSection(
        title=llm.heading,
        heading=_strip_col_break_para(orig.heading.with_text(llm.heading)),
        semantic_type=orig.semantic_type,
        body_paras=clean_body,
        roles=updated_roles,
        section_id=orig.section_id,
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


_SKILLS_FILTER_RE = re.compile(
    r"CURRENT_DATE|Generated\s+on|__TEMPLATE__",
    re.IGNORECASE,
)
_ADDITIONAL_RE = re.compile(r"^additional\b", re.IGNORECASE)


def _sanitize_skills_lines(lines: list[str]) -> list[str]:
    """Remove lines that must not appear in a rendered Skills section (spec §6).

    Removes:
    - Lines containing internal markers: CURRENT_DATE, "Generated on", etc.
    - Lines starting with "Additional" (LLM sometimes emits "Additional: …").
    - Full sentences: lines with 8+ whitespace-separated tokens ending in "."
      (indicates the LLM accidentally wrote prose instead of skill tokens).
    """
    clean: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            clean.append(line)
            continue
        if _SKILLS_FILTER_RE.search(stripped):
            _log.debug("skills sanitize: dropping marker line %r", stripped[:80])
            continue
        if _ADDITIONAL_RE.match(stripped):
            _log.debug("skills sanitize: dropping 'Additional' line %r", stripped[:80])
            continue
        # Full-sentence detection: 6+ words AND ends with a sentence-final punct.
        # Threshold lowered from 8 to 6 to catch citizenship/personal-statement lines
        # like "Canadian citizen; eligible to work in Canada." that LLMs sometimes
        # append after the skills section.  Legitimate skill lines ending in a period
        # are rare; most skill entries use commas or no terminal punctuation.
        tokens = stripped.split()
        if len(tokens) >= 6 and stripped[-1] in ".!?":
            _log.debug("skills sanitize: dropping full-sentence line %r", stripped[:80])
            continue
        clean.append(line)
    return clean


def _update_body_section(
    orig: ResumeSection,
    llm: LlmSection,
    layout_bound: bool = False,
) -> ResumeSection:
    """Update a non-experience section with LLM body lines.

    When *layout_bound* is True:
    - Extra LLM lines beyond the original content-para count are packed into
      the last available slot (joined by newline) rather than creating new
      unbound paragraphs.  This keeps all content bound to existing para_ids.
    - section_id is carried over from the original so layout_blocks references
      remain resolvable.
    """
    llm_lines = [l for l in llm.body_lines if l.strip()]

    # Separate body paragraphs into content targets and decorative preservations.
    # Decorative paras (mixed run fonts) are preserved verbatim and never used as
    # LLM-text targets; they act like empty spacers in the mapping.
    non_empty = [p for p in orig.body_paras if p.text.strip()]
    content_paras = [p for p in non_empty if not _is_decorative_para(p)]

    # Derive the cloning archetype from real content paragraphs (clean font).
    # Fall back to heading only when the section has no content paragraphs at all.
    arch = content_paras[0] if content_paras else orig.heading

    # In layout-bound mode: drop surplus LLM lines rather than packing multiple
    # lines into one paragraph (which destroys visual layout density).
    # Only as many lines are used as there are anchored content slots.
    if layout_bound and content_paras and len(llm_lines) > len(content_paras):
        n_drop = len(llm_lines) - len(content_paras)
        _log.debug(
            "BODY_OVERFLOW_DROPPED_FOR_LAYOUT: dropped %d/%d lines from %r",
            n_drop, len(llm_lines), orig.title[:40],
        )
        packed_llm = llm_lines[: len(content_paras)]
    else:
        packed_llm = llm_lines

    # Build updated versions of each content para (paired by position with LLM lines).
    updated: list[ParaModel] = []
    for i, line in enumerate(packed_llm):
        if i < len(content_paras):
            updated.append(content_paras[i].with_text(line))
            _log.debug("UPDATER_LAYOUT_BOUND_REPLACEMENT: para_id=%r → %r",
                       content_paras[i].para_id, line[:60])
        elif not layout_bound:
            updated.append(arch.clone_as(line, "paragraph"))
        else:
            _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: %r (no slot)", line[:60])

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

    # Append extra LLM lines beyond the original content para count (non-layout-bound only).
    if not layout_bound:
        for i in range(len(content_paras), len(packed_llm)):
            new_body.append(updated[i])

    return ResumeSection(
        title=llm.heading,
        heading=orig.heading.with_text(llm.heading),
        semantic_type=orig.semantic_type,
        body_paras=new_body,
        roles=[],
        section_id=orig.section_id,
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

    Column breaks are stripped from the cloned heading: the heading_arch may
    have inherited a column break from a template paragraph that controlled
    two-column layout (e.g. the first role heading in the veeva_03 template).
    Extra sections should let natural column flow determine their position —
    keeping the break on an injected section heading (e.g. Professional Summary)
    causes it to jump to the wrong column when the left-column content overflows
    due to an expanded skills section.
    """
    new_heading = _strip_col_break_para(heading_arch.clone_as(llm.heading, "section_heading"))

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
# Column-break stripping helper
# ---------------------------------------------------------------------------

def _strip_col_break_para(pm: ParaModel) -> ParaModel:
    """Return a clone of *pm* with w:br type='column' removed from xml_proto.

    Only clones when a column break is actually present (cheap no-op otherwise).
    Used to strip spurious column breaks from Experience section headings, role
    headers, and extra section headings that inherit their xml_proto from a para
    that originally had a column break (e.g. the "Software Engineer" Heading 1
    that started the right column in the veeva_03 template).  Removing the break
    lets natural two-column flow determine column placement; this avoids the
    heading jumping to an unexpected column when the opposite column overflows
    due to an expanded skills section.
    """
    if pm.style.xml_proto is None:
        return pm
    has_cb = any(
        br.get(f"{{{_W}}}type") == "column"
        for br in pm.style.xml_proto.findall(f".//{{{_W}}}br")
    )
    if not has_cb:
        return pm
    cloned = pm.clone_as(pm.text, pm.semantic)
    for r_elem in list(cloned.style.xml_proto.findall(f"{{{_W}}}r")):
        for br in list(r_elem.findall(f"{{{_W}}}br")):
            if br.get(f"{{{_W}}}type") == "column":
                r_elem.remove(br)
    return cloned


# ---------------------------------------------------------------------------
# Experience body_lines re-parser (dash-format role headers)
# ---------------------------------------------------------------------------

# LLMs sometimes format roles as "Title — Company" or "Title / Leader — Company"
# (em/en dash) instead of the canonical "Title | Company" pipe.  parse_llm_output
# treats these as body_lines because _is_role_header requires "|".  This re-parser
# recovers the role structure so bullets can be matched to template roles.
_ROLE_BODY_SEP_RE = re.compile(r'\s\u2014\s|\s\u2013\s|\s\u2012\s')  # em/en/figure dash


def _reparse_body_lines_as_roles(body_lines: list[str]) -> list[LlmRole]:
    """Re-parse experience body_lines into LlmRole objects using dash separators.

    Only returns a non-empty list when at least one role-boundary line is found.
    Each role boundary is a line containing an em-dash / en-dash separator.
    """
    if not body_lines:
        return []

    # Locate role-boundary lines
    boundaries: list[int] = [
        i for i, line in enumerate(body_lines)
        if _ROLE_BODY_SEP_RE.search(line)
    ]
    if not boundaries:
        return []

    roles: list[LlmRole] = []
    for idx, boundary_i in enumerate(boundaries):
        end_i = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(body_lines)
        header = body_lines[boundary_i]
        meta: list[str] = []
        bullets: list[str] = []
        for line in body_lines[boundary_i + 1: end_i]:
            s = line.strip()
            if not s:
                continue
            if (
                _YEAR_RE.search(s)
                or s.lower() in ("current", "present", "dates not provided",
                                 "date not provided", "n/a")
            ):
                meta.append(s)
            else:
                bullets.append(s)
        roles.append(LlmRole(header=header, meta_lines=meta, bullets=bullets))
    return roles


def _update_role_bullets_only(
    orig: RoleEntry,
    llm_bullets: list[str],
    layout_bound: bool = False,
) -> RoleEntry:
    """Return a copy of *orig* with bullets replaced by *llm_bullets*.

    The role header and meta_lines are preserved verbatim from the template.
    Used when the LLM wrote roles in dash format: the header text is unreliable
    (formatting differs from template) so only the bullet content is used.

    When *layout_bound* is True, extra bullets beyond the original count are
    dropped instead of being cloned into unbound paragraphs.
    """
    arch = orig.bullets[0] if orig.bullets else orig.header
    new_bullets: list[ParaModel] = []
    if layout_bound and orig.bullets:
        n_orig = len(orig.bullets)
        n_llm = len(llm_bullets)
        for i in range(min(n_orig, n_llm)):
            new_bullets.append(orig.bullets[i].with_text(llm_bullets[i]))
        if n_llm > n_orig:
            extras = llm_bullets[n_orig:]
            last_orig_len = len(orig.bullets[-1].text)
            potential = new_bullets[-1].text + " " + " ".join(e.strip() for e in extras)
            max_merged = max(200, last_orig_len * 1.25)
            if "\n" not in potential and len(potential) <= max_merged:
                new_bullets[-1] = orig.bullets[-1].with_text(potential)
                _log.debug("BULLET_OVERFLOW_MERGED_CONSERVATIVELY: %d extras (bullets-only)", len(extras))
            else:
                _log.debug("BULLET_OVERFLOW_DROPPED_FOR_LAYOUT: %d extras (bullets-only)", len(extras))
    else:
        for i, text in enumerate(llm_bullets):
            if i < len(orig.bullets):
                new_bullets.append(orig.bullets[i].with_text(text))
            elif not layout_bound:
                new_bullets.append(arch.clone_as(text, "bullet"))
            else:
                _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: extra bullet %r", text[:60])
    return RoleEntry(
        # Strip any column break from the role header — the section heading
        # (or Summary heading) handles right-column placement; a second break
        # on the first role header would cause a spurious column jump.
        header=_strip_col_break_para(orig.header),
        meta_lines=orig.meta_lines,
        bullets=new_bullets,
        role_id=orig.role_id,
    )


# ---------------------------------------------------------------------------
# Header-skills detection and injection
# ---------------------------------------------------------------------------

def _find_header_skills_block(
    header_paras: list[ParaModel],
) -> tuple[int, int] | None:
    """Return (start, end_exclusive) of the last contiguous non-empty block
    in *header_paras* as a candidate for skill lines.

    This block is assumed to be the skills section in templates where skills
    live in the left-column header area (no dedicated section heading).
    Returns None when header_paras is empty or the last block is the very
    first block (name/title area — we avoid clobbering the header).
    """
    if not header_paras:
        return None

    # Walk back from the end to find last non-empty para
    end = len(header_paras) - 1
    while end >= 0 and not header_paras[end].text.strip():
        end -= 1
    if end < 0:
        return None

    # Walk back further to find the block start (stop at empty separator)
    start = end
    while start > 0 and header_paras[start - 1].text.strip():
        start -= 1

    # Don't treat the very first block (name/title, index 0) as skills
    if start == 0:
        return None

    return (start, end + 1)


def _clear_left_indent(pm: ParaModel) -> ParaModel:
    """Return a clone of *pm* with left/hanging/firstLine indents removed.

    Skill paragraphs in narrow-column templates often carry large left indents
    sized for the original short placeholder text (e.g. 'Java SQL').  When LLM
    skill lines replace those placeholders with longer content the inherited
    indent confines text to a tiny strip, producing single-character-per-line
    wrapping.  Clearing the left constraints while keeping the right indent and
    alignment preserves the intended right-aligned appearance without forcing
    the text into an impossibly narrow area.
    """
    from copy import deepcopy as _deepcopy
    proto = pm.style.xml_proto
    if proto is None:
        return pm
    new_proto = _deepcopy(proto)
    pPr = new_proto.find(f"{{{_W}}}pPr")
    if pPr is not None:
        ind = pPr.find(f"{{{_W}}}ind")
        if ind is not None:
            for attr in (f"{{{_W}}}left", f"{{{_W}}}hanging", f"{{{_W}}}firstLine"):
                if ind.get(attr) is not None:
                    del ind.attrib[attr]
            if not ind.attrib:
                pPr.remove(ind)
    from dataclasses import replace as _dc_replace
    new_style = _dc_replace(
        pm.style,
        indent_left=None,
        hanging=None,
        xml_proto=new_proto,
    )
    from tailor.compiler.models import ParaModel as _PM
    return _PM(text=pm.text, style=new_style, semantic=pm.semantic,
               paragraph_profile=pm.paragraph_profile)


def _inject_skills_into_header(
    header_paras: list[ParaModel],
    skill_range: tuple[int, int],
    llm_skills: "LlmSection",
    layout_bound: bool = False,
) -> list[ParaModel]:
    """Replace skill lines in *header_paras* with LLM skill content.

    *skill_range* is (start, end_exclusive) from _find_header_skills_block.
    Lines beyond the original skill-line count are appended as clones of
    the first original skill paragraph (or packed into the last slot when
    *layout_bound* is True).
    Sanitization (marker / sentence filtering) is applied to the LLM lines.

    Left/hanging/firstLine indents are stripped from every resulting skill
    paragraph: original placeholders were short tokens tuned to narrow indents,
    and LLM skill lines are typically much longer.  See _clear_left_indent.
    """
    start, end = skill_range
    orig_skill_paras = [header_paras[i] for i in range(start, end) if header_paras[i].text.strip()]
    if not orig_skill_paras:
        return list(header_paras)

    llm_lines = _sanitize_skills_lines(
        [line for line in llm_skills.body_lines if line.strip()]
    )

    # In layout-bound mode: pack surplus lines into last slot.
    if layout_bound and len(llm_lines) > len(orig_skill_paras):
        n = len(orig_skill_paras)
        packed = "\n".join(llm_lines[n - 1:])
        llm_lines = list(llm_lines[: n - 1]) + [packed]

    arch = _clear_left_indent(orig_skill_paras[0])
    new_skill_paras: list[ParaModel] = []
    for i, line in enumerate(llm_lines):
        if i < len(orig_skill_paras):
            new_skill_paras.append(_clear_left_indent(orig_skill_paras[i].with_text(line)))
        elif not layout_bound:
            new_skill_paras.append(arch.clone_as(line, "paragraph"))
        else:
            _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: header skill line %r", line[:60])

    return list(header_paras[:start]) + new_skill_paras + list(header_paras[end:])


# ---------------------------------------------------------------------------
# Intro-prose paragraph detection (for implicit summary injection)
# ---------------------------------------------------------------------------

# Minimum character length for a paragraph to qualify as intro prose.
_INTRO_PROSE_MIN_LEN = 60


def _find_intro_prose_para(original: ResumeDocument) -> ParaModel | None:
    """Find the template paragraph that looks like an intro/summary prose block.

    Used to inject LLM summary text into templates that have no dedicated summary
    section but do contain a prose-style intro paragraph (e.g. table-sidebar
    templates where the intro sits inside the skills or 'other' section column).

    Criteria:
    - Length ≥ _INTRO_PROSE_MIN_LEN characters.
    - Not a bullet, role_header, or role_meta semantic type.
    - No pipe separator (|) — role headers are excluded.
    - No URL (://).
    - Contains at least one space (not a single-token label).
    - Low comma density (< 0.10) — distinguishes prose from comma-separated skills.
    - Not in a locked or experience section (only skills / 'other' searched).
    """
    for section in original.sections:
        if section.semantic_type in _LOCKED_SEMANTIC_TYPES:
            continue
        if section.semantic_type == "experience":
            continue
        for p in section.body_paras:
            text = p.text.strip()
            if len(text) < _INTRO_PROSE_MIN_LEN:
                continue
            if p.semantic in ("role_header", "role_meta"):
                continue
            if "|" in text or "://" in text:
                continue
            if " " not in text:
                continue
            if text[0] in ("-", "•", "·", "–", "*"):
                continue
            comma_density = text.count(",") / max(1, len(text))
            if comma_density >= 0.10:
                continue
            return p
    return None


# Words that indicate a section is experience-related even when the section
# heading wasn't matched to _EXPERIENCE_NAMES (e.g. "Additional Experience",
# "Prior Employment").  Used to prevent spec §5 violations where the LLM
# invents a second experience block with a slightly different heading.
_EXPERIENCE_HEADING_WORDS: frozenset[str] = frozenset({
    "experience", "employment", "work", "career",
})


def _is_experience_like(llm_s: "LlmSection") -> bool:
    """Return True when llm_s looks like a duplicate experience section (spec §5)."""
    if llm_s.semantic_type == "experience":
        return True
    words = set(llm_s.heading.lower().split())
    return bool(words & _EXPERIENCE_HEADING_WORDS)


# ---------------------------------------------------------------------------
# Date-first experience layout detection and repair
# ---------------------------------------------------------------------------
#
# Some templates place the date range BEFORE the company/title lines:
#
#   (2010-2013)
#   Company Name
#   JOB TITLE
#   bullet ...
#
#   (2014-Now)
#   ...
#
# The parser's _group_roles() state machine expects role_header → meta → bullets
# and cannot handle this pattern. It misclassifies the first date as a
# role_header, producing one collapsed malformed RoleEntry per section.
#
# The fix runs entirely inside apply_tailored before any content update:
#   1. Detect the pattern from body_paras.
#   2. Rebuild correct RoleEntry groups (for matching only).
#   3. Match LLM roles to rebuilt IR roles by company name similarity.
#   4. Update bullet paragraph texts in-place.
#   5. Return section with roles=[] so the all_paras builder uses body_paras
#      (preserving the original template paragraph order).

_COMPANY_STOP_WORDS: frozenset[str] = frozenset({
    "inc", "co", "llc", "ltd", "corp", "international", "group",
    "the", "and", "of", "for", "a", "an",
})


def _extract_company_tokens(text: str) -> frozenset[str]:
    """Return normalised significant tokens from a role header or company line.

    Strips date ranges, splits on common role separators (pipe, dash) to keep
    only the company half, removes stop-words and punctuation.
    """
    # Drop parenthesised date ranges like "(2014-Now)", "(2010–2013)"
    text = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", "", text)
    # Split on role separators — keep only the first (company) segment
    parts = re.split(r"\s[–—\-]\s|\|", text)
    company_part = parts[0].strip().lower()
    # Remove punctuation
    company_part = re.sub(r"[^\w\s]", " ", company_part)
    tokens = frozenset(
        t for t in company_part.split()
        if t and t not in _COMPANY_STOP_WORDS and len(t) > 1
    )
    return tokens


def _has_date_first_layout(section: "ResumeSection") -> bool:
    """Return True when the experience section uses a date-first role layout.

    Conditions (all must hold):
    - body_paras contains ≥ 2 non-empty role_meta (date) paragraphs
    - roles is empty OR the first role's header carries a role_meta semantic
      (i.e. the parser collapsed the section into one malformed role)
    """
    meta_count = sum(
        1 for p in section.body_paras
        if p.text.strip() and p.semantic == "role_meta"
    )
    if meta_count < 2:
        return False
    if not section.roles:
        return True
    return section.roles[0].header.semantic == "role_meta"


def _rebuild_date_first_roles(section: "ResumeSection") -> "list[RoleEntry]":
    """Rebuild RoleEntry list from body_paras for a date-first experience section.

    Grouping algorithm:
    - A new role starts when a non-empty role_meta paragraph is encountered.
    - First non-empty non-date para after the date → header (company line).
    - Second non-empty non-date para before any content → header_extra (title).
    - Remaining non-empty paras until the next date → bullets.
    - Empty paragraphs are skipped for grouping; they stay in body_paras for
      rendering (body_paras is NOT modified).

    Returned RoleEntry objects hold direct references to the ParaModel objects
    inside body_paras (no copies are made).
    """
    roles: list[RoleEntry] = []

    cur_meta: list[ParaModel] = []
    cur_header: "ParaModel | None" = None
    cur_header_extra: list[ParaModel] = []
    cur_bullets: list[ParaModel] = []
    in_role = False

    def _flush() -> None:
        nonlocal cur_meta, cur_header, cur_header_extra, cur_bullets, in_role
        if not in_role or not cur_meta:
            return
        header = cur_header if cur_header is not None else cur_meta[0]
        roles.append(RoleEntry(
            header=header,
            header_extra=cur_header_extra[:],
            meta_lines=cur_meta[:],
            bullets=cur_bullets[:],
            role_id=header.text.strip(),
            role_id_stable=header.para_id or header.text.strip(),
        ))
        cur_meta = []
        cur_header = None
        cur_header_extra = []
        cur_bullets = []
        in_role = False

    for para in section.body_paras:
        if not para.text.strip():
            continue
        if para.semantic == "role_meta":
            _flush()
            cur_meta = [para]
            in_role = True
        elif in_role:
            if cur_header is None:
                cur_header = para
            elif not cur_bullets and not cur_header_extra:
                cur_header_extra = [para]
            else:
                cur_bullets.append(para)

    _flush()

    _log.debug(
        "date-first rebuild: section %r → %d roles (original malformed: %d)  "
        "meta_para_ids=%s  header_para_ids=%s",
        section.title,
        len(roles),
        len(section.roles),
        [r.meta_lines[0].para_id for r in roles if r.meta_lines],
        [r.header.para_id for r in roles],
    )
    return roles


def _match_llm_to_ir_roles(
    llm_roles: "list[LlmRole]",
    ir_roles: "list[RoleEntry]",
) -> "list[int | None]":
    """Match each IR role to the best LLM role by company name similarity.

    Returns a list of length len(ir_roles) where entry i is the index of the
    matched LLM role, or None when no match exceeded the similarity threshold.
    Unmatched LLM roles that remain after similarity matching are then assigned
    by position (fallback).

    Matching strategy (logged per role):
    1. Jaccard similarity of normalised company tokens > 0.3 → company_similarity
    2. First unmatched LLM role in LLM output order → position
    """
    ir_token_sets = []
    for ir_role in ir_roles:
        tokens: frozenset[str] = frozenset()
        for src in [ir_role.header] + ir_role.header_extra:
            tokens = tokens | _extract_company_tokens(src.text)
        ir_token_sets.append(tokens)

    used_llm: set[int] = set()
    result: list[int | None] = [None] * len(ir_roles)

    # Pass 1: company similarity
    for ir_idx, ir_tokens in enumerate(ir_token_sets):
        if not ir_tokens:
            continue
        best_score = 0.0
        best_llm_idx: int | None = None
        for llm_idx, llm_role in enumerate(llm_roles):
            if llm_idx in used_llm:
                continue
            llm_tokens = _extract_company_tokens(llm_role.header)
            if not llm_tokens:
                continue
            union = ir_tokens | llm_tokens
            score = len(ir_tokens & llm_tokens) / len(union)
            if score > best_score:
                best_score = score
                best_llm_idx = llm_idx
        if best_llm_idx is not None and best_score > 0.3:
            result[ir_idx] = best_llm_idx
            used_llm.add(best_llm_idx)
            _log.debug(
                "date-first match: IR role %r → LLM[%d] %r "
                "(score=%.2f, strategy=company_similarity)",
                ir_roles[ir_idx].role_id, best_llm_idx,
                llm_roles[best_llm_idx].header, best_score,
            )

    # Pass 2: positional fallback for unmatched IR roles
    llm_cursor = 0
    for ir_idx in range(len(ir_roles)):
        if result[ir_idx] is not None:
            continue
        while llm_cursor in used_llm and llm_cursor < len(llm_roles):
            llm_cursor += 1
        if llm_cursor < len(llm_roles):
            result[ir_idx] = llm_cursor
            used_llm.add(llm_cursor)
            _log.debug(
                "date-first match: IR role %r → LLM[%d] %r (strategy=position)",
                ir_roles[ir_idx].role_id, llm_cursor,
                llm_roles[llm_cursor].header,
            )
            llm_cursor += 1
        else:
            _log.debug(
                "date-first match: IR role %r → unmatched (no LLM role available)",
                ir_roles[ir_idx].role_id,
            )

    return result


def _update_experience_date_first(
    orig: "ResumeSection",
    llm: "LlmSection",
    rebuilt_roles: "list[RoleEntry]",
) -> "ResumeSection":
    """Apply LLM bullet content to a date-first experience section.

    - Resolves LLM roles from pipe or dash format.
    - Matches them to rebuilt IR roles by company similarity (then position).
    - Updates bullet paragraph texts in-place on body_paras ParaModel objects.
    - Returns the section with roles=[] so the all_paras builder uses body_paras
      in their original template order (date-first layout preserved).
    - Extra LLM roles beyond IR role count are ignored.
    - IR roles with no LLM counterpart keep their original bullet text.
    """
    # Resolve LLM role list (pipe or dash format)
    llm_roles = llm.roles
    if not llm_roles and llm.body_lines:
        reparsed = _reparse_body_lines_as_roles(llm.body_lines)
        if reparsed:
            llm_roles = reparsed

    if not llm_roles:
        _log.debug("date-first: no LLM roles resolved; preserving section verbatim")
        return ResumeSection(
            title=orig.title,
            heading=orig.heading,
            semantic_type=orig.semantic_type,
            body_paras=orig.body_paras,
            roles=[],
            section_id=orig.section_id,
        )

    match_map = _match_llm_to_ir_roles(llm_roles, rebuilt_roles)

    if len(llm_roles) > len(rebuilt_roles):
        _log.debug(
            "date-first: %d extra LLM roles ignored (IR has %d roles)",
            len(llm_roles) - len(rebuilt_roles), len(rebuilt_roles),
        )

    # Mutate bullet text in-place (the ParaModel objects are shared with body_paras)
    for ir_idx, ir_role in enumerate(rebuilt_roles):
        llm_idx = match_map[ir_idx]
        if llm_idx is None:
            _log.debug(
                "date-first: IR role %r → no match, keeping original bullets",
                ir_role.role_id,
            )
            continue
        llm_bullets = llm_roles[llm_idx].bullets
        for i, bullet_para in enumerate(ir_role.bullets):
            if i < len(llm_bullets):
                _log.debug(
                    "date-first: para %r updated  %r → %r",
                    bullet_para.para_id,
                    bullet_para.text[:40],
                    llm_bullets[i][:40],
                )
                bullet_para.text = llm_bullets[i]

    # Return with roles=[] so all_paras builder uses body_paras order
    return ResumeSection(
        title=orig.title,
        heading=orig.heading,
        semantic_type=orig.semantic_type,
        body_paras=orig.body_paras,
        roles=[],
        section_id=orig.section_id,
    )


# ---------------------------------------------------------------------------
# Classification-constrained update helpers
# ---------------------------------------------------------------------------

def _update_experience_classified(
    orig: ResumeSection,
    llm: LlmSection,
    cls_sec: "ClassificationSection",
    role_cls: "dict[str, ClassificationRole]",
    layout_bound: bool = False,
) -> ResumeSection:
    """Experience section update constrained by classification.

    Rules (always applied regardless of rewrite_policy, except "preserve"
    which is handled upstream):
    - Role header and meta lines are NEVER modified.
    - Only bullet text is updated.
    - IR role count is authoritative: extra LLM roles are ignored; extra IR
      roles beyond the LLM output are kept verbatim.
    """
    # Resolve LLM roles: try pipe format first, then dash format.
    llm_roles = llm.roles
    if not llm_roles and llm.body_lines and orig.roles:
        reparsed = _reparse_body_lines_as_roles(llm.body_lines)
        if reparsed:
            llm_roles = reparsed

    if len(llm_roles) > len(orig.roles):
        _log.debug(
            "classification: ignoring %d extra LLM roles for section %r (IR has %d)",
            len(llm_roles) - len(orig.roles), orig.title, len(orig.roles),
        )

    updated_roles: list[RoleEntry] = []
    for i, o_role in enumerate(orig.roles):
        if i < len(llm_roles):
            updated = _update_role_bullets_only(o_role, llm_roles[i].bullets, layout_bound=layout_bound)
            _log.debug(
                "classification: role %r → updated %d bullets",
                o_role.role_id, len(llm_roles[i].bullets),
            )
        else:
            # No LLM counterpart — keep IR role verbatim.
            updated = o_role
            _log.debug("classification: role %r → verbatim (no LLM counterpart)", o_role.role_id)
        updated_roles.append(updated)

    new_heading = (
        orig.heading
        if cls_sec.preserve_heading
        else _strip_col_break_para(orig.heading.with_text(llm.heading))
    )
    _log.debug(
        "classification: section %r preserve_heading=%s rewrite_policy=%s",
        orig.title, cls_sec.preserve_heading, cls_sec.rewrite_policy,
    )
    return ResumeSection(
        title=orig.title if cls_sec.preserve_heading else llm.heading,
        heading=new_heading,
        semantic_type=orig.semantic_type,
        body_paras=orig.body_paras,
        roles=updated_roles,
    )


def _update_body_classified(
    orig: ResumeSection,
    llm: LlmSection,
    cls_sec: "ClassificationSection",
    layout_bound: bool = False,
) -> ResumeSection:
    """Non-experience body section update constrained by classification.

    When preserve_body_structure is True: only update text inside existing
    content paragraphs — no adds, no removes.  Spacer and decorative paragraphs
    are always preserved.

    When preserve_body_structure is False: delegates to _update_body_section
    (existing behaviour), then patches the heading back if preserve_heading.
    """
    new_heading = (
        orig.heading
        if cls_sec.preserve_heading
        else orig.heading.with_text(llm.heading)
    )
    _log.debug(
        "classification: body section %r preserve_heading=%s preserve_body_structure=%s",
        orig.title, cls_sec.preserve_heading, cls_sec.preserve_body_structure,
    )

    if cls_sec.preserve_body_structure:
        llm_lines = [l for l in llm.body_lines if l.strip()]
        if orig.semantic_type == "skills":
            llm_lines = _sanitize_skills_lines(llm_lines)

        new_body: list[ParaModel] = []
        llm_cursor = 0
        for p in orig.body_paras:
            if not p.text.strip() or _is_decorative_para(p):
                new_body.append(p)
            elif llm_cursor < len(llm_lines):
                new_body.append(p.with_text(llm_lines[llm_cursor]))
                _log.debug("classification: para %r → updated", p.para_id or p.text[:30])
                llm_cursor += 1
            else:
                # No more LLM lines — keep original text.
                new_body.append(p)
                _log.debug("classification: para %r → verbatim (no LLM line)", p.para_id or p.text[:30])
        if llm_cursor < len(llm_lines):
            _log.debug(
                "classification: %d extra LLM lines ignored (preserve_body_structure)",
                len(llm_lines) - llm_cursor,
            )
        return ResumeSection(
            title=orig.title if cls_sec.preserve_heading else llm.heading,
            heading=new_heading,
            semantic_type=orig.semantic_type,
            body_paras=new_body,
            roles=[],
        )

    # No structure constraint — use existing body update, then restore heading if needed.
    if orig.semantic_type == "skills":
        llm = LlmSection(
            heading=llm.heading,
            semantic_type=llm.semantic_type,
            body_lines=_sanitize_skills_lines(llm.body_lines),
            roles=llm.roles,
        )
    updated = _update_body_section(orig, llm, layout_bound=layout_bound)
    if cls_sec.preserve_heading:
        return ResumeSection(
            title=orig.title,
            heading=orig.heading,
            semantic_type=updated.semantic_type,
            body_paras=updated.body_paras,
            roles=[],
        )
    return updated


def _apply_section_classified(
    orig: ResumeSection,
    llm: LlmSection,
    cls_sec: "ClassificationSection",
    role_cls: "dict[str, ClassificationRole]",
    layout_bound: bool = False,
) -> ResumeSection:
    """Dispatch classification-constrained section update."""
    if cls_sec.rewrite_policy == "preserve":
        _log.debug("classification: section %r (%s) → preserve", orig.title, orig.section_id)
        return orig
    if orig.semantic_type == "experience":
        return _update_experience_classified(orig, llm, cls_sec, role_cls, layout_bound=layout_bound)
    return _update_body_classified(orig, llm, cls_sec, layout_bound=layout_bound)


# ---------------------------------------------------------------------------
# LLM role-continuation repair
# ---------------------------------------------------------------------------

# Job-title words used to detect whether a section heading looks like a role
# title rather than a structural section name.
_JOB_TITLE_WORDS_FOR_REPAIR: frozenset[str] = frozenset({
    "engineer", "developer", "programmer", "designer", "analyst",
    "architect", "manager", "director", "lead", "senior", "junior",
    "intern", "associate", "specialist", "consultant", "coordinator",
    "administrator", "technician", "scientist", "researcher",
    "officer", "executive", "head", "principal", "staff",
    "web", "software", "frontend", "backend", "full", "ui", "ux",
    "data", "machine", "learning", "devops", "qa", "security",
})


def _is_role_continuation_section(sec: "LlmSection") -> bool:
    """Return True when *sec* looks like a role continuation rather than a real section.

    A section is treated as a role continuation when:
    - Its semantic_type is "other" (not a known structural section).
    - Its heading contains at least one job-title word.
    - Its heading is short (≤ 80 chars) and has no pipe / company markers.
    - Its body starts with role-like content: a sub-role (has ``|`` in first
      parsed role header) or a line containing a year.
    """
    if sec.semantic_type != "other":
        return False
    title = sec.heading.strip()
    if not title or len(title) > 80:
        return False
    if "|" in title:
        return False  # likely "Title | Company" — already a proper role header
    title_words = {w.lower() for w in re.split(r"\W+", title) if w}
    if not (title_words & _JOB_TITLE_WORDS_FOR_REPAIR):
        return False
    # Must have role-like body content.
    if sec.roles:
        return True  # text_parser found a sub-role → definitely a continuation
    for line in (sec.body_lines or [])[:4]:
        stripped = line.strip()
        if "|" in stripped or _YEAR_RE.search(stripped):
            return True
    return False


def _llm_section_to_role(sec: "LlmSection") -> "LlmRole":
    """Convert a role-continuation LlmSection to an LlmRole.

    The section heading becomes the role title.
    If text_parser found a sub-role inside the section (company|date format),
    that sub-role's header becomes a meta line and its bullets are used.
    Otherwise body_lines are inspected directly.
    """
    from tailor.compiler.text_parser import LlmRole as _LlmRole
    header = sec.heading.strip()
    meta_lines: list[str] = []
    bullets: list[str] = []

    if sec.roles:
        first = sec.roles[0]
        # The first sub-role header is typically "Company | Date"
        if first.header.strip():
            meta_lines.append(first.header)
        meta_lines.extend(first.meta_lines)
        bullets.extend(first.bullets)
        # Additional sub-roles in the section are rare but fold their bullets in.
        for extra in sec.roles[1:]:
            bullets.extend(extra.bullets)
    else:
        # Parse body_lines directly: lines with | or year → meta; bullet lines → bullets
        for line in sec.body_lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("- ", "• ", "* ", "– ")):
                bullets.append(stripped.lstrip("-•*– ").strip())
            elif "|" in stripped or _YEAR_RE.search(stripped):
                meta_lines.append(stripped)
            else:
                bullets.append(stripped)

    return _LlmRole(header=header, meta_lines=meta_lines, bullets=bullets)


def _repair_role_continuation_sections(
    llm_sections: list["LlmSection"],
) -> list["LlmSection"]:
    """Absorb role-title LLM sections following an Experience section as extra roles.

    When text_parser sees a blank line between roles in the LLM output it may
    emit each role title as a separate top-level LlmSection (semantic "other")
    rather than as an LlmRole inside the Experience section.  This function
    detects those continuation sections and merges them back so that:
    - The Experience section gains the extra LlmRole entries.
    - The standalone role-title sections are removed from the top-level list.

    Only sections that immediately follow an Experience section AND satisfy
    _is_role_continuation_section are absorbed.  Once a non-continuation section
    is encountered the scan stops (we do not skip structural sections to find
    more continuations).
    """
    result: list[LlmSection] = list(llm_sections)
    i = 0
    while i < len(result):
        sec = result[i]
        if sec.semantic_type != "experience":
            i += 1
            continue
        # Greedily absorb following role-continuation sections
        j = i + 1
        while j < len(result) and _is_role_continuation_section(result[j]):
            cand = result[j]
            new_role = _llm_section_to_role(cand)
            sec.roles.append(new_role)
            _log.debug(
                "UPDATER_ROLE_CONTINUATION_REPAIR: absorbed %r as role in Experience",
                cand.heading,
            )
            result.pop(j)  # remove the absorbed section; j stays same
        i += 1
    return result


# Short action-verb set used to distinguish achievement bullets from role titles.
_ACTION_VERBS_LB: frozenset[str] = frozenset({
    "developed", "built", "led", "managed", "created", "designed",
    "implemented", "architected", "optimized", "improved", "reduced",
    "increased", "collaborated", "worked", "utilized", "delivered",
    "maintained", "supported", "owned", "drove", "helped", "assisted",
    "spearheaded", "launched", "deployed", "automated", "integrated",
    "refactored", "migrated", "scaled", "researched", "analyzed",
    "coordinated", "oversaw", "directed", "established", "introduced",
})


def _bullet_looks_like_role_title(text: str) -> bool:
    """Return True when a bullet line looks like a role title rather than an achievement.

    Triggers on:
    - Lines containing ``|`` with non-empty text on both sides (canonical role format).
    - Short (≤ 6 tokens) title-case-like lines that start with a job-title word and
      do NOT start with an action verb.

    False-positive guard: lines ending with sentence punctuation, lines with
    common prepositions mid-text (indicating full sentences), and lines longer
    than 80 characters are rejected.
    """
    t = text.strip()
    if not t or len(t) > 80:
        return False
    if t[-1] in ".!?":
        return False
    # Pipe format is the strongest signal (Title | Company or Title | Date)
    if "|" in t:
        left, right = t.split("|", 1)
        if left.strip() and right.strip():
            return True
    # Short phrase with no action verb at start + job-title word
    tokens = t.split()
    if len(tokens) > 6:
        return False
    first = tokens[0].lower().rstrip(",;:")
    if first in _ACTION_VERBS_LB:
        return False
    words = {w.lower().strip(",:;()") for w in tokens}
    # Reject if contains sentence connectors indicating a full sentence
    if words & {"to", "for", "with", "using", "in", "at", "on", "from", "and", "or"}:
        return False
    if words & _JOB_TITLE_WORDS_FOR_REPAIR:
        return True
    return False


def _repair_roles_from_bullets(
    llm_sections: list["LlmSection"],
) -> list["LlmSection"]:
    """Extract role headers embedded as bullets back into proper LlmRole entries.

    When the LLM formats a new role start as a bullet point (e.g. a line like
    "Web Development Intern | Co." inside a role's bullet list), this function
    splits the role at that point and creates a new LlmRole for the continuation.

    Only modifies Experience sections; leaves all other sections unchanged.
    """
    from tailor.compiler.text_parser import LlmRole as _LlmRole
    for sec in llm_sections:
        if sec.semantic_type != "experience":
            continue
        repaired_roles: list["LlmRole"] = []
        for role in sec.roles:
            # Iterate over a snapshot of the original bullets to avoid mutation
            # during iteration (the role's bullet list is modified in place below).
            original_bullets = list(role.bullets)
            current_role: "LlmRole" = role
            current_bullets: list[str] = []
            repaired_roles.append(current_role)

            for bullet in original_bullets:
                if _bullet_looks_like_role_title(bullet):
                    # Assign accumulated bullets to the current role and start a new one
                    current_role.bullets[:] = current_bullets
                    current_bullets = []
                    new_role = _LlmRole(header=bullet.strip(), bullets=[])
                    repaired_roles.append(new_role)
                    current_role = new_role
                    _log.debug(
                        "ROLE_BOUNDARY_VIOLATION: extracted %r from bullets as new role",
                        bullet[:60],
                    )
                else:
                    current_bullets.append(bullet)

            # Assign remaining bullets to the last active role
            current_role.bullets[:] = current_bullets

        sec.roles[:] = repaired_roles
    return llm_sections


def normalize_llm_sections(
    llm_sections: list["LlmSection"],
) -> list["LlmSection"]:
    """Normalize LLM output sections to structural correctness before apply_tailored.

    Runs two repair passes:
    1. Role-continuation repair: absorbs "Web Designer"-style top-level sections
       that immediately follow an Experience section as additional LlmRole entries.
    2. Role-in-bullets repair: extracts role headers accidentally embedded as
       bullet points back into proper LlmRole entries.

    These repairs are always applied when layout_blocks are present to prevent
    structural mismatch between the semantic model and the layout tree.
    """
    llm_sections = _repair_role_continuation_sections(llm_sections)
    llm_sections = _repair_roles_from_bullets(llm_sections)
    return llm_sections


# ---------------------------------------------------------------------------
# Layout binding validation
# ---------------------------------------------------------------------------

def validate_layout_binding(doc: "ResumeDocument") -> dict:
    """Count layout-binding health metrics for an updated ResumeDocument.

    Returns a dict with:
    - ``unbound_semantic_paras``: paragraphs in the semantic model with empty para_id
    - ``unbound_non_empty_paras``: subset of above that have non-empty text
    - ``missing_section_ids``: sections with empty section_id
    - ``missing_role_ids``: roles with empty role_id_stable
    """
    from tailor.compiler.models import LayoutParagraphBlock, LayoutTableBlock

    unbound = 0
    unbound_non_empty = 0
    missing_sections = 0
    missing_roles = 0

    def _count_para(pm: "ParaModel") -> None:
        nonlocal unbound, unbound_non_empty
        if not pm.para_id:
            unbound += 1
            if pm.text.strip():
                unbound_non_empty += 1

    for pm in doc.header_paras:
        _count_para(pm)
    for sec in doc.sections:
        if not sec.section_id:
            missing_sections += 1
        _count_para(sec.heading)
        for role in sec.roles:
            if not role.role_id_stable:
                missing_roles += 1
            _count_para(role.header)
            for pm in role.meta_lines:
                _count_para(pm)
            for pm in role.bullets:
                _count_para(pm)
        for pm in sec.body_paras:
            _count_para(pm)

    # Para-ids referenced by layout_blocks but not in the semantic model
    lb_ids: set[str] = set()
    if doc.layout_blocks:
        for block in doc.layout_blocks:
            if isinstance(block, LayoutTableBlock):
                lb_ids.update(pid for pid in block.para_ids if pid)
            elif isinstance(block, LayoutParagraphBlock) and block.para_id:
                lb_ids.add(block.para_id)
    semantic_ids = {pm.para_id for pm in (doc.all_paras or []) if pm.para_id}
    orphan_layout_ids = len(lb_ids - semantic_ids)

    result = {
        "unbound_semantic_paras": unbound,
        "unbound_non_empty_paras": unbound_non_empty,
        "missing_section_ids": missing_sections,
        "missing_role_ids": missing_roles,
        "orphan_layout_block_ids": orphan_layout_ids,
    }
    _log.debug("validate_layout_binding: %s", result)
    return result


def validate_structural_integrity(
    original: "ResumeDocument",
    updated: "ResumeDocument",
) -> dict:
    """Check that the updated IR satisfies the core structural invariants.

    Compares the updated document against the original and reports violations:

    - role_count_violations: experience sections where len(updated.roles) ≠ len(original.roles)
    - role_boundary_violations: roles where a bullet looks like a role header
    - unbound_non_empty_paras: paragraphs with text but para_id=""
    - missing_section_ids: updated sections with empty section_id (had one in original)
    - layout_semantic_mismatches: layout_blocks para_ids not found in updated all_paras

    Logs one diagnostic code per category of violation found.
    """
    violations: dict = {
        "role_count_violations": 0,
        "role_boundary_violations": 0,
        "unbound_non_empty_paras": 0,
        "missing_section_ids": 0,
        "layout_semantic_mismatches": 0,
    }

    # Map original sections by section_id for comparison
    orig_by_id: dict[str, "ResumeSection"] = {
        s.section_id: s for s in original.sections if s.section_id
    }

    for sec in updated.sections:
        orig_sec = orig_by_id.get(sec.section_id) if sec.section_id else None

        if orig_sec is None and any(
            s.section_id == sec.section_id for s in original.sections
        ):
            violations["missing_section_ids"] += 1

        if sec.semantic_type == "experience":
            orig_for_count = orig_sec
            if orig_for_count is None:
                # Try to find by semantic match
                orig_for_count = next(
                    (s for s in original.sections if s.semantic_type == "experience"), None
                )
            if orig_for_count is not None and len(sec.roles) != len(orig_for_count.roles):
                violations["role_count_violations"] += 1
                _log.debug(
                    "ROLE_COLLAPSE_DETECTED: section %r has %d roles, expected %d",
                    sec.title, len(sec.roles), len(orig_for_count.roles),
                )
            for role in sec.roles:
                for bullet in role.bullets:
                    if _bullet_looks_like_role_title(bullet.text):
                        violations["role_boundary_violations"] += 1
                        _log.debug(
                            "ROLE_BOUNDARY_VIOLATION: bullet %r in role %r looks like role title",
                            bullet.text[:60], role.role_id,
                        )

    # Unbound paragraphs
    for pm in (updated.all_paras or []):
        if not pm.para_id and pm.text.strip():
            violations["unbound_non_empty_paras"] += 1
    if violations["unbound_non_empty_paras"]:
        _log.debug(
            "UNBOUND_PARAGRAPH_DETECTED: %d non-empty paras with para_id=''",
            violations["unbound_non_empty_paras"],
        )

    # Layout ↔ semantic consistency
    if updated.layout_blocks is not None:
        from tailor.compiler.models import LayoutParagraphBlock, LayoutTableBlock
        semantic_ids = {pm.para_id for pm in (updated.all_paras or []) if pm.para_id}
        for block in updated.layout_blocks:
            if isinstance(block, LayoutTableBlock):
                for pid in block.para_ids:
                    if pid and pid not in semantic_ids:
                        violations["layout_semantic_mismatches"] += 1
            elif isinstance(block, LayoutParagraphBlock):
                if block.para_id and block.para_id not in semantic_ids:
                    violations["layout_semantic_mismatches"] += 1
        if violations["layout_semantic_mismatches"]:
            _log.debug(
                "LAYOUT_SEMANTIC_MISMATCH: %d layout_blocks para_ids not in updated semantic model",
                violations["layout_semantic_mismatches"],
            )

    _log.debug("validate_structural_integrity: %s", violations)
    return violations


def validate_layout_density(
    original: "ResumeDocument",
    updated: "ResumeDocument",
) -> dict:
    """Compare text lengths between original and updated paragraphs to detect density overflow.

    A paragraph is flagged when:
    - its updated text is longer than max(200, original_length × 2.0), OR
    - its updated text contains newlines AND is > 1.3× the original length
      (indicating multi-bullet packing).

    Returns a dict with:
    - ``density_overflow_count``: paragraphs whose updated length exceeds the threshold
    - ``multi_bullet_packing_count``: paragraphs with newline-joined content (over-packed)
    """
    violations: dict = {"density_overflow_count": 0, "multi_bullet_packing_count": 0}
    orig_by_id: dict[str, "ParaModel"] = {
        pm.para_id: pm for pm in (original.all_paras or []) if pm.para_id
    }
    for pm in (updated.all_paras or []):
        if not pm.para_id:
            continue
        orig_pm = orig_by_id.get(pm.para_id)
        if orig_pm is None:
            continue
        orig_len = len(orig_pm.text.strip())
        updated_len = len(pm.text.strip())
        max_allowed = max(200, orig_len * 2.0)
        if updated_len > max_allowed:
            violations["density_overflow_count"] += 1
            _log.debug(
                "PARAGRAPH_DENSITY_OVERFLOW: para_id=%r orig=%d updated=%d",
                pm.para_id, orig_len, updated_len,
            )
        if "\n" in pm.text and updated_len > orig_len * 1.3 and orig_len > 0:
            violations["multi_bullet_packing_count"] += 1
            _log.debug("MULTI_BULLET_PACKING_DETECTED: para_id=%r", pm.para_id)
    _log.debug("validate_layout_density: %s", violations)
    return violations


def repair_layout_density(
    orig_para_map: "dict[str, ParaModel]",
    all_paras: "list[ParaModel]",
) -> None:
    """Truncate or strip multi-line packing from density-overflow paragraphs in-place.

    For each paragraph in *all_paras* whose updated text exceeds the density threshold,
    the text is replaced with only the first line (splitting on newlines) or truncated.
    This operates directly on the ParaModel objects so both all_paras and the
    section/role references are updated simultaneously.
    """
    for pm in all_paras:
        if not pm.para_id:
            continue
        orig_pm = orig_para_map.get(pm.para_id)
        if orig_pm is None:
            continue
        orig_len = len(orig_pm.text.strip())
        updated_text = pm.text.strip()
        max_allowed = max(200, orig_len * 2.0)

        needs_repair = (
            len(updated_text) > max_allowed
            or ("\n" in pm.text and len(updated_text) > orig_len * 1.3 and orig_len > 0)
        )
        if not needs_repair:
            continue

        # Truncate to first line; if still too long, hard-truncate to max_allowed
        first_line = pm.text.split("\n")[0].strip()
        if len(first_line) <= max_allowed and first_line:
            pm.text = first_line
        elif len(first_line) > max_allowed:
            pm.text = first_line[: int(max_allowed)].rstrip()
        else:
            pm.text = updated_text[: int(max_allowed)].rstrip()
        _log.debug("DENSITY_REPAIR_APPLIED: para_id=%r", pm.para_id)


def enforce_no_unbound_paragraphs(
    updated_sections: "list[ResumeSection]",
    effective_header_paras: "list[ParaModel]",
) -> None:
    """Mutate sections in-place to eliminate remaining unbound non-empty paragraphs.

    Called as a post-hoc safety net at the end of apply_tailored when
    layout_bound=True.  Unbound paragraphs (para_id=="", text non-empty) that
    survive bullet-packing or body-section-packing are packed into the last
    anchored paragraph in the same container, or dropped if no anchor exists.

    This is an invariant enforcement pass — ideally all unbound content was
    already resolved by the packing logic in _update_role and
    _update_body_section.  This function handles residual edge cases such as
    classification-path outputs and date-first layout repairs.
    """

    def _pack_or_drop(
        items: "list[ParaModel]",
        context: str,
    ) -> "list[ParaModel]":
        """Return a new list with unbound non-empty items packed into the last anchor."""
        unbound_texts = [p.text for p in items if not p.para_id and p.text.strip()]
        if not unbound_texts:
            return items
        # Build a map of para_id → (possibly updated) para for anchored items
        anchored: list[ParaModel] = [p for p in items if p.para_id and p.text.strip()]
        if anchored:
            packed_text = anchored[-1].text + "\n" + "\n".join(unbound_texts)
            anchored_map: dict[str, ParaModel] = {p.para_id: p for p in anchored}
            anchored_map[anchored[-1].para_id] = anchored[-1].with_text(packed_text)
            _log.debug(
                "STRUCTURAL_REPAIR_ATTEMPTED: packed %d unbound into %s",
                len(unbound_texts), context,
            )
        else:
            anchored_map = {}
            _log.debug(
                "STRUCTURAL_REPAIR_ATTEMPTED: dropped %d unbound (no anchor) from %s",
                len(unbound_texts), context,
            )
        # Rebuild: spacers kept; anchored replaced with (possibly packed) version; unbound dropped
        result: list[ParaModel] = []
        for p in items:
            if not p.text.strip():
                result.append(p)       # spacer / empty → keep
            elif p.para_id:
                result.append(anchored_map.get(p.para_id, p))  # anchored
            # unbound non-empty: drop
        return result

    # Header paras — normally never have unbound content, but check as a safety net
    if any(not p.para_id and p.text.strip() for p in effective_header_paras):
        effective_header_paras[:] = _pack_or_drop(effective_header_paras, "header_paras")

    for sec in updated_sections:
        # Body paras of non-experience sections
        if any(not p.para_id and p.text.strip() for p in sec.body_paras):
            sec.body_paras[:] = _pack_or_drop(
                sec.body_paras, f"sec:{sec.title[:30]}.body_paras"
            )
        # Role bullets
        for role in sec.roles:
            if any(not b.para_id and b.text.strip() for b in role.bullets):
                role.bullets[:] = _pack_or_drop(
                    role.bullets, f"role:{role.role_id[:30]}.bullets"
                )


# ---------------------------------------------------------------------------
# Anchored summary insertion (layout-bound mode)
# ---------------------------------------------------------------------------

def _find_summary_anchors(
    doc: "ResumeDocument",
) -> "tuple[ParaModel, ParaModel] | None":
    """Find two empty header paragraphs to anchor an inserted summary section.

    Scans header_paras for a trailing cluster of empty paragraphs (text.strip()
    == '') and returns the last two.  These are the slots closest to the first
    section heading and the most natural position for a professional summary.

    Returns (heading_anchor, body_anchor) or None if fewer than 2 candidates.
    """
    header_paras = doc.header_paras
    if len(header_paras) < 2:
        return None

    # Walk backwards to find the trailing cluster of empty paragraphs.
    cluster_start = len(header_paras)
    for i in range(len(header_paras) - 1, -1, -1):
        pm = header_paras[i]
        if pm.para_id and not pm.text.strip():
            cluster_start = i
        else:
            break

    trailing = header_paras[cluster_start:]
    if len(trailing) < 2:
        return None

    # Use the last two in the trailing cluster (closest to the first section).
    return trailing[-2], trailing[-1]


def _clean_summary_text(body_lines: "list[str]") -> str:
    """Produce a single clean string from LLM summary body_lines.

    - Strips a leading "Professional Summary:" prefix if present.
    - Drops "Current Date: ..." lines (belt-and-suspenders; injection script
      already removes these but the updater runs on raw LLM output too).
    - Joins remaining lines into one paragraph.
    """
    lines = [l.strip() for l in body_lines if l.strip()]
    # Remove "Current Date:" residuals
    lines = [
        l for l in lines
        if not re.match(r"^current\s+date\s*:", l, re.IGNORECASE)
    ]
    # Strip leading "Professional Summary:" label
    if lines and re.match(r"^professional\s+summary\s*:", lines[0], re.IGNORECASE):
        lines[0] = re.sub(
            r"^professional\s+summary\s*:\s*", "", lines[0], flags=re.IGNORECASE
        ).strip()
        if not lines[0]:
            lines.pop(0)
    return " ".join(lines).strip()


def _build_anchored_summary_section(
    llm_section: "LlmSection",
    heading_anchor: "ParaModel",
    body_anchor: "ParaModel",
) -> "ResumeSection":
    """Build a summary ResumeSection fully anchored to existing para_ids.

    heading_anchor and body_anchor must be empty paragraphs from header_paras
    with valid para_ids.  Their para_ids are reused so the layout renderer
    can place the new content exactly where those empty slots appear in the
    original DOCX.

    section_id is set to "sec_summary_inserted" so finalize_layout_bound_ir
    does not treat the section as synthetic (section_id != '').
    """
    heading_text = "PROFESSIONAL SUMMARY"
    body_text = _clean_summary_text(llm_section.body_lines)

    new_heading = heading_anchor.with_text(heading_text)
    new_heading_pm = ParaModel(
        text=new_heading.text,
        style=new_heading.style,
        semantic="section_heading",
        paragraph_profile=new_heading.paragraph_profile,
    )
    new_heading_pm.para_id = heading_anchor.para_id

    new_body = body_anchor.with_text(body_text)
    new_body_pm = ParaModel(
        text=new_body.text,
        style=new_body.style,
        semantic="paragraph",
        paragraph_profile=new_body.paragraph_profile,
    )
    new_body_pm.para_id = body_anchor.para_id

    return ResumeSection(
        title="Professional Summary",
        heading=new_heading_pm,
        semantic_type="summary",
        body_paras=[new_body_pm] if body_text else [],
        roles=[],
        section_id="sec_summary_inserted",
    )


# ---------------------------------------------------------------------------
# Layout-bound IR finalization (unconditional enforcement)
# ---------------------------------------------------------------------------

def finalize_layout_bound_ir(
    original: "ResumeDocument",
    updated_sections: "list[ResumeSection]",
    effective_header_paras: "list[ParaModel]",
    all_paras: "list[ParaModel]",
    content_enforcement: bool = False,
) -> None:
    """Enforce layout-bound structural invariants as a final cleanup step.

    Always runs when ``original.layout_blocks`` is present, regardless of
    ``USE_LAYOUT_BOUND_UPDATER``.

    Hard cleanup (always applied):
    1. Remove sections with section_id='' and non-empty content — they are
       synthetic (created by ``_make_extra_section``) and cannot be mapped to
       any layout_blocks entry, so the layout renderer cannot place them.

    Content enforcement (applied only when ``content_enforcement`` is True,
    i.e. when ``USE_LAYOUT_BOUND_UPDATER=True``):
    2. Enforce zero unbound paragraphs via ``enforce_no_unbound_paragraphs``.
    3. Apply layout density repair via ``repair_layout_density``.

    All modifications are in-place on the mutable lists passed as arguments.
    Callers must rebuild ``all_paras`` if sections are removed.
    """
    # 1. Hard cleanup: remove synthetic sections (section_id='' with content)
    # These can never be rendered by the layout_blocks renderer.
    synthetic_removed = 0
    i = 0
    while i < len(updated_sections):
        s = updated_sections[i]
        has_content = (
            any(p.text.strip() for p in s.body_paras)
            or any(r.header.text.strip() for r in s.roles)
        )
        if not s.section_id and has_content:
            _log.debug(
                "finalize_layout_bound_ir: removed synthetic section %r "
                "(section_id='', cannot be placed by layout_blocks renderer)",
                s.title,
            )
            updated_sections.pop(i)
            synthetic_removed += 1
        else:
            i += 1
    if synthetic_removed:
        _log.debug("finalize_layout_bound_ir: removed %d synthetic sections", synthetic_removed)

    # 2 & 3. Content enforcement (only when USE_LAYOUT_BOUND_UPDATER=True).
    # enforce_no_unbound_paragraphs and repair_layout_density were already
    # called in the _layout_bound branch above; avoid a redundant second pass.
    if content_enforcement:
        # Safety-net: check and log any residual unbound content.
        n_unbound = sum(1 for pm in all_paras if not pm.para_id and pm.text.strip())
        n_no_sid = sum(
            1 for s in updated_sections
            if not s.section_id and (
                any(p.text.strip() for p in s.body_paras)
                or any(r.header.text.strip() for r in s.roles)
            )
        )
        if n_unbound or n_no_sid:
            _log.debug(
                "finalize_layout_bound_ir: residual violations after enforcement — "
                "unbound_non_empty=%d synthetic_sections=%d",
                n_unbound, n_no_sid,
            )


# ---------------------------------------------------------------------------
# Anchor-budget enforcement (layout-bound mode)
# ---------------------------------------------------------------------------

#: Max chars for an inserted summary heading anchor (was originally empty).
SUMMARY_HEADING_BUDGET: int = 40

#: Max chars for an inserted summary body anchor (was originally empty).
#: Conservative estimate: ~28 chars/line at 18 pt in a half-page column × 7 lines.
SUMMARY_BODY_BUDGET: int = 200


def _compute_anchor_budgets(
    original: "ResumeDocument",
    updated: "ResumeDocument",
) -> "dict[str, int]":
    """Derive per-para_id replacement-text budgets from the original document.

    Rules by context
    ----------------
    * Empty header_paras that became the inserted-summary heading: ``SUMMARY_HEADING_BUDGET``
    * Empty header_paras that became the inserted-summary body:    ``SUMMARY_BODY_BUDGET``
    * Role bullet / body slot (experience):  ``max(160, orig_len * 1.25)``
    * Skills section body slots:             ``max(80,  orig_len * 1.25)``
    * Role headers:                          ``max(80,  orig_len * 1.5)``
    * Role meta lines:                       ``max(60,  orig_len * 1.5)``
    * Other section body (non-empty orig):   ``max(80,  orig_len * 1.5)``
    * Other empty body slot:                 ``SUMMARY_BODY_BUDGET`` (conservative)
    * Non-empty header paras:                ``max(80,  orig_len * 1.5)``

    The *updated* document is consulted only to identify which originally-empty
    header paragraphs were claimed as summary anchors so the correct tighter
    heading budget can be applied.
    """
    budgets: dict[str, int] = {}

    # Para_ids that were originally empty header paragraphs — these are the
    # candidates for summary heading/body anchors.
    _orig_empty_header: set[str] = {
        pm.para_id
        for pm in original.header_paras
        if pm.para_id and not pm.text.strip()
    }

    # ── Header paragraphs ──────────────────────────────────────────────────
    for pm in original.header_paras:
        if not pm.para_id:
            continue
        if pm.para_id in _orig_empty_header:
            # Conservative default; will be overridden for summary anchors below.
            budgets[pm.para_id] = SUMMARY_BODY_BUDGET
        else:
            orig_len = len(pm.text.strip())
            budgets[pm.para_id] = max(80, int(orig_len * 1.5))

    # ── Sections ───────────────────────────────────────────────────────────
    for sec in original.sections:
        for role in sec.roles:
            if role.header.para_id:
                ol = len(role.header.text.strip())
                budgets[role.header.para_id] = max(80, int(ol * 1.5))
            for m in role.meta_lines:
                if m.para_id:
                    ol = len(m.text.strip())
                    budgets[m.para_id] = max(60, int(ol * 1.5))
            for b in role.bullets:
                if b.para_id:
                    ol = len(b.text.strip())
                    budgets[b.para_id] = max(160, int(ol * 1.25))
        for bp in sec.body_paras:
            if not bp.para_id or bp.para_id in budgets:
                continue  # already set (e.g. same para_id shared by role + body)
            ol = len(bp.text.strip())
            if sec.semantic_type == "skills":
                budgets[bp.para_id] = max(80, int(ol * 1.25))
            elif ol > 0:
                budgets[bp.para_id] = max(80, int(ol * 1.5))
            else:
                budgets[bp.para_id] = SUMMARY_BODY_BUDGET  # empty slot default

    # ── Override for inserted summary anchors ──────────────────────────────
    # Paragraphs that were originally empty header slots but are now the
    # heading or body of an inserted summary section get tighter budgets.
    for sec in updated.sections:
        if sec.semantic_type == "summary" and sec.section_id == "sec_summary_inserted":
            if sec.heading.para_id in _orig_empty_header:
                budgets[sec.heading.para_id] = SUMMARY_HEADING_BUDGET
            for bp in sec.body_paras:
                if bp.para_id in _orig_empty_header:
                    budgets[bp.para_id] = SUMMARY_BODY_BUDGET

    return budgets


def _truncate_to_budget(pm: "ParaModel", budget: int) -> "ParaModel":
    """Return *pm* with text truncated to *budget* characters if necessary.

    Truncation strategy: cut at the last sentence-ending period that falls
    before the budget.  If no such period exists in the first half of the
    budget, hard-cut at ``budget - 3`` and append ``"..."``.
    Returns *pm* unchanged when the text is already within budget.
    """
    if len(pm.text) <= budget:
        return pm
    text = pm.text
    cut_at = text.rfind(". ", 0, budget)
    if cut_at >= budget // 2:
        truncated = text[: cut_at + 1]
    else:
        truncated = text[: budget - 3] + "..."
    _log.debug(
        "CONTENT_TRUNCATED_FOR_LAYOUT: para_id=%r budget=%d actual=%d",
        pm.para_id, budget, len(text),
    )
    return pm.with_text(truncated)


def apply_anchor_budgets(
    original: "ResumeDocument",
    updated: "ResumeDocument",
) -> "ResumeDocument":
    """Enforce per-paragraph text-length budgets on the layout-bound updated IR.

    Budgets are computed from the original paragraph lengths via
    ``_compute_anchor_budgets``.  Any paragraph whose replacement text exceeds
    its budget is truncated at the last sentence boundary (falling back to a
    hard cut with ``"..."``).

    Returns a new ``ResumeDocument``; neither *original* nor *updated* is
    mutated.  Called automatically by ``apply_tailored`` in layout-bound mode.
    """
    budgets = _compute_anchor_budgets(original, updated)

    def _t(pm: "ParaModel") -> "ParaModel":
        b = budgets.get(pm.para_id)
        return _truncate_to_budget(pm, b) if (b is not None and b > 0) else pm

    new_header: list[ParaModel] = [_t(p) for p in updated.header_paras]

    new_sections: list[ResumeSection] = []
    for sec in updated.sections:
        new_roles: list[RoleEntry] = []
        for role in sec.roles:
            new_roles.append(RoleEntry(
                header=_t(role.header),
                header_extra=list(role.header_extra),
                meta_lines=[_t(m) for m in role.meta_lines],
                bullets=[_t(b) for b in role.bullets],
                role_id=role.role_id,
                role_id_stable=role.role_id_stable,
            ))
        new_sections.append(ResumeSection(
            title=sec.title,
            heading=_t(sec.heading),
            semantic_type=sec.semantic_type,
            body_paras=[_t(p) for p in sec.body_paras],
            roles=new_roles,
            section_id=sec.section_id,
        ))

    # Rebuild all_paras in canonical order
    all_paras: list[ParaModel] = list(new_header)
    for s in new_sections:
        all_paras.append(s.heading)
        if s.semantic_type == "experience" and s.roles:
            for r in s.roles:
                all_paras.append(r.header)
                all_paras.extend(r.meta_lines)
                all_paras.extend(r.bullets)
        else:
            all_paras.extend(s.body_paras)

    return ResumeDocument(
        header_paras=new_header,
        sections=new_sections,
        layout=updated.layout,
        all_paras=all_paras,
        source_kind=updated.source_kind,
        layout_blocks=updated.layout_blocks,
        body_items=updated.body_items,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_tailored(
    original: ResumeDocument,
    llm_sections: list[LlmSection],
    classification: "ClassificationOutput | None" = None,
) -> ResumeDocument:
    """Apply LLM-tailored sections to the original document.

    Returns a new ResumeDocument with updated content; the original is not
    modified.  The all_paras flat list is rebuilt from the updated sections.

    When the LLM adds sections that are absent from the original (but all
    original sections are present), the extras are inserted at their LLM
    output position using cloned styles from the nearest existing sections.

    Parameters
    ----------
    original:
        Parsed template ResumeDocument.
    llm_sections:
        Parsed LLM output sections.
    classification:
        Optional upload-time ClassificationOutput.  When provided, the updater
        consults it for each section to enforce rewrite_policy, preserve_heading,
        and preserve_body_structure constraints.  Sections not found in the
        classification index fall back to existing behavior.  When None the
        function behaves identically to the pre-classification implementation.

    Raises
    ------
    ValueError
        If sections cannot be matched (see module docstring).
    """
    # Build fast lookup indices from classification (empty dicts when absent).
    _sec_cls: dict[str, ClassificationSection] = {}
    _role_cls: dict[str, ClassificationRole] = {}
    if classification is not None:
        for _cs in classification.sections:
            if _cs.section_id:
                _sec_cls[_cs.section_id] = _cs
            for _cr in _cs.roles:
                if _cr.role_id:
                    _role_cls[_cr.role_id] = _cr
        _log.debug(
            "apply_tailored: classification loaded — %d sections, %d roles indexed",
            len(_sec_cls), len(_role_cls),
        )

    # Layout-bound mode: active when layout_blocks are present and the flag is on.
    # normalize_llm_sections runs whenever layout_blocks exist (flag-independent):
    # it absorbs fake role-title sections and extracts role headers from bullets,
    # preventing structural mismatch before section matching even begins.
    from tailor.config import USE_LAYOUT_BOUND_UPDATER
    _layout_bound = original.layout_blocks is not None and USE_LAYOUT_BOUND_UPDATER
    if original.layout_blocks is not None:
        llm_sections = normalize_llm_sections(list(llm_sections))

    try:
        match = _match_sections(original.sections, llm_sections)
    except ValueError as exc:
        # Spec §9: ambiguous section mapping → do NOT modify → return template verbatim.
        _log.warning("Section mapping failed — returning template verbatim: %s", exc)
        return original

    def _apply_section(orig_section: ResumeSection, llm_section: LlmSection) -> ResumeSection:
        """Update orig_section with llm_section content, respecting lock rules."""
        if orig_section.semantic_type in _LOCKED_SEMANTIC_TYPES:
            # Spec §3: locked section — preserve source verbatim.
            return orig_section

        # Date-first experience layout: the parser produced malformed/collapsed
        # roles because dates appear before company/title in the template.
        # Detect and fix before any classification or normal dispatch.
        if orig_section.semantic_type == "experience" and _has_date_first_layout(orig_section):
            _log.debug(
                "date-first layout detected: section %r  "
                "original_role_count=%d  body_meta_count=%d",
                orig_section.title,
                len(orig_section.roles),
                sum(1 for p in orig_section.body_paras
                    if p.text.strip() and p.semantic == "role_meta"),
            )
            cls_sec = _sec_cls.get(orig_section.section_id) if _sec_cls else None
            if cls_sec is not None and cls_sec.rewrite_policy == "preserve":
                return orig_section
            rebuilt = _rebuild_date_first_roles(orig_section)
            return _update_experience_date_first(orig_section, llm_section, rebuilt)

        # Classification-constrained path: look up by stable section_id.
        cls_sec = _sec_cls.get(orig_section.section_id) if _sec_cls else None
        if cls_sec is not None:
            return _apply_section_classified(
                orig_section, llm_section, cls_sec, _role_cls, layout_bound=_layout_bound
            )

        # No classification (or section_id not in index) → existing behaviour.
        if orig_section.semantic_type == "experience":
            if orig_section.roles or llm_section.roles:
                return _update_experience_section(
                    orig_section, llm_section, layout_bound=_layout_bound
                )
            # No roles on either side — treat as body section to avoid content loss
            return _update_body_section(orig_section, llm_section, layout_bound=_layout_bound)
        if orig_section.semantic_type == "skills":
            # Spec §6: sanitize skills lines before inserting.
            sanitized = LlmSection(
                heading=llm_section.heading,
                semantic_type=llm_section.semantic_type,
                body_lines=_sanitize_skills_lines(llm_section.body_lines),
                roles=llm_section.roles,
            )
            return _update_body_section(orig_section, sanitized, layout_bound=_layout_bound)
        return _update_body_section(orig_section, llm_section, layout_bound=_layout_bound)

    # injectable_skills_section is set in the extras path when skills live in
    # header_paras.  Initialised here so the all_paras build (after both paths)
    # can reference it unconditionally.
    injectable_skills_section: LlmSection | None = None
    header_skill_target: tuple[int, int] | None = None

    # Para-ids claimed by anchored summary insertion.  These are removed from
    # effective_header_paras so they are not double-counted in all_paras.
    _used_anchor_ids: set[str] = set()

    if not match.extras:
        # ---- Fast path: no extras, keep original section order ----
        new_sections: list[ResumeSection] = []
        for orig_section, llm_section in match.pairs:
            if llm_section is None:
                new_sections.append(orig_section)
            else:
                new_sections.append(_apply_section(orig_section, llm_section))

    else:
        # ---- Extras path: follow LLM output order, splicing in extras ----
        # Content originals are all matched; 'other'-type originals (e.g. the
        # name/contact header block) may still have llm_section=None and are
        # kept verbatim, prepended before the LLM-ordered sections.

        # Guard: if match.pairs is empty (IR has 0 sections — e.g. PDF parsing
        # detected no section headings), we have no archetype to clone styles
        # from.  Return the original document verbatim rather than crashing.
        if not match.pairs:
            _log.warning(
                "apply_tailored: IR has 0 sections — cannot apply LLM output; "
                "returning original document verbatim."
            )
            return original

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
            else:
                # Locked sections register under LLM heading key so they are placed
                # at the correct LLM output position, not prepended as verbatim.
                heading_to_section[llm_section.heading.lower()] = (
                    _apply_section(orig_section, llm_section)
                )

        # Detect whether LLM skills extras should be injected into header_paras
        # rather than creating a new section.  This applies to templates where the
        # skills section lives in the left-column header area (no dedicated section
        # heading) and there is no existing skills section to match against.
        template_has_skills = any(s.semantic_type == "skills" for s in original.sections)
        header_skill_target = (
            _find_header_skills_block(original.header_paras)
            if not template_has_skills
            else None
        )

        # Pre-compute summary anchors for layout-bound mode.
        # Used when the LLM adds a summary the template does not have:
        # instead of dropping it, anchor it to existing empty header slots.
        _summary_anchors: "tuple[ParaModel, ParaModel] | None" = None
        if _layout_bound:
            _summary_anchors = _find_summary_anchors(original)
            if _summary_anchors:
                _log.debug(
                    "SUMMARY_ANCHORS_FOUND: heading_pid=%r body_pid=%r",
                    _summary_anchors[0].para_id, _summary_anchors[1].para_id,
                )
            else:
                _log.debug("SUMMARY_ANCHORS_NOT_FOUND: no safe empty header slots")

        # Iterate LLM output order; emit matched or extra sections.
        # Spec §5: extra experience sections are never created.
        # Skills extras that have a header target are injected there instead.
        llm_order_sections: list[ResumeSection] = []
        injectable_skills_section: "LlmSection | None" = None
        for llm_s in llm_sections:
            key = llm_s.heading.lower()
            if key in heading_to_section:
                llm_order_sections.append(heading_to_section[key])
            elif _is_experience_like(llm_s):
                # Spec §5: do NOT create new experience sections.
                _log.debug(
                    "apply_tailored: discarding extra experience section %r", llm_s.heading
                )
            elif llm_s.semantic_type in _LOCKED_SEMANTIC_TYPES:
                # Locked type with no template section match → silently drop.
                # The template's verbatim version (in header_paras or a locked
                # section) is preserved; the LLM copy is discarded.
                _log.debug(
                    "apply_tailored: discarding unmatched locked-type section %r",
                    llm_s.heading,
                )
            elif (
                llm_s.semantic_type == "skills"
                and header_skill_target is not None
                and injectable_skills_section is None  # first skills extra wins
            ):
                # Skills live in header_paras — update there, not as a section.
                injectable_skills_section = llm_s
                _log.debug(
                    "apply_tailored: routing skills extra %r to header_paras injection",
                    llm_s.heading,
                )
            elif _layout_bound:
                # In layout-bound mode, try anchored summary insertion first.
                # Non-summary extras are dropped to prevent unbound sections.
                if llm_s.semantic_type == "summary" and _summary_anchors is not None:
                    anchored = _build_anchored_summary_section(
                        llm_s, _summary_anchors[0], _summary_anchors[1]
                    )
                    llm_order_sections.append(anchored)
                    _used_anchor_ids.add(_summary_anchors[0].para_id)
                    _used_anchor_ids.add(_summary_anchors[1].para_id)
                    _summary_anchors = None  # consume anchors; only one summary
                    _log.debug(
                        "SUMMARY_INSERTED_ANCHORED: %r heading_pid=%r body_pid=%r",
                        llm_s.heading,
                        anchored.heading.para_id,
                        anchored.body_paras[0].para_id if anchored.body_paras else None,
                    )
                elif llm_s.semantic_type == "summary":
                    _log.debug(
                        "SUMMARY_INSERTION_SKIPPED_NO_ANCHORS: %r", llm_s.heading
                    )
                else:
                    _log.debug(
                        "UPDATER_SECTION_ANCHOR_NOT_FOUND: %r dropped in layout-bound mode",
                        llm_s.heading,
                    )
            else:
                llm_order_sections.append(_make_extra_section(llm_s, heading_arch, body_arch))

        # Build new_sections in the correct final order.
        #
        # Layout-bound mode: the layout_blocks tree defines physical position,
        # so the canonical order is the ORIGINAL section order, not the LLM
        # output order.  Anchored summary sections (inserted by the extras loop
        # above) are placed first; all other sections follow in their original
        # order with updates applied.
        #
        # Non-layout-bound mode: follow LLM output order (existing behaviour)
        # so that new sections added by the LLM appear in the expected position.
        if _layout_bound:
            anchored_summaries = [
                s for s in llm_order_sections if s.semantic_type == "summary"
            ]
            ordered_sections: list[ResumeSection] = []
            for orig_section, llm_section in match.pairs:
                if llm_section is None:
                    ordered_sections.append(orig_section)
                else:
                    key = llm_section.heading.lower()
                    ordered_sections.append(
                        heading_to_section.get(key, orig_section)
                    )
            new_sections = anchored_summaries + ordered_sections
        else:
            # Non-layout-bound: follow LLM output order with summary at top.
            template_has_summary = any(
                s.semantic_type == "summary" for s in original.sections
            )
            if not template_has_summary:
                summary_extras = [s for s in llm_order_sections if s.semantic_type == "summary"]
                other_llm = [s for s in llm_order_sections if s.semantic_type != "summary"]
                if summary_extras:
                    new_sections = summary_extras + verbatim_sections + other_llm
                else:
                    new_sections = verbatim_sections + llm_order_sections
            else:
                new_sections = verbatim_sections + llm_order_sections

    # Apply skills injection into header_paras when identified in the extras path.
    # injectable_skills_section / header_skill_target are None in the fast path.
    if injectable_skills_section is not None and header_skill_target is not None:
        effective_header_paras: list[ParaModel] = _inject_skills_into_header(
            original.header_paras,
            header_skill_target,
            injectable_skills_section,
            layout_bound=_layout_bound,
        )
    else:
        effective_header_paras = list(original.header_paras)

    # Remove anchor paragraphs claimed by anchored summary insertion from
    # effective_header_paras so they do not appear twice in all_paras.
    # (They now live inside the summary ResumeSection's heading/body_paras.)
    if _used_anchor_ids:
        effective_header_paras = [
            p for p in effective_header_paras
            if p.para_id not in _used_anchor_ids
        ]

    # Hard ban in layout-bound mode: remove any section that has non-empty content
    # but no section_id (i.e. it was created synthetic via _make_extra_section or
    # some other path that bypassed the section anchor).  Such sections have no
    # corresponding layout_blocks entry and cannot be rendered faithfully.
    if _layout_bound:
        _clean_sections: list[ResumeSection] = []
        for _s in new_sections:
            _has_content = (
                any(p.text.strip() for p in _s.body_paras)
                or any(r.header.text.strip() for r in _s.roles)
            )
            if not _s.section_id and _has_content:
                _log.debug(
                    "EXTRA_SECTION_SKIPPED_LAYOUT_BOUND: %r removed "
                    "(section_id='', non-empty content)",
                    _s.title,
                )
            else:
                _clean_sections.append(_s)
        if len(_clean_sections) < len(new_sections):
            new_sections = _clean_sections

    # layout-bound mode: enforce zero-unbound invariant before rebuilding all_paras.
    if _layout_bound:
        enforce_no_unbound_paragraphs(new_sections, effective_header_paras)

    # Rebuild flat para list in document order
    all_paras: list[ParaModel] = list(effective_header_paras)
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

    # Finalize layout-bound IR: runs when the full layout-bound mode is active.
    # The `content_enforcement=True` flag enables the full set of invariants
    # (unbound-para removal, density repair, synthetic section removal).
    # Without USE_LAYOUT_BOUND_UPDATER=True, this block does not execute and
    # the classic rendering path (with xml_proto) handles synthetic sections.
    if original.layout_blocks is not None and _layout_bound:
        _orig_sec_count = len(new_sections)
        finalize_layout_bound_ir(
            original, new_sections, effective_header_paras, all_paras,
            content_enforcement=True,
        )
        # If finalize removed synthetic sections, rebuild all_paras from the
        # cleaned section list so the final doc doesn't include removed content.
        if len(new_sections) < _orig_sec_count:
            all_paras = list(effective_header_paras)
            for section in new_sections:
                all_paras.append(section.heading)
                if section.semantic_type == "experience" and section.roles:
                    if any(bp.semantic == "role_header" for bp in section.body_paras):
                        for bp in section.body_paras:
                            if bp.semantic == "role_header":
                                break
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

    # Injectable extras: LLM summary sections that have no matching template section
    # but can be placed into an existing intro-prose paragraph in-place.
    # This handles templates where the intro sits inside an unnamed body paragraph
    # (e.g. table-sidebar templates) rather than having an explicit summary section.
    #
    # A summary extra is only injectable when there is an actual intro-prose paragraph
    # to receive the text.  When no such target exists, the extras path runs normally
    # and creates a new structural section (old behaviour, table structure dropped).
    injectable_extras = [
        e for e in match.extras
        if e.semantic_type == "summary" and any(l.strip() for l in e.body_lines)
    ]
    intro_para = _find_intro_prose_para(original) if injectable_extras else None
    # Extras are unhandled (preventing table path) when they are non-summary-type,
    # or when they are summary-type but no intro-prose target was found.
    # In layout-bound mode, all extras were either injected via intro_para or
    # dropped with a diagnostic — none are "unhandled" from the table path's view.
    if _layout_bound:
        has_unhandled_extras = False
    else:
        has_unhandled_extras = any(
            e not in injectable_extras or intro_para is None
            for e in match.extras
        ) if match.extras else False

    if has_table_blocks and not has_unhandled_extras:
        # Table in-place update: mutate ParaModel.text on the original objects
        # so _render_table_block picks up the new text from tb.para_models.
        # When extras are injectable summaries with a target, we also update the
        # intro-prose paragraph so the template's existing prose gets replaced.
        for orig_section, llm_section in match.pairs:
            if llm_section is None:
                continue
            if orig_section.semantic_type in _LOCKED_SEMANTIC_TYPES:
                continue  # Spec §3: locked sections are never updated in-place either.

            cls_sec = _sec_cls.get(orig_section.section_id) if _sec_cls else None

            # Classification: preserve → skip entirely.
            if cls_sec is not None and cls_sec.rewrite_policy == "preserve":
                _log.debug(
                    "classification(table): skip section %r (preserve)", orig_section.title
                )
                continue

            # Heading: respect preserve_heading.
            if cls_sec is None or not cls_sec.preserve_heading:
                orig_section.heading.text = llm_section.heading

            if orig_section.semantic_type == "experience":
                llm_roles = llm_section.roles or []
                if cls_sec is not None:
                    # Classification present: update bullets only, never header/meta.
                    for i, o_role in enumerate(orig_section.roles):
                        if i < len(llm_roles):
                            for o_b, n_b in zip(o_role.bullets, llm_roles[i].bullets):
                                o_b.text = n_b
                        # else: keep verbatim
                else:
                    # No classification: existing behaviour.
                    for o_role, n_role in zip(orig_section.roles, llm_roles):
                        o_role.header.text = n_role.header
                        for o_m, n_m in zip(o_role.meta_lines, n_role.meta_lines):
                            o_m.text = n_m
                        for o_b, n_b in zip(o_role.bullets, n_role.bullets):
                            o_b.text = n_b
            else:
                non_empty_orig = [p for p in orig_section.body_paras if p.text.strip()]
                llm_lines = [l for l in llm_section.body_lines if l.strip()]
                if orig_section.semantic_type == "skills":
                    llm_lines = _sanitize_skills_lines(llm_lines)
                # Both classified (preserve_body_structure) and unclassified paths
                # update only as many paras as exist (zip stops at shorter list).
                for o_p, new_text in zip(non_empty_orig, llm_lines):
                    o_p.text = new_text

        # Inject summary text into the intro-prose paragraph.
        # intro_para is guaranteed non-None here (checked in has_unhandled_extras above).
        if intro_para is not None:
            for extra_llm in injectable_extras:
                summary_text = " ".join(l for l in extra_llm.body_lines if l.strip())
                intro_para.text = summary_text
                _log.debug(
                    "apply_tailored: injected summary into intro-prose para "
                    "(first 60 chars: %r)", summary_text[:60]
                )

    # Final structural integrity check — scan all_paras for lingering unbound content.
    # In layout-bound mode this should be zero; any residual is a bug in the update
    # pipeline worth diagnosing immediately.
    _unbound_non_empty = sum(
        1 for pm in all_paras if not pm.para_id and pm.text.strip()
    )
    if _unbound_non_empty:
        _log.debug(
            "UNBOUND_PARAGRAPH_DETECTED: %d non-empty paras with para_id='' "
            "survived enforce pass in updated IR",
            _unbound_non_empty,
        )
        if _layout_bound:
            _log.debug("STRUCTURAL_REPAIR_FAILED: %d unbound paras remain after enforcement",
                       _unbound_non_empty)

    # Run structural validation gate in layout-bound mode and report violations.
    if _layout_bound:
        _sv = validate_structural_integrity(original, ResumeDocument(
            header_paras=effective_header_paras,
            sections=new_sections,
            layout=original.layout,
            all_paras=all_paras,
            source_kind=original.source_kind,
            layout_blocks=original.layout_blocks,
        ))
        if any(v > 0 for v in _sv.values()):
            _log.debug("STRUCTURAL_VALIDATION_GATE: violations=%s", _sv)

    _result = ResumeDocument(
        header_paras=effective_header_paras,
        sections=new_sections,
        layout=original.layout,
        all_paras=all_paras,
        source_kind=original.source_kind,
        # Return body_items only when the in-place table update actually ran.
        # When unhandled extras exist the in-place update was skipped, leaving
        # body_items with stale original text — pass None so the renderer falls
        # back to all_paras (correctly rebuilt by the extras path).
        body_items=original.body_items if (has_table_blocks and not has_unhandled_extras) else None,
        # Carry layout_blocks forward unconditionally so the renderer can use
        # serialized XML prototypes after a DB round-trip regardless of whether
        # the document uses tables or flat paragraphs.  para_id values on
        # with_text()-derived paragraphs (set in ParaModel.with_text) match the
        # layout_blocks entries so the renderer can look them up by ID.
        layout_blocks=original.layout_blocks,
    )

    # Apply per-slot text-length budgets in layout-bound mode.  Runs last so
    # all prior structural repairs (split-brain fix, section-order rewrite,
    # bullet overflow drop) have already been applied before truncation.
    if _layout_bound:
        _result = apply_anchor_budgets(original, _result)

    return _result

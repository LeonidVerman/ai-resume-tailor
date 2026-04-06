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
"""
from __future__ import annotations

import logging
import re
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

def _update_role(orig: RoleEntry, llm: LlmRole) -> RoleEntry:
    """Produce an updated RoleEntry from original + LLM data."""

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
    # When the LLM wrote roles in dash format (no "|"), parse_llm_output returns
    # body_lines instead of roles.  Re-parse and update only the bullets, keeping
    # the template's role headers and meta verbatim (dates, company, title).
    if not llm.roles and llm.body_lines and orig.roles:
        reparsed = _reparse_body_lines_as_roles(llm.body_lines)
        if reparsed:
            updated_roles: list[RoleEntry] = []
            for o_role, r_role in zip(orig.roles, reparsed):
                updated_roles.append(_update_role_bullets_only(o_role, r_role.bullets))
            # Template roles with no LLM counterpart are kept verbatim
            for o_role in orig.roles[len(reparsed):]:
                updated_roles.append(o_role)
            return ResumeSection(
                title=llm.heading,
                heading=_strip_col_break_para(orig.heading.with_text(llm.heading)),
                semantic_type=orig.semantic_type,
                body_paras=orig.body_paras,
                roles=updated_roles,
            )

    # Normal path: pipe-separated LLM roles matched by position.
    # Surplus originals are dropped, extra LLM roles clone from last orig.
    updated_roles = [_update_role(o, l) for o, l in zip(orig.roles, llm.roles)]

    if len(llm.roles) > len(orig.roles) and orig.roles:
        last_orig = orig.roles[-1]
        for extra_llm in llm.roles[len(orig.roles):]:
            updated_roles.append(_update_role(last_orig, extra_llm))

    return ResumeSection(
        title=llm.heading,
        heading=_strip_col_break_para(orig.heading.with_text(llm.heading)),
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


def _update_role_bullets_only(orig: RoleEntry, llm_bullets: list[str]) -> RoleEntry:
    """Return a copy of *orig* with bullets replaced by *llm_bullets*.

    The role header and meta_lines are preserved verbatim from the template.
    Used when the LLM wrote roles in dash format: the header text is unreliable
    (formatting differs from template) so only the bullet content is used.
    """
    arch = orig.bullets[0] if orig.bullets else orig.header
    new_bullets: list[ParaModel] = []
    for i, text in enumerate(llm_bullets):
        if i < len(orig.bullets):
            new_bullets.append(orig.bullets[i].with_text(text))
        else:
            new_bullets.append(arch.clone_as(text, "bullet"))
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
) -> list[ParaModel]:
    """Replace skill lines in *header_paras* with LLM skill content.

    *skill_range* is (start, end_exclusive) from _find_header_skills_block.
    Lines beyond the original skill-line count are appended as clones of
    the first original skill paragraph.
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

    arch = _clear_left_indent(orig_skill_paras[0])
    new_skill_paras: list[ParaModel] = []
    for i, line in enumerate(llm_lines):
        if i < len(orig_skill_paras):
            new_skill_paras.append(_clear_left_indent(orig_skill_paras[i].with_text(line)))
        else:
            new_skill_paras.append(arch.clone_as(line, "paragraph"))

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
        if orig_section.semantic_type == "experience":
            if orig_section.roles or llm_section.roles:
                return _update_experience_section(orig_section, llm_section)
            # No roles on either side — treat as body section to avoid content loss
            return _update_body_section(orig_section, llm_section)
        if orig_section.semantic_type == "skills":
            # Spec §6: sanitize skills lines before inserting.
            sanitized = LlmSection(
                heading=llm_section.heading,
                semantic_type=llm_section.semantic_type,
                body_lines=_sanitize_skills_lines(llm_section.body_lines),
                roles=llm_section.roles,
            )
            return _update_body_section(orig_section, sanitized)
        return _update_body_section(orig_section, llm_section)

    # injectable_skills_section is set in the extras path when skills live in
    # header_paras.  Initialised here so the all_paras build (after both paths)
    # can reference it unconditionally.
    injectable_skills_section: LlmSection | None = None
    header_skill_target: tuple[int, int] | None = None

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
            else:
                llm_order_sections.append(_make_extra_section(llm_s, heading_arch, body_arch))

        # When the LLM generates a new Professional Summary that has no match in
        # the template (summary extra), place it BEFORE the verbatim sections so
        # it appears at the top of the main content area.  In 2-column layouts this
        # puts the summary at the top of the wider right column (above experience);
        # in single-column layouts it precedes the experience entries naturally.
        # Only applies when the template itself has no summary section — if the
        # template already had a summary it would have been matched, not an extra.
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
            # Verbatim sections (name/contact block etc.) always precede the body.
            new_sections = verbatim_sections + llm_order_sections

    # Apply skills injection into header_paras when identified in the extras path.
    # injectable_skills_section / header_skill_target are None in the fast path.
    if injectable_skills_section is not None and header_skill_target is not None:
        effective_header_paras: list[ParaModel] = _inject_skills_into_header(
            original.header_paras, header_skill_target, injectable_skills_section
        )
    else:
        effective_header_paras = list(original.header_paras)

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
                if orig_section.semantic_type == "skills":
                    llm_lines = _sanitize_skills_lines(llm_lines)
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

    return ResumeDocument(
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
    )

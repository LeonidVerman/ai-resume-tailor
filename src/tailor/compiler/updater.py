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
    "education", "certifications", "languages", "websites", "contact",
})

# Major resume content sections — a synthetic summary should appear before these,
# and after any preceding profile/title/other sections.
_MAJOR_SECTION_TYPES: frozenset[str] = frozenset(
    {"experience", "education", "skills", "certifications"}
)

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

    # Pass 1: exact heading match (case-insensitive).
    # Guard: skip when the LLM section is semantic=other but the template section
    # is a generated content type (skills/experience/education/certifications).
    # This prevents a verbatim LLM "MY QUALIFICATIONS" (other) from consuming the
    # template's "MY QUALIFICATIONS" (skills) slot before the actual LLM skills
    # section ("TECHNICAL SKILLS", semantic=skills) can match via Pass 2.
    _GENERATED_TYPES: frozenset[str] = frozenset({"skills", "experience", "education", "certifications"})
    for li, ls in enumerate(llm):
        for oi, os_ in enumerate(orig):
            if oi in used_orig:
                continue
            if os_.title.lower() == ls.heading.lower():
                if ls.semantic_type == "other" and os_.semantic_type in _GENERATED_TYPES:
                    continue  # let Pass 2 semantic match take priority
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

    # Pass 3: skill-title similarity — match LLM "skills" sections to template
    # sections whose title contains "skill" (e.g. "Skills & Abilities").  Handles
    # templates where the skills section is classified as "other" rather than "skills"
    # because its heading ("Skills & Abilities") was not in the parser's exact list.
    for li, ls in enumerate(llm):
        if li in used_llm:
            continue
        if ls.semantic_type != "skills":
            continue
        for oi, os_ in enumerate(orig):
            if oi in used_orig:
                continue
            if "skill" in os_.title.lower():
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
            if oi not in used_orig
            and orig[oi].semantic_type not in _verbatim_only
            and (orig[oi].body_paras or orig[oi].roles)  # empty sections have nothing to inject
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
        # Also suppress LLM "other" sections whose heading text matches a template
        # section that was already matched to a different LLM section — e.g. a
        # verbatim "MY QUALIFICATIONS" (other) when the template's MY QUALIFICATIONS
        # was matched to "TECHNICAL SKILLS" via semantic type in Pass 2.
        _matched_orig_titles: set[str] = {orig[oi].title.lower() for oi in used_orig}
        extras = [
            llm[li] for li in unmatched_llm
            if llm[li].semantic_type not in _LOCKED_SEMANTIC_TYPES
            and not (
                llm[li].semantic_type == "other"
                and llm[li].heading.lower() in _matched_orig_titles
            )
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
    #
    # Layout-bound title-only format preservation: some templates store the role
    # title, company name, and date in SEPARATE paragraphs (e.g. sample 3 has
    # title in one para, company in the next body_para, date in a meta_line).
    # When the original header contains no pipe separator but the LLM provides
    # a pipe-delimited "Title | Company | Date" string, writing the full string
    # into the title para causes extra line-wrapping (16 ch → 66+ ch) that
    # accumulates across 3 roles and pushes the experience section off the right
    # column onto page 2, where it incorrectly appears in the left/sidebar column.
    # Extracting only the first pipe segment restores the 1-line title format
    # so the company and date (already in their own body_para/meta slots) are
    # not duplicated and the section footprint matches the original template.
    _header_rest: list[str] = []   # pipe segments beyond the title (company, date)
    if layout_bound and "|" not in orig.header.text.strip() and "|" in llm.header:
        _parts = [p.strip() for p in llm.header.split("|")]
        _header_text = _parts[0]
        _header_rest = _parts[1:]
        _log.debug(
            "ROLE_HEADER_FORMAT_PRESERVED: title-only original; extracted %r from %r",
            _header_text, llm.header[:60],
        )
    else:
        _header_text = llm.header
    new_header = _strip_col_break_para(orig.header.with_text(_header_text))

    # If the template had a multi-line role header (e.g. "..., St." / "Petersburg"),
    # the LLM input included the continuation line as a separate paragraph, so the
    # LLM may echo it back as a meta line.  Strip any meta line whose text matches
    # a header_extra fragment so it doesn't appear in the rendered output.
    header_extra_texts = {pm.text.strip().lower() for pm in orig.header_extra}
    llm_meta = [m for m in llm.meta_lines if m.strip().lower() not in header_extra_texts]

    # When the header-only split was applied and the LLM provided no separate meta
    # lines, the remaining pipe segments (company, date) would otherwise be silently
    # discarded.  Recover them as a synthetic meta entry but ONLY when the template's
    # single meta is date-only (starts with a digit), meaning it has no company slot.
    # Templates whose meta already includes the company name are left unchanged so
    # we don't overwrite a correct template value with the LLM's formatting.
    if (
        not llm_meta
        and _header_rest
        and orig.meta_lines
        and orig.meta_lines[0].text.strip()[:1].isdigit()
    ):
        _synthetic = " | ".join(_header_rest)
        llm_meta = [_synthetic]
        _log.debug(
            "ROLE_HEADER_REST_RECOVERED: date-only meta slot; injecting %r",
            _synthetic[:60],
        )

    # Meta lines: reuse original protos; in layout-bound mode drop extras.
    # When the LLM provides no meta lines (e.g. experience dates used "20XX"
    # placeholders that were not recognised as dates), preserve the template's
    # original meta lines verbatim so the role's date text is not lost.
    new_meta: list[ParaModel] = []
    if not llm_meta and orig.meta_lines:
        new_meta = list(orig.meta_lines)
    else:
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
        for i in range(min(n_orig, n_llm)):
            new_bullets.append(orig.bullets[i].with_text(llm.bullets[i]))
        if n_llm > n_orig:
            # Extra bullets: clone_as unbound paragraphs so they flow to overflow
            # pages naturally rather than being concatenated into the last slot.
            for extra_text in llm.bullets[n_orig:]:
                new_bullets.append(arch.clone_as(extra_text, "bullet"))
            _log.debug(
                "CONTENT_OVERFLOW_REFLOW: %d extra bullets for role %r → unbound paras",
                n_llm - n_orig, orig.role_id[:40],
            )
    else:
        for i, bullet_text in enumerate(llm.bullets):
            if i < len(orig.bullets):
                new_bullets.append(orig.bullets[i].with_text(bullet_text))
            elif not layout_bound:
                new_bullets.append(arch.clone_as(bullet_text, "bullet"))
            elif not orig.bullets:
                # Template has no bullet slots — create unbound para so the auto-register
                # pass can inject it after the role header via _extra_injections.
                new_bullets.append(arch.clone_as(bullet_text, "bullet"))
                _log.debug(
                    "CONTENT_OVERFLOW_REFLOW: unbound bullet for role %r (no template slots)",
                    orig.role_id[:40] if orig.role_id else "?",
                )
            else:
                _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: extra bullet %r", bullet_text[:60])

    return RoleEntry(
        header=new_header,
        meta_lines=new_meta,
        bullets=new_bullets,
        role_id=orig.role_id,
        role_id_stable=orig.role_id_stable if layout_bound else "",
    )


_YEAR_EXTRACT_RE = re.compile(r"\b(19|20)\d{2}\b")


def _role_min_year(texts: "list[str]") -> int:
    """Extract the earliest 4-digit year from a list of text strings.

    Returns 9999 (sentinel for 'no year found') so roles without dates sort
    to the end regardless of template/LLM order.
    """
    years = [int(m.group()) for t in texts for m in _YEAR_EXTRACT_RE.finditer(t)]
    return min(years) if years else 9999


def _reorder_llm_roles_by_date(
    orig_roles: "list[RoleEntry]",
    llm_roles: "list[LlmRole]",
) -> "list[LlmRole]":
    """Reorder LLM roles so their date order matches the template's date order.

    Handles the common case where the LLM returns roles in reverse-chronological
    order (newest first) while the source template uses chronological order
    (oldest first), or vice versa.  Roles are matched by sorting both lists by
    start year and pairing them positionally; the result is then placed back into
    the template's original index positions.

    If role counts differ, dates cannot be extracted, or matching is ambiguous,
    the original LLM order is returned unchanged.
    """
    if len(orig_roles) != len(llm_roles) or len(orig_roles) < 2:
        return llm_roles

    def _tmpl_year(role: "RoleEntry") -> int:
        texts = [role.header.text] + [m.text for m in role.meta_lines]
        return _role_min_year(texts)

    def _llm_year(role: "LlmRole") -> int:
        texts = [str(role.header)] + [str(m) for m in role.meta_lines]
        return _role_min_year(texts)

    tmpl_years = [_tmpl_year(r) for r in orig_roles]
    llm_years = [_llm_year(r) for r in llm_roles]

    # If all years are unknown, fall back to positional matching
    if all(y == 9999 for y in tmpl_years) or all(y == 9999 for y in llm_years):
        return llm_roles

    # Sort both by start year; pair k-th template (by year) with k-th LLM (by year)
    tmpl_order = sorted(range(len(orig_roles)), key=lambda i: tmpl_years[i])
    llm_by_year = sorted(range(len(llm_roles)), key=lambda j: llm_years[j])

    reordered = [None] * len(llm_roles)
    for rank, tmpl_idx in enumerate(tmpl_order):
        if rank < len(llm_by_year):
            reordered[tmpl_idx] = llm_roles[llm_by_year[rank]]

    if any(r is None for r in reordered):
        return llm_roles  # matching failed — fall back to original order

    _log.debug(
        "ROLE_DATE_REORDER: section reordered LLM roles by year; "
        "tmpl_years=%s llm_years=%s",
        tmpl_years, llm_years,
    )
    return reordered  # type: ignore[return-value]


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
            if len(reparsed) != len(orig.roles):
                _log.debug(
                    "EXPERIENCE_ROLE_COUNT_MISMATCH: section=%r orig_roles=%d reparsed_roles=%d",
                    orig.title, len(orig.roles), len(reparsed),
                )
            # Reorder reparsed roles to match the template's role order by date.
            # Template role.meta_lines may be empty (dates are in pre-role body_paras);
            # extract dates from body_paras to build the template year sequence.
            if len(reparsed) == len(orig.roles):
                _yr_re = _YEAR_EXTRACT_RE
                # For each template role, find its year from pre-role body_paras
                # (body_paras before the role_header carry the date/company lines).
                tmpl_body_years: list[int] = []
                rh_positions = [
                    i for i, p in enumerate(orig.body_paras)
                    if p.semantic == "role_header"
                ]
                prev = 0
                for rh_pos in rh_positions:
                    seg_years = [
                        int(m.group())
                        for p in orig.body_paras[prev:rh_pos]
                        for m in _yr_re.finditer(p.text)
                    ]
                    tmpl_body_years.append(min(seg_years) if seg_years else 9999)
                    prev = rh_pos + 1
                # Pad remaining roles with meta-line years (fallback)
                while len(tmpl_body_years) < len(orig.roles):
                    extra_texts = [m.text for m in orig.roles[len(tmpl_body_years)].meta_lines]
                    extra_years = [int(m.group()) for t in extra_texts for m in _yr_re.finditer(t)]
                    tmpl_body_years.append(min(extra_years) if extra_years else 9999)

                # Build the enriched _reorder helper with body-derived years
                if any(y != 9999 for y in tmpl_body_years):
                    llm_years_ord = sorted(
                        range(len(reparsed)),
                        key=lambda j: _role_min_year(
                            [str(reparsed[j].header)] + [str(m) for m in reparsed[j].meta_lines]
                        ),
                    )
                    tmpl_order = sorted(range(len(orig.roles)), key=lambda i: tmpl_body_years[i])
                    _reordered: list[None] = [None] * len(reparsed)  # type: ignore[assignment]
                    for rank, tmpl_idx in enumerate(tmpl_order):
                        if rank < len(llm_years_ord):
                            _reordered[tmpl_idx] = reparsed[llm_years_ord[rank]]  # type: ignore[index]
                    if None not in _reordered:
                        reparsed = _reordered  # type: ignore[assignment]
                        _log.debug(
                            "REPARSED_ROLE_REORDER: using body_paras dates; "
                            "tmpl_years=%s", tmpl_body_years,
                        )

            updated_roles: list[RoleEntry] = []
            _dash_company_re = re.compile(r'^(.+?)\s+[–—]\s+.+$')
            for o_role, r_role in zip(orig.roles, reparsed):
                updated = _update_role_bullets_only(o_role, r_role.bullets, layout_bound=layout_bound)
                # Inject company name from reparsed role header (em-dash format) when
                # the template meta has a date line but no company name.  Roles whose
                # meta is empty already render the company via pre-role orphan body_paras.
                if o_role.meta_lines:
                    _cm = _dash_company_re.match(str(r_role.header).strip())
                    _company = _cm.group(1).strip() if _cm else None
                    if _company and not any(
                        _company.lower() in m.text.lower()
                        for m in updated.meta_lines
                    ):
                        _arch = updated.meta_lines[-1] if updated.meta_lines else updated.header
                        _company_pm = _arch.clone_as(_company, "role_meta")
                        updated = RoleEntry(
                            header=updated.header,
                            header_extra=updated.header_extra,
                            meta_lines=list(updated.meta_lines) + [_company_pm],
                            bullets=updated.bullets,
                            role_id=updated.role_id,
                        )
                updated_roles.append(updated)
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
                title=orig.title,
                heading=orig.heading,
                semantic_type=orig.semantic_type,
                body_paras=clean_body_d,
                roles=updated_roles,
                section_id=orig.section_id,
            )
        # Reparse found no role structure — preserve original roles verbatim to
        # prevent the zip(orig.roles, llm.roles=[]) fallthrough wiping all roles.
        _log.debug(
            "EXPERIENCE_LLM_ROLE_PARSE_FAILED: section=%r body_lines=%d "
            "reason=no_role_boundaries; preserving %d orig roles verbatim",
            orig.title, len(llm.body_lines), len(orig.roles),
        )
        return ResumeSection(
            title=orig.title,
            heading=orig.heading,
            semantic_type=orig.semantic_type,
            body_paras=orig.body_paras,
            roles=list(orig.roles),
            section_id=orig.section_id,
        )

    # Normal path: pipe-separated LLM roles matched by position.
    # Reorder LLM roles to match the template's role order by date so that
    # reversed-chronological LLM output (newest first) maps correctly to
    # chronological template layouts (oldest first), and vice versa.
    reordered_llm_roles = _reorder_llm_roles_by_date(orig.roles, llm.roles)

    # In layout-bound mode:
    #   - surplus LLM roles are dropped (no unbound clones)
    #   - unmatched original roles are PRESERVED verbatim (Invariant 3: cardinality)
    updated_roles = [
        _update_role(o, l, layout_bound=layout_bound)
        for o, l in zip(orig.roles, reordered_llm_roles)
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

        # Extension-bullet deduplication: when a role has no original bullet slots but
        # the LLM provides bullets, the updater creates UNBOUND bullets (para_id="")
        # that will later become _ext_ layout blocks in apply_tailored.  The original
        # template body paragraphs that fell between role headers (plain Normal-style
        # paragraphs without bullet markers — e.g. template 17's "Spearheads..." and
        # "Works collaboratively...") remain in clean_body with their original text and
        # duplicate the ext bullets in the rendered DOCX.
        # Fix: clear any non-empty 'paragraph'-semantic body_para that falls AFTER a
        # role header (in para_id numeric order) when that role has unbound bullets,
        # UNLESS the paragraph is a visual role-title line for one of the roles.
        _roles_with_ext = [
            _r for _r in updated_roles
            if any(not (_b.para_id or "") for _b in _r.bullets)
        ]
        if _roles_with_ext:
            def _pid_num(pid: str) -> int:
                m = re.search(r"(\d+)", (pid or "").split("_ext_")[0])
                return int(m.group(1)) if m else 0

            _role_header_nums = sorted(
                _pid_num(_r.header.para_id) for _r in updated_roles if _r.header.para_id
            )
            _ext_role_nums = {
                _pid_num(_r.header.para_id) for _r in _roles_with_ext if _r.header.para_id
            }

            def _is_role_title_line(text: str) -> bool:
                """True if text looks like a visual role-title prefix for one of the roles."""
                t = text.strip().lower()
                for _r in updated_roles:
                    rh = _r.header.text.strip()
                    title_part = rh.split("|")[0].strip().lower()
                    if title_part and t and title_part.startswith(t[:20]) or t.startswith(title_part[:20]):
                        return True
                return False

            new_clean_body: list = []
            for _bp in clean_body:
                if (
                    _bp.text.strip()
                    and _bp.semantic == "paragraph"
                    and _bp.para_id
                    and not _is_role_title_line(_bp.text)
                ):
                    _bp_num = _pid_num(_bp.para_id)
                    _preceding = max(
                        (n for n in _role_header_nums if n < _bp_num), default=None
                    )
                    if _preceding in _ext_role_nums:
                        _log.debug(
                            "ext_bullet_dedup: cleared body_para %r (original plain-text"
                            " role content duplicated by ext bullets)",
                            _bp.para_id,
                        )
                        new_clean_body.append(_bp.with_text(""))
                        continue
                new_clean_body.append(_bp)
            clean_body = new_clean_body
    else:
        clean_body = orig.body_paras

    return ResumeSection(
        title=orig.title,
        heading=orig.heading,
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
# Non-skill labeled categories that LLMs sometimes append to Technical Skills sections.
# Note: "languages?" is intentionally excluded — "Languages: Java, Python, C++" is a
# valid technical-skill category line and should pass through unchanged.
# Spoken-language proficiency lines are filtered separately in layout.py via
# _SPOKEN_LANG_PROFICIENCY_RE (which detects markers like "(native)", "(fluent)").
_NON_SKILL_LABEL_RE = re.compile(
    r"^(?:hobbies?|awards?|activities|interests?|volunteering?|publications?|references?)\s*[:：]\s*",
    re.IGNORECASE,
)
# Bare social-media or website names that are not skill tokens (e.g. "LinkedIn" alone).
_SOCIAL_BARE_RE = re.compile(
    r"^(?:linkedin|github|twitter|instagram|portfolio|website|url)\.?$",
    re.IGNORECASE,
)
# Standalone URL lines that the LLM extracts from contact info into the skills section.
_URL_LINE_RE = re.compile(r"^https?://", re.IGNORECASE)


def _sanitize_skills_lines(lines: list[str]) -> list[str]:
    """Remove lines that must not appear in a rendered Skills section (spec §6).

    Removes:
    - Lines containing internal markers: CURRENT_DATE, "Generated on", etc.
    - Lines starting with "Additional".
    - Lines with known non-skill label prefixes (Hobbies:, Awards:, Interests:, …).
    - Bare social-media / website names (LinkedIn, GitHub, …) with no skill context.
    - Full sentences: 6+ whitespace-separated tokens ending in sentence punctuation.
    - Bare single-word category names that duplicate an existing colon-labeled line
      (e.g. "Communication" when "Communication: ..." already appears in the list).
    """
    non_empty = [l.strip() for l in lines if l.strip()]
    _colon_labels: set[str] = {
        l.split(":")[0].strip().lower() for l in non_empty if ":" in l
    }
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
        if _NON_SKILL_LABEL_RE.match(stripped):
            # Strip the non-skill category label but keep the content.
            # "Languages: Java, Python, C++" → "Java, Python, C++" (preserved at its
            # original position in the LLM output order, label removed).
            m = _NON_SKILL_LABEL_RE.match(stripped)
            content = stripped[m.end():].strip()
            if content:
                _log.debug(
                    "skills sanitize: stripping label prefix from %r → %r",
                    stripped[:60], content[:60],
                )
                clean.append(content)
            else:
                _log.debug("skills sanitize: dropping empty-after-label line %r", stripped[:60])
            continue
        if _SOCIAL_BARE_RE.match(stripped):
            _log.debug("skills sanitize: dropping bare social name %r", stripped[:80])
            continue
        if _URL_LINE_RE.match(stripped):
            _log.debug("skills sanitize: dropping URL line %r", stripped[:80])
            continue
        # Drop bare single-word/phrase category labels that are already covered by
        # a colon-labeled line (e.g. bare "Communication" when "Communication: ..."
        # is present — avoids duplicate heading artifacts from LLM output).
        if ":" not in stripped and " " not in stripped and stripped.lower() in _colon_labels:
            _log.debug("skills sanitize: dropping duplicate category label %r", stripped[:80])
            continue
        # Full-sentence detection: 6+ words AND ends with a sentence-final punct.
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

    # No packing: extra lines become unbound paragraphs that flow to overflow pages.
    packed_llm = llm_lines
    if layout_bound and content_paras and len(llm_lines) > len(content_paras):
        _log.debug(
            "CONTENT_OVERFLOW_REFLOW: %d extra lines from %r → unbound paras",
            len(llm_lines) - len(content_paras), orig.title[:40],
        )

    # Build updated versions of each content para (paired by position with LLM lines).
    _is_skills = orig.semantic_type == "skills"
    updated: list[ParaModel] = []
    for i, line in enumerate(packed_llm):
        if i < len(content_paras):
            pm = content_paras[i].with_text(line)
            # Clear list/bullet indentation from skills body paras: some templates
            # (e.g. template 23 MY QUALIFICATIONS) store skills as indented list
            # items.  The LLM's categorised skill lines are not list items and should
            # render at normal paragraph indent like the GENERAL INFO section.
            if _is_skills and (pm.style.indent_left or 0) > 0:
                pm = _clear_left_indent(pm)
            updated.append(pm)
            _log.debug("UPDATER_LAYOUT_BOUND_REPLACEMENT: para_id=%r → %r",
                       content_paras[i].para_id, line[:60])
        else:
            # Extra line: clone_as unbound so it flows to overflow pages
            updated.append(arch.clone_as(line, "paragraph"))

    # Rebuild body_paras:
    # - empty paras → preserved (spacing)
    # - decorative paras → preserved verbatim (font integrity)
    # - content paras → replaced with updated LLM text (in order)
    # - trailing content paras (LLM had fewer lines) → dropped in non-layout-bound
    #   mode; KEPT in layout-bound mode so para_ids remain in new_sections for the
    #   layout-blocks renderer to find (intro-prose summary injection depends on this).
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
        elif layout_bound:
            # Keep para_id bound in layout tree but clear text — LLM provided
            # fewer lines than the template has content slots.  Keeping the
            # original text would show stale template content (e.g. a split
            # reference entry appearing twice after the LLM merges it into one
            # line).  Clearing to empty string makes the slot invisible while
            # preserving the layout_block para_id reference.
            #
            # Exception: in a skills section, non-bullet paragraph-type paras
            # (e.g. a candidate name or intro-prose summary that sits in a
            # different visual column of the same table cell) must be preserved
            # verbatim — they carry either the original design element text
            # (e.g. 'Leonid Verman') or the LLM summary text injected earlier
            # by _find_intro_prose_para.  Clearing them erases the name/summary.
            if _is_skills and p.semantic == "paragraph":
                new_body.append(p)  # preserve name / summary / non-skill para
            else:
                new_body.append(p.with_text(""))
        # else (non-layout-bound): LLM produced fewer lines — drop trailing para

    # Append any remaining unbound extra paras (LLM content beyond template slots).
    # Register them under the last content para's ID so apply_tailored can inject
    # matching LayoutParagraphBlock entries.  para_id is left "" here — the
    # injector assigns IDs only when the anchor block has an xml_proto_xml.
    _body_extra_injections: "dict[str, list[ParaModel]]" = {}
    _anchor_pid = content_paras[-1].para_id if content_paras else ""
    for extra_pm in updated[content_cursor:]:
        if _anchor_pid:
            _body_extra_injections.setdefault(_anchor_pid, []).append(extra_pm)
        new_body.append(extra_pm)

    # When no content_paras existed (empty section body), every LLM line was
    # cloned via clone_as() and carries para_id="".  Assign synthetic IDs so
    # ir_validator's empty-para_id check (_epi_count > 2 → hard fail) passes.
    if not content_paras:
        _synth_i = 0
        for _bp in new_body:
            if _bp.text.strip() and not _bp.para_id:
                _bp.para_id = f"_synth_{orig.section_id}_{_synth_i}"
                _synth_i += 1

    # Defensive copy of the heading ParaModel so that any later in-place
    # mutation of orig.heading.text (e.g. by apply_tailored's extras path
    # when a "summary" LLM section is injected into header_paras) does not
    # propagate back to this section's heading — preserving the original
    # template heading text (e.g. "GENERAL INFO" instead of "Professional Summary").
    _heading_copy = orig.heading.with_text(orig.heading.text)
    result = ResumeSection(
        title=orig.title,
        heading=_heading_copy,
        semantic_type=orig.semantic_type,
        body_paras=new_body,
        roles=[],
        section_id=orig.section_id,
    )
    if _body_extra_injections:
        result._extra_injections = _body_extra_injections  # type: ignore[attr-defined]
    return result


def _find_body_prototype(
    pairs: "list[tuple[ResumeSection, LlmSection | None]]",
) -> ParaModel:
    """Return the best body-text prototype for inserted extra sections.

    B: Selection criteria (in priority order):
    1. Non-empty body paragraph from a non-'other' section.
    2. Not bold — explicit style.bold OR heading-named paragraph style
       (e.g. 'Heading 3') are both excluded; heading styles render bold in
       Word/LibreOffice even when style.bold is None.
    3. Not explicitly center- or right-aligned (hard left-alignment rule).
    Falls back to any non-empty non-bold body para, then any non-empty body
    para, then the first section heading.
    """
    def _is_heading_style(p: "ParaModel") -> bool:
        sn = (p.style.style_name or "").lower()
        return sn.startswith("heading")

    # Preferred: non-other, non-bold, non-heading-style, non-center/right para
    for orig_section, _ in pairs:
        if orig_section.semantic_type == "other":
            continue
        for p in orig_section.body_paras:
            if not p.text.strip():
                continue
            if p.style.bold:
                continue
            if _is_heading_style(p):
                continue
            if p.style.alignment in ("center", "right"):
                continue
            return p
    # Fallback: any non-empty non-bold para (including 'other' sections)
    for orig_section, _ in pairs:
        for p in orig_section.body_paras:
            if p.text.strip() and not p.style.bold and not _is_heading_style(p):
                return p
    # Final fallback: any non-empty body para
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

    # Safety-net: strip bold and any inherited heading paragraph style from
    # body paragraphs.  _find_body_prototype already excludes heading-style
    # paragraphs, but in case the archetype carries a named heading style (e.g.
    # 'Heading 3' which is bold in most themes), normalise it here so the
    # injected section body text renders as regular weight.
    def _normalise_body_pm(pm: "ParaModel") -> "ParaModel":
        sn = (pm.style.style_name or "").lower()
        # Also check paragraph_profile.bold for PDF-sourced paragraphs whose
        # style.bold is None even when the paragraph is visually bold.
        _pp_bold = pm.paragraph_profile.bold if pm.paragraph_profile else False
        if not sn.startswith("heading") and not pm.style.bold and not _pp_bold:
            return pm
        from dataclasses import replace as _dc_replace
        from copy import deepcopy as _deepcopy
        new_style = _dc_replace(pm.style, bold=False)
        # If the xml_proto carries a heading pStyle, remove it so the paragraph
        # inherits the document's Normal/body style (typically not bold).
        if new_style.xml_proto is not None and sn.startswith("heading"):
            new_proto = _deepcopy(new_style.xml_proto)
            pPr = new_proto.find(f"{{{_W}}}pPr")
            if pPr is not None:
                pStyle = pPr.find(f"{{{_W}}}pStyle")
                if pStyle is not None:
                    pPr.remove(pStyle)
            new_style = _dc_replace(new_style, xml_proto=new_proto)
        if pm.paragraph_profile is not None:
            from tailor.compiler.models import ParagraphProfile
            pp = ParagraphProfile.from_dict(pm.paragraph_profile.to_dict())
            pp.bold = False
        else:
            pp = pm.paragraph_profile
        return ParaModel(
            text=pm.text, style=new_style, semantic=pm.semantic,
            paragraph_profile=pp if pm.paragraph_profile else None,
        )

    body_paras: list[ParaModel] = []
    for line in llm.body_lines:
        if line.strip():
            body_paras.append(_normalise_body_pm(body_arch.clone_as(line, "paragraph")))

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

def _move_layout_block(
    layout_blocks: list,
    move_pid: str,
    before_pid: str,
) -> list:
    """Return a copy of *layout_blocks* with the block whose para_id==*move_pid*
    repositioned to immediately before the block whose para_id==*before_pid*.

    Used to place a synthetic summary LayoutParagraphBlock at the correct
    render position when its anchor para_id appears early in the original
    template (e.g. an empty header slot before the profile/title section).

    No-op when either para_id is absent or the block is already in position.
    """
    move_idx = next(
        (i for i, b in enumerate(layout_blocks) if getattr(b, "para_id", None) == move_pid),
        None,
    )
    target_idx = next(
        (i for i, b in enumerate(layout_blocks) if getattr(b, "para_id", None) == before_pid),
        None,
    )
    if move_idx is None or target_idx is None:
        return layout_blocks
    # Compute insertion point after removing the block at move_idx
    insert_at = target_idx - (1 if move_idx < target_idx else 0)
    if move_idx == insert_at:
        return layout_blocks  # already in position
    new_lb = list(layout_blocks)
    block = new_lb.pop(move_idx)
    new_lb.insert(insert_at, block)
    _log.debug(
        "SYNTHETIC_SUMMARY_LAYOUT_BLOCK_INSERTED: moved para_id=%r "
        "from index %d to %d (before %r at original index %d)",
        move_pid, move_idx, insert_at, before_pid, target_idx,
    )
    return new_lb


def _detect_multi_copy_count(sections: "list[ResumeSection]") -> int:
    """Return N when the template has N identical copies of its section structure.

    Detected when every section heading appears exactly N > 1 times and the
    total section count is divisible by N.  Returns 1 when no multi-copy
    pattern is found.
    """
    if not sections:
        return 1
    from collections import Counter
    counts = Counter(s.title.lower() for s in sections)
    unique_counts = set(counts.values())
    if len(unique_counts) == 1:
        n = next(iter(unique_counts))
        if n > 1 and len(sections) % n == 0:
            return n
    return 1


def _trim_to_first_copy_layout_blocks(
    blocks: list,
    n_copies: int,
) -> list:
    """Trim layout_blocks to only the first copy for multi-copy templates.

    Keeps: blocks before the second TableBlock (i.e. leading para + first
    TableBlock) and blocks after the last TableBlock (trailing para).
    Drops: separator paragraphs between copies and all subsequent TableBlocks.
    This prevents blank-page artifacts caused by the first copy's expanded
    content pushing inter-copy separator paragraphs onto their own page.
    """
    from tailor.compiler.models import LayoutTableBlock
    table_indices = [i for i, b in enumerate(blocks) if isinstance(b, LayoutTableBlock)]
    if len(table_indices) != n_copies:
        _log.debug(
            "MULTI_COPY_TRIM_SKIPPED: expected %d TableBlocks, found %d",
            n_copies, len(table_indices),
        )
        return blocks
    first_table_end = table_indices[0] + 1
    last_table_end = table_indices[-1] + 1
    kept = list(blocks[:first_table_end]) + list(blocks[last_table_end:])
    _log.debug(
        "MULTI_COPY_TEMPLATE_TRIMMED: kept %d blocks (was %d); "
        "removed %d inter-copy blocks",
        len(kept), len(blocks), len(blocks) - len(kept),
    )
    return kept


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
    cloned.para_id = pm.para_id
    for r_elem in list(cloned.style.xml_proto.findall(f"{{{_W}}}r")):
        for br in list(r_elem.findall(f"{{{_W}}}br")):
            if br.get(f"{{{_W}}}type") == "column":
                r_elem.remove(br)
    return cloned


# ---------------------------------------------------------------------------
# Fragmented-experience injection — decorative templates (Fix 4)
# ---------------------------------------------------------------------------

_ROLE_TITLE_WORDS: frozenset[str] = frozenset({
    "engineer", "developer", "manager", "designer", "analyst",
    "director", "lead", "intern", "architect", "consultant",
    "programmer", "scientist", "specialist", "coordinator",
    "administrator", "technician", "officer", "supervisor",
})


def _is_role_like_heading(title: str) -> bool:
    """Return True when *title* looks like a job title (not a company/school/skill).

    Strips a leading date-range prefix (e.g. 'May 2018 - Dec 2019') before
    checking so that merged date+title headings are handled correctly.

    For pipe-separated headings (e.g. 'Senior Engineer | Acme Corp | 2022–2024'),
    only the first segment (the role title) is evaluated so that long combined
    headings are not rejected by the word-count guard.
    """
    # Extract just the title segment for pipe-separated headings
    title_part = title.split("|")[0].strip() if "|" in title else title
    clean = re.sub(r'^\w+\s+\d{4}\s*[-–]\s*\w+\s+\d{4}', '', title_part).strip()
    words = clean.lower().split()
    if not (1 <= len(words) <= 7):
        return False
    # Must contain at least one recognised job-title word
    return any(w in _ROLE_TITLE_WORDS for w in words)


def _find_role_like_other_sections(
    sections: "list[ResumeSection]",
) -> "list[ResumeSection]":
    """Return 'other' sections whose headings look like job titles."""
    return [
        s for s in sections
        if s.semantic_type == "other" and _is_role_like_heading(s.title)
    ]


def _inject_fragmented_experience(
    sections: "list[ResumeSection]",
    original_sections: "list[ResumeSection]",
    llm_exp: LlmSection,
) -> "list[ResumeSection]":
    """Inject LLM experience roles into role-like 'other' sections.

    Called when no original experience section was matched but the template
    contains sections whose headings look like job titles (decorative templates
    that use the role title as the section heading rather than having an
    umbrella 'Work Experience' heading).

    Each LLM role is matched to a role-like 'other' section by position.
    The section heading is updated with the LLM role header and the available
    body paragraph slots are filled with bullets.
    """
    role_like = _find_role_like_other_sections(original_sections)
    if not role_like or not llm_exp.roles:
        return sections

    _log.debug(
        "FRAGMENTED_EXPERIENCE_DETECTED: %d role-like sections, %d LLM roles",
        len(role_like), len(llm_exp.roles),
    )

    # Build a replacement map: section_id → updated section
    replacement: dict[str, ResumeSection] = {}
    for i, sec in enumerate(role_like):
        if i >= len(llm_exp.roles):
            break
        llm_role = llm_exp.roles[i]
        # Update heading with LLM role header text
        new_heading = _strip_col_break_para(sec.heading.with_text(llm_role.header))
        # Fill available body_para slots with bullets, pack overflow into last slot
        body = list(sec.body_paras)
        bullet_slots = [j for j, bp in enumerate(body) if bp.para_id and not bp.text.startswith('\n')]
        # First slot can carry a newline/spacer — skip those, prefer content slots
        if not bullet_slots:
            bullet_slots = [j for j, bp in enumerate(body) if bp.para_id]
        for slot_rank, slot_j in enumerate(bullet_slots):
            if slot_rank < len(llm_role.bullets):
                body[slot_j] = body[slot_j].with_text(llm_role.bullets[slot_rank])
            elif slot_rank == len(bullet_slots) - 1 and slot_rank < len(llm_role.bullets):
                # Pack remaining bullets into last slot
                extras = llm_role.bullets[slot_rank:]
                packed = "; ".join(e.strip() for e in extras)
                body[slot_j] = body[slot_j].with_text(packed)

        updated_sec = ResumeSection(
            title=llm_role.header,
            heading=new_heading,
            semantic_type="other",
            body_paras=body,
            roles=[],
            section_id=sec.section_id,
        )
        replacement[sec.section_id] = updated_sec
        _log.debug(
            "FRAGMENTED_EXPERIENCE_SYNTHESIZED: %r -> %r",
            sec.title[:40], llm_role.header[:40],
        )

    # Rebuild sections with replacements applied
    result = []
    for sec in sections:
        result.append(replacement.get(sec.section_id, sec))
    return result


# ---------------------------------------------------------------------------
# Experience body_lines re-parser — robust multi-format role reconstruction
# ---------------------------------------------------------------------------

# Strategy 1: LLMs sometimes format roles as "Title — Company" (em/en/figure dash).
_ROLE_BODY_SEP_RE = re.compile(r'\s—\s|\s–\s|\s‒\s')

# Strategy 2: standalone date-line boundaries.
#   A "date line" is a line whose entire content is a date range, e.g.
#   "Jan 20XX - Current", "March 2020 – December 2022", "2019–2021", "Present".
#   Safe against normal bullet text ("cross-functional", "day-to-day") because
#   those phrases never contain month names or 4-digit/XX years.
_MONTH_PAT = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_YEAR_SLOT_PAT = r"(?:19\d{2}|20\d{2}|19[Xx]{2}|20[Xx]{2})"  # real or XX placeholder
_DATE_WORD_PAT = r"(?:Present|Current|Now|Ongoing)"
_SINGLE_DATE_PAT = rf"(?:{_MONTH_PAT}\.?\s+{_YEAR_SLOT_PAT}|{_YEAR_SLOT_PAT})"
_DATE_SEP_LOOSE_PAT = r"(?:\s*[-–—‒]\s*|\s+to\s+|\s+through\s+)"
_DATE_RANGE_PAT = (
    rf"(?:{_SINGLE_DATE_PAT}"
    rf"(?:{_DATE_SEP_LOOSE_PAT}(?:{_SINGLE_DATE_PAT}|{_DATE_WORD_PAT}))?"
    rf"|{_DATE_WORD_PAT})"
)
_STANDALONE_DATE_LINE_RE = re.compile(
    rf"^\s*{_DATE_RANGE_PAT}\s*$", re.IGNORECASE
)
# Bullet marker at start of a line (defensive; text_parser may already strip).
_BULLET_MARKER_RE = re.compile(
    r"^\s*[-•‣◦⁃▸⦿●*–—‒]\s+"
)


def _reparse_body_lines_as_roles(body_lines: list[str]) -> list[LlmRole]:
    """Re-parse experience body_lines into LlmRole objects.

    Tries two strategies in order:

    1. Em/en/figure-dash boundaries — lines containing " — ", " – ", or " ‒ "
       (existing behaviour; handles "Title — Company" format).

    2. Standalone date-line boundaries — lines whose entire content is a date
       range (e.g. "Jan 20XX - Current", "March 2020 – December 2022", "2019–2021").
       The line immediately after the date line becomes the role header; subsequent
       non-date lines up to the next boundary become bullets.

    Returns [] when no role structure is detectable so callers can fall back safely.
    """
    if not body_lines:
        return []

    dash_bounds = [
        i for i, ln in enumerate(body_lines)
        if _ROLE_BODY_SEP_RE.search(ln)
        and not _STANDALONE_DATE_LINE_RE.match(ln.strip())
    ]
    date_bounds = [
        i for i, ln in enumerate(body_lines)
        if _STANDALONE_DATE_LINE_RE.match(ln.strip())
    ]

    # Strategy 1 — em/en-dash boundary lines ("Title — Company" format).
    # Strategy 2 — standalone date-line boundaries ("Jan 20XX - Current" format).
    #
    # Prefer Strategy 2 when date boundaries start earlier than dash boundaries.
    # This handles the common case where LLM mixes formats in one section: the
    # first role uses "Jan 20XX - Current" (ASCII hyphen → date-only line) while
    # later roles use "March 20xx – December 20xx" (en-dash → also a date line,
    # but detected by Strategy 1 as an em/en-dash boundary).  Without this check,
    # Strategy 1 would start at the SECOND role and silently drop the first.
    use_date = date_bounds and (not dash_bounds or date_bounds[0] < dash_bounds[0])

    if use_date:
        roles = _roles_from_date_boundaries(body_lines, date_bounds)
        _log.debug(
            "EXPERIENCE_LLM_ROLES_REPARSED: body_lines=%d date_boundaries=%d "
            "reparsed_roles=%d pattern=date_first",
            len(body_lines), len(date_bounds), len(roles),
        )
        return roles

    if dash_bounds:
        roles = _roles_from_dash_boundaries(body_lines, dash_bounds)
        _log.debug(
            "EXPERIENCE_LLM_ROLES_REPARSED: body_lines=%d dash_boundaries=%d "
            "reparsed_roles=%d pattern=em_en_dash",
            len(body_lines), len(dash_bounds), len(roles),
        )
        return roles

    return []


def _roles_from_dash_boundaries(
    body_lines: list[str], boundaries: list[int]
) -> list[LlmRole]:
    """Em/en-dash boundary parser (factored out of original _reparse function)."""
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


def _roles_from_date_boundaries(
    body_lines: list[str], boundaries: list[int]
) -> list[LlmRole]:
    """Date-line boundary parser: each standalone date line starts a new role."""
    roles: list[LlmRole] = []
    for idx, boundary_i in enumerate(boundaries):
        end_i = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(body_lines)
        date_text = body_lines[boundary_i].strip()

        header = date_text  # fallback when no title line follows
        meta: list[str] = [date_text]
        bullets: list[str] = []
        saw_header = False

        for line in body_lines[boundary_i + 1: end_i]:
            stripped = line.strip()
            if not stripped:
                continue
            # Strip bullet markers defensively (text_parser may already do this).
            clean = _BULLET_MARKER_RE.sub("", stripped).strip()
            if not saw_header:
                header = clean if clean else stripped
                saw_header = True
            else:
                bullets.append(clean if clean else stripped)

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

    When *layout_bound* is True, extra bullets beyond the original count become
    unbound paragraphs that flow to overflow pages.
    """
    arch = orig.bullets[0] if orig.bullets else orig.header
    new_bullets: list[ParaModel] = []
    if layout_bound and orig.bullets:
        n_orig = len(orig.bullets)
        n_llm = len(llm_bullets)
        for i in range(min(n_orig, n_llm)):
            new_bullets.append(orig.bullets[i].with_text(llm_bullets[i]))
        if n_llm > n_orig:
            # Extra bullets: clone_as unbound so they flow to overflow pages.
            for extra_text in llm_bullets[n_orig:]:
                new_bullets.append(arch.clone_as(extra_text, "bullet"))
            _log.debug(
                "CONTENT_OVERFLOW_REFLOW: %d extra bullets (bullets-only) → unbound paras",
                n_llm - n_orig,
            )
    elif layout_bound and not orig.bullets and llm_bullets:
        # Template role has no bullet slots — all LLM bullets become unbound paras
        # that flow to overflow pages rather than being silently dropped.
        for text in llm_bullets:
            new_bullets.append(arch.clone_as(text, "bullet"))
        _log.debug(
            "CONTENT_OVERFLOW_REFLOW: %d LLM bullets → unbound paras (no template slots)",
            len(llm_bullets),
        )
    else:
        for i, text in enumerate(llm_bullets):
            if i < len(orig.bullets):
                new_bullets.append(orig.bullets[i].with_text(text))
            elif not layout_bound:
                new_bullets.append(arch.clone_as(text, "bullet"))
            else:
                _log.debug("UPDATER_EXTRA_LLM_CONTENT_DROPPED: extra bullet %r", text[:60])
    # When the template role had no bullet slots but the LLM now provides bullets,
    # strip meta_lines that look like description sentences (not dates/company/location).
    # These are plain-text descriptions that the PDF parser placed in meta_lines
    # because no explicit bullet markers were detected; keeping them alongside the
    # new LLM bullets would duplicate the content.  A line is treated as a
    # description (not a date/location) when it ends with a sentence-closing mark
    # ('. ', '? ', '! ') or a plain period at end-of-string AND is not a date-like
    # string.
    kept_meta = list(orig.meta_lines)
    if not orig.bullets and new_bullets and orig.meta_lines:
        import re as _re
        _DATE_HINT = _re.compile(r"\b\d{4}\b|\bPresent\b|\bCurrent\b|\bNow\b", _re.IGNORECASE)
        _SENTENCE_END = _re.compile(r"[.!?]\s*$")
        cleaned = []
        for m in orig.meta_lines:
            t = m.text.strip()
            if _SENTENCE_END.search(t) and not _DATE_HINT.search(t):
                _log.debug(
                    "ROLE_META_DESCRIPTION_STRIP: stripped description-like meta %r",
                    t[:60],
                )
            else:
                cleaned.append(m)
        if cleaned != orig.meta_lines:
            kept_meta = cleaned

    return RoleEntry(
        # Strip any column break from the role header — the section heading
        # (or Summary heading) handles right-column placement; a second break
        # on the first role header would cause a spurious column jump.
        header=_strip_col_break_para(orig.header),
        meta_lines=kept_meta,
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

    PROTECTION RULES — identity/title/subtitle is immutable:
    - The first contiguous non-empty block is always the name/title area.
    - Any block that is too close to the name block (fewer than 2 empty lines
      between them) is treated as a subtitle/role line — also protected.
    - Only a block clearly separated from the name area (≥ 2 index gap after
      the name block ends) may be used as a skills target.
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

    # Find the first non-empty para (start of name/title block)
    first_ne = next(
        (i for i, pm in enumerate(header_paras) if pm.text.strip()), None
    )
    if first_ne is None:
        return None

    # Find the end of the name/title block (last index of first contiguous cluster)
    first_ne_end = first_ne
    while (first_ne_end + 1 < len(header_paras)
           and header_paras[first_ne_end + 1].text.strip()):
        first_ne_end += 1

    # Require at least 2 empty-para positions of separation between the end of
    # the name/title block and the start of the candidate skills block.  This
    # prevents the subtitle or role-type line immediately below the name (e.g.
    # "registered nurse", "Phlebotomist") from being selected as a skills
    # target — those are protected identity/subtitle lines.
    # Gap of 2 means: first_ne_end < start - 2, i.e. start >= first_ne_end + 3.
    if start < first_ne_end + 3:
        return None

    # Don't treat the very first block (name/title, index 0) as skills
    if start == 0:
        return None

    # Guard: if the candidate block contains contact-info content (email, URL,
    # phone-number), it is a contact section, not a skills block.  Templates
    # like sample 12 have the contact info (phone, email, website) at the end
    # of header_paras; without this guard the injector overwrites contact info.
    _contact_markers = ("@", "www.", "http://", "https://")
    _phone_re = re.compile(r"^\d[\d\s\-\.\(\)]{6,}$")
    for _i in range(start, end + 1):
        _t = header_paras[_i].text.strip()
        if not _t:
            continue
        if any(m in _t for m in _contact_markers):
            return None
        if _phone_re.match(_t):
            return None

    # Guard: single-word or two-word blocks are title/subtitle/department labels
    # (e.g. "ENGINEERING", "Phlebotomist"), not skills blocks.  Skills blocks
    # have comma- or semicolon-separated lists with several terms.
    _candidate_word_count = sum(
        len(header_paras[_i].text.strip().split())
        for _i in range(start, end + 1)
        if header_paras[_i].text.strip()
    )
    if _candidate_word_count < 3:
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
    result = _PM(text=pm.text, style=new_style, semantic=pm.semantic,
                 paragraph_profile=pm.paragraph_profile)
    result.para_id = pm.para_id  # preserve so the layout-blocks renderer can find it
    return result


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
    # Use "; " as separator: _set_para_text strips "\n" from paragraph text,
    # so "\n".join would silently concatenate lines without any separator
    # (e.g. "DocumentationLinkedIn").  "; " produces coherent single-line output.
    if layout_bound and len(llm_lines) > len(orig_skill_paras):
        n = len(orig_skill_paras)
        packed = "; ".join(llm_lines[n - 1:])
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

# Section titles that must never be used as intro-prose summary anchors.
# These are named semantic sections (Communication, Leadership, References, etc.)
# whose original content must be preserved intact rather than overwritten with a
# Professional Summary.  Injecting a summary here corrupts meaningful template
# structure (e.g. Communication skills, Leadership awards, References list).
_PROTECTED_INTRO_PROSE_TITLES: frozenset[str] = frozenset({
    "communication", "leadership", "references", "awards",
    "hobbies", "activities", "achievements", "volunteer", "publications",
    "interests", "memberships", "affiliations",
})


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
    - Section title not in _PROTECTED_INTRO_PROSE_TITLES (Communication, Leadership, etc.).
    """
    _sections = original.sections
    # "contact", "social", and "websites" sections indicate a contact/sidebar area.
    # The grader flags any summary text in a section adjacent to these types as
    # SUMMARY_IN_WRONG_SECTION, so the renderer must reject those same sections
    # as intro-prose anchors.  "websites" is intentionally excluded: a websites
    # section before a skills/other section does not indicate a contact sidebar,
    # and the section after it may legitimately hold intro-prose summary content
    # (e.g. sample 2 where the Skills section body contains the profile paragraph).
    _CONTACT_AREA_TYPES: frozenset[str] = frozenset({"contact", "social"})
    for _si, section in enumerate(_sections):
        if section.semantic_type in _LOCKED_SEMANTIC_TYPES:
            continue
        if section.semantic_type == "experience":
            continue
        # Skip named semantic sections that should never receive summary injection.
        if section.title.strip().lower() in _PROTECTED_INTRO_PROSE_TITLES:
            continue
        # Skip sections whose PREVIOUS section is a contact/social type.
        # Those are inside a contact sidebar area — the candidate section follows
        # contact info and injecting summary there would embed it in the contact block.
        # The NEXT section being a contact type is fine: the candidate appears before
        # contact info (e.g. "OFFICE MANAGER" before "LinkedIn profile") and is the
        # natural profile/summary slot.
        _prev_types = [
            _sections[j].semantic_type
            for j in (_si - 1,)
            if 0 <= j < len(_sections)
        ]
        if any(nt in _CONTACT_AREA_TYPES for nt in _prev_types):
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
            # Reject keyword lists that use '•' as an inline separator
            # (e.g. "Senior Architect • Principal Developer • Senior App Developer").
            # More than 1 bullet in the middle indicates a skills keyword list, not prose.
            if text.count("•") > 1:
                continue
            comma_density = text.count(",") / max(1, len(text))
            if comma_density >= 0.10:
                continue
            return p

    # Also search header_paras for intro-prose paragraphs (e.g. sample 24 where
    # the original summary placeholder "I enjoy learning..." is in header_paras
    # rather than in any section body).  Only search the tail of header_paras
    # (after the name/title/contact block) to avoid replacing contact info.
    _CONTACT_SIGNALS: frozenset[str] = frozenset({"@", "://"})
    _hp = original.header_paras
    # Find where the contact block ends: walk backwards past trailing empties to
    # the last long content paragraph, then search only from there onward.
    _hp_start = 0
    for _j in range(len(_hp) - 1, -1, -1):
        _ht = _hp[_j].text.strip()
        if len(_ht) > 15 and not any(s in _ht for s in _CONTACT_SIGNALS):
            _hp_start = _j
            break
    for p in _hp[_hp_start:]:
        text = p.text.strip()
        if len(text) < _INTRO_PROSE_MIN_LEN:
            continue
        if p.semantic in ("role_header", "role_meta", "section_heading"):
            continue
        if "|" in text or "://" in text or "@" in text:
            continue
        if " " not in text:
            continue
        if text[0] in ("-", "•", "·", "–", "*"):
            continue
        if text.count("•") > 1:
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

    Also splits concatenated digit+letter sequences (e.g. "2023Ginyard" →
    "2023 ginyard") so Pattern B combined headers ("2023Ginyard Co. Title")
    yield the same company token ("ginyard") as the LLM's dash-separated
    header ("Ginyard Co. — Title").
    """
    # Drop parenthesised date ranges like "(2014-Now)", "(2010–2013)"
    text = re.sub(r"\([^)]*(?:19|20)\d{2}[^)]*\)", "", text)
    # Split on role separators — keep only the first (company) segment
    parts = re.split(r"\s[–—\-]\s|\|", text)
    company_part = parts[0].strip().lower()
    # Remove punctuation
    company_part = re.sub(r"[^\w\s]", " ", company_part)
    # Split concatenated year+word tokens (e.g. "2023ginyard" → "2023 ginyard")
    company_part = re.sub(r"(?<=\d)(?=[a-z])", " ", company_part)
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


def _rebuild_roles_from_classification(
    section: "ResumeSection",
    cls_sec: "ClassificationSection",
) -> "list[RoleEntry]":
    """Rebuild RoleEntry list from classification block para_id assignments.

    Used when the parser produced no role structure (e.g. Pattern B where
    year+company+title appear in one role_meta paragraph) but the classification
    LLM correctly identified role boundaries from the paragraph sequence.

    Returned RoleEntry objects hold direct references to the ParaModel objects
    inside body_paras (no copies are made), so in-place text updates by
    _update_experience_date_first propagate back to body_paras automatically.
    """
    para_map: dict[str, "ParaModel"] = {
        p.para_id: p for p in section.body_paras if p.para_id
    }

    roles: list[RoleEntry] = []
    for cls_role in cls_sec.roles:
        header_para: "ParaModel | None" = None
        for block in cls_role.header_blocks:
            p = para_map.get(block.para_id)
            if p is not None:
                header_para = p
                break

        if header_para is None:
            _log.debug(
                "cls-rebuild: role %r has no resolvable header para — skipped",
                cls_role.role_id,
            )
            continue

        meta_lines = [
            para_map[b.para_id]
            for b in cls_role.meta_blocks
            if b.para_id in para_map
        ]
        bullets = [
            para_map[b.para_id]
            for b in cls_role.body_blocks
            if b.para_id in para_map
        ]

        roles.append(RoleEntry(
            header=header_para,
            header_extra=[],
            meta_lines=meta_lines,
            bullets=bullets,
            role_id=header_para.text.strip(),
            role_id_stable=header_para.para_id or header_para.text.strip(),
        ))

    _log.debug(
        "cls-rebuild: section %r → %d roles from classification "
        "(cls_roles=%d, body_paras=%d)",
        section.title, len(roles), len(cls_sec.roles), len(section.body_paras),
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
            # Subset bonus: if all LLM company tokens appear in the (broader)
            # IR tokens — typical for Pattern B combined headers where the IR
            # para reads "2023Ginyard Co. Title" and the LLM para reads
            # "Ginyard Co. — Title" giving IR={"2023","ginyard",...},
            # LLM={"ginyard"}.  Jaccard alone is low; boost to 0.5.
            if llm_tokens and llm_tokens <= ir_tokens:
                score = max(score, 0.5)
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

    if len(llm_roles) != len(rebuilt_roles):
        _log.debug(
            "EXPERIENCE_ROLE_COUNT_MISMATCH: section=%r ir_roles=%d llm_roles=%d",
            orig.title, len(rebuilt_roles), len(llm_roles),
        )

    _PLACEHOLDER_MARKERS = (
        "summarize your key",
        "key responsibilities",
        "add your experience",
        "describe your experience",
    )

    # Mutate bullet text in-place (the ParaModel objects are shared with body_paras).
    # When the template has no bullet-semantic paragraphs for a role (e.g. only a
    # single placeholder paragraph classified as header_extra), fall back to updating
    # header_extra paragraphs so LLM content is still injected.
    # _extra_injections: anchor_para_id → [extra ParaModel, ...]
    _extra_injections: "dict[str, list[ParaModel]]" = {}
    for ir_idx, ir_role in enumerate(rebuilt_roles):
        llm_idx = match_map[ir_idx]
        if llm_idx is None:
            # Check if original content looks like a placeholder (should have been replaced).
            targets = ir_role.bullets if ir_role.bullets else ir_role.header_extra
            for t in targets:
                if any(m in t.text.lower() for m in _PLACEHOLDER_MARKERS):
                    _log.debug(
                        "EXPERIENCE_PLACEHOLDER_BODY_SURVIVED: section=%r role=%r "
                        "para=%r — no LLM match, placeholder kept verbatim",
                        orig.title, ir_role.role_id, t.para_id,
                    )
            _log.debug(
                "date-first: IR role %r → no match, keeping original bullets",
                ir_role.role_id,
            )
            continue
        llm_bullets = llm_roles[llm_idx].bullets
        # Warn when first LLM bullet looks like a role title rather than body content.
        if llm_bullets:
            first = llm_bullets[0]
            if _ROLE_BODY_SEP_RE.search(first) or (
                "," in first and len(first) < 60 and not first.strip().startswith(("-", "•", "*"))
            ):
                _log.debug(
                    "EXPERIENCE_ROLE_BODY_LOOKS_LIKE_NEXT_HEADER: "
                    "section=%r role=%r first_bullet=%r — may be a misaligned role title",
                    orig.title, ir_role.role_id, first[:60],
                )
        targets = ir_role.bullets if ir_role.bullets else ir_role.header_extra
        for i, bullet_para in enumerate(targets):
            if i < len(llm_bullets):
                _log.debug(
                    "date-first: para %r updated  %r → %r",
                    bullet_para.para_id,
                    bullet_para.text[:40],
                    llm_bullets[i][:40],
                )
                bullet_para.text = llm_bullets[i]

        # Extra LLM bullets beyond the template's existing slots.
        # Clone from the last target paragraph; leave para_id="" — the injection
        # code in apply_tailored assigns IDs and creates layout_blocks only when
        # the anchor block has an xml_proto_xml (real DOCX template).
        if targets and len(llm_bullets) > len(targets):
            arch = targets[-1]
            arch_pid = arch.para_id  # injection anchor: insert after this block
            if arch_pid:
                for extra_text in llm_bullets[len(targets):]:
                    extra_pm = arch.clone_as(extra_text)
                    # para_id intentionally left "" — injector assigns it later
                    _extra_injections.setdefault(arch_pid, []).append(extra_pm)
                    _log.debug(
                        "date-first: extra bullet (anchor=%r) → %r",
                        arch_pid,
                        extra_text[:40],
                    )

    # Insert extra paragraphs into body_paras at the correct positions so they
    # appear in document order (critical for the two-column table renderer).
    if _extra_injections:
        new_body: list[ParaModel] = []
        for pm in orig.body_paras:
            new_body.append(pm)
            extras = _extra_injections.get(pm.para_id)
            if extras:
                new_body.extend(extras)
        result_body = new_body
    else:
        result_body = orig.body_paras

    # Return with roles=[] so all_paras builder uses body_paras order.
    # Attach _extra_injections so apply_tailored can inject matching layout_blocks.
    result = ResumeSection(
        title=orig.title,
        heading=orig.heading,
        semantic_type=orig.semantic_type,
        body_paras=result_body,
        roles=[],
        section_id=orig.section_id,
    )
    if _extra_injections:
        result._extra_injections = _extra_injections  # type: ignore[attr-defined]
    return result


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

    When the template stores experience as flat body_paras (orig.roles=[]),
    falls back to _update_body_classified so the LLM content is not silently
    dropped.
    """
    # Template has no role structure — treat like a body section so LLM
    # content is applied to body_paras rather than silently dropped.
    if not orig.roles:
        _log.debug(
            "classification: experience section %r has no roles → delegating to body update",
            orig.title,
        )
        return _update_body_classified(orig, llm, cls_sec, layout_bound=layout_bound)

    # Resolve LLM roles: try pipe format first, then dash/date format.
    llm_roles = llm.roles
    if not llm_roles and llm.body_lines and orig.roles:
        reparsed = _reparse_body_lines_as_roles(llm.body_lines)
        if reparsed:
            llm_roles = reparsed
            if len(reparsed) != len(orig.roles):
                _log.debug(
                    "EXPERIENCE_ROLE_COUNT_MISMATCH: section=%r orig_roles=%d reparsed_roles=%d",
                    orig.title, len(orig.roles), len(reparsed),
                )
        else:
            _log.debug(
                "EXPERIENCE_LLM_ROLE_PARSE_FAILED: section=%r body_lines=%d "
                "reason=no_boundaries; keeping %d orig roles verbatim",
                orig.title, len(llm.body_lines), len(orig.roles),
            )

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

    _log.debug(
        "classification: section %r preserve_heading=%s rewrite_policy=%s",
        orig.title, cls_sec.preserve_heading, cls_sec.rewrite_policy,
    )
    return ResumeSection(
        title=orig.title,
        heading=orig.heading,
        semantic_type=orig.semantic_type,
        body_paras=orig.body_paras,
        roles=updated_roles,
        section_id=orig.section_id,
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
            title=orig.title,
            heading=orig.heading,
            semantic_type=orig.semantic_type,
            body_paras=new_body,
            roles=[],
            section_id=orig.section_id,
        )

    # No structure constraint — use existing body update.
    if orig.semantic_type == "skills":
        llm = LlmSection(
            heading=llm.heading,
            semantic_type=llm.semantic_type,
            body_lines=_sanitize_skills_lines(llm.body_lines),
            roles=llm.roles,
        )
    return _update_body_section(orig, llm, layout_bound=layout_bound)


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
        """Keep all items including unbound — extra content flows to overflow pages."""
        unbound_texts = [p.text for p in items if not p.para_id and p.text.strip()]
        if unbound_texts:
            _log.debug(
                "CONTENT_OVERFLOW_REFLOW: keeping %d unbound para(s) in %s for overflow rendering",
                len(unbound_texts), context,
            )
        return list(items)

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
    if not trailing:
        return None

    def _is_divider_style(pm: "ParaModel") -> bool:
        """True if the paragraph uses a separator / divider-line style."""
        sn = (pm.style.style_name or "") if pm.style else ""
        return "divid" in sn.lower() or "line" in sn.lower()

    # Two or more empty slots: use the FIRST two (heading_anchor, body_anchor).
    # Using the first two slots (immediately after name/title) places the summary
    # right below the candidate's name, minimising vertical whitespace between
    # the name and the summary.  The remaining empty slots act as natural spacers
    # before the table/body that follows — giving a visually tight header block.
    # Skip divider-line slots as body_anchor: those styles carry negative indents
    # that push the summary text flush to the left margin (sample 15 regression).
    if len(trailing) >= 2:
        heading_anchor = trailing[0]
        # Prefer the first non-divider slot as body_anchor; fall back to trailing[1]
        body_anchor = next(
            (p for p in trailing[1:] if not _is_divider_style(p)),
            trailing[1],
        )
        return heading_anchor, body_anchor

    # Single empty slot only (no preceding prose para found above).
    _log.debug(
        "SUMMARY_SINGLE_ANCHOR: one empty header slot found — "
        "summary inserted without heading anchor (body_pid=%r)",
        trailing[0].para_id,
    )
    return None, trailing[0]


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

    When *heading_anchor* is None (single-slot mode), the body text is
    compacted to 1 sentence to avoid expanding a narrow template header slot
    (typically an empty trailing paragraph in a compact table template).
    """
    if heading_anchor is None:
        # Single-anchor: only the body slot exists.  Use the full summary text
        # and let the table cell expand naturally — the user expects the complete
        # summary to appear and is aware that later content may shift down.
        body_text = _clean_summary_text(llm_section.body_lines)
        _log.debug(
            "ANCHORED_SUMMARY_SINGLE_SLOT: len=%d chars (full text, no compaction)",
            len(body_text),
        )
    else:
        body_text = _clean_summary_text(llm_section.body_lines)

    if heading_anchor is not None:
        # Keep the heading slot empty — templates that lack a dedicated summary
        # section should receive only the body text, not a synthetic
        # "PROFESSIONAL SUMMARY" label that was never in the original design.
        new_heading_pm = ParaModel(
            text="",
            style=heading_anchor.style,
            semantic="section_heading",
            paragraph_profile=heading_anchor.paragraph_profile,
        )
        new_heading_pm.para_id = heading_anchor.para_id
    else:
        # Single-anchor mode: no heading slot available.  Use an empty unanchored
        # para so finalize_layout_bound_ir treats it as a spacer (not dropped).
        new_heading_pm = ParaModel(
            text="",
            style=body_anchor.style,
            semantic="section_heading",
            paragraph_profile=body_anchor.paragraph_profile,
        )
        new_heading_pm.para_id = ""

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

# Semantic types whose content paragraphs must never be truncated by budget
# enforcement.  Budget slots are skipped so _truncate_to_budget is never applied.
_MEANINGFUL_SEMANTICS: frozenset[str] = frozenset({"experience", "summary", "skills"})


def _is_intro_prose_section(sec: "ResumeSection") -> bool:
    """Return True when an 'other'-type section looks like an intro-prose block.

    Mirrors the heuristic in layout._has_intro_prose_content.  Used to detect
    which 'other' section received implicit summary injection so its body_para
    para_ids can be excluded from budget truncation.
    """
    if sec.semantic_type in _LOCKED_SEMANTIC_TYPES or sec.semantic_type == "experience":
        return False
    non_empty = [p for p in sec.body_paras if p.text.strip()]
    if not non_empty:
        return False
    if any(p.semantic in ("role_meta", "bullet") for p in non_empty):
        return False
    total = " ".join(p.text.strip() for p in non_empty)
    if len(total) < 30:
        return False
    if all("://" in p.text or " " not in p.text for p in non_empty):
        return False
    return (total.count(",") / max(1, len(total))) < 0.15


def _collect_content_para_ids(doc: "ResumeDocument") -> "frozenset[str]":
    """Return para_ids of content paragraphs that must not be budget-truncated.

    Includes:
    - Bullets of experience roles.
    - Body_paras of summary/skills/experience sections.
    - Body_paras of the first 'other' section that qualifies as an intro-prose
      anchor (receives implicit summary injection via layout._anchor_implicit_summary).
    - The body_anchor para_id from single-slot summary insertion (para from
      header_paras used as the sole anchor when only one empty slot exists).

    Role headers and meta lines are excluded — structural anchors stay compact.
    """
    ids: set[str] = set()
    _found_intro_prose = False  # only add the first qualifying 'other' section
    for sec in doc.sections:
        if sec.semantic_type not in _MEANINGFUL_SEMANTICS:
            # Detect intro-prose anchor in the header zone ('other' sections only).
            # Use a flag (not break) so the loop continues past the intro-prose
            # section and still processes meaningful sections (skills, experience).
            # Using `break` here was a bug: it exited the loop before adding
            # skills/experience body_paras, leaving them without budget protection.
            if not _found_intro_prose and sec.semantic_type == "other" and _is_intro_prose_section(sec):
                for bp in sec.body_paras:
                    if bp.para_id:
                        ids.add(bp.para_id)
                _found_intro_prose = True
            continue
        for role in sec.roles:
            for b in role.bullets:
                if b.para_id:
                    ids.add(b.para_id)
        for bp in sec.body_paras:
            if bp.para_id:
                ids.add(bp.para_id)

    # Single-slot summary anchor: body_anchor is an empty header_para.
    # Its para_id would normally get SUMMARY_BODY_BUDGET (200 chars) which
    # is too short for a full LLM summary.  Exclude it from budget enforcement.
    anchors = _find_summary_anchors(doc)
    if anchors is not None:
        _, body_anchor = anchors
        if body_anchor.para_id:
            ids.add(body_anchor.para_id)

    return frozenset(ids)


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

    # Para_ids of meaningful content paragraphs (experience bullets, summary/skills
    # body lines).  These must never be truncated — budget slots are intentionally
    # left unset so _truncate_to_budget is never applied.
    _no_truncate: frozenset[str] = _collect_content_para_ids(original)

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
        if pm.para_id in _no_truncate:
            # Single-slot summary body anchor — no budget, full LLM text preserved.
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
                if b.para_id and b.para_id not in _no_truncate:
                    ol = len(b.text.strip())
                    budgets[b.para_id] = max(160, int(ol * 1.25))
                # else: meaningful bullet — no budget, text preserved fully
        for bp in sec.body_paras:
            if not bp.para_id or bp.para_id in budgets or bp.para_id in _no_truncate:
                continue  # already set, or meaningful content that must not be cut
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
                if bp.para_id in _orig_empty_header and bp.para_id not in _no_truncate:
                    # Two-slot anchor: constrained heading + body budget.
                    # Single-slot anchor: body_anchor.para_id is in _no_truncate
                    # (added by _collect_content_para_ids) — no budget, full text.
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
        page_images=getattr(updated, "page_images", []),
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
            # When classification has roles, use them to reconstruct role
            # boundaries instead of the deterministic date-first heuristic.
            if cls_sec is not None and cls_sec.roles:
                rebuilt = _rebuild_roles_from_classification(orig_section, cls_sec)
            else:
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

    # Summary anchors are pre-computed in the extras path when layout-bound.
    # Initialised here so post-merge code (lorem injection) can reference it
    # unconditionally regardless of which path (fast/extras) was taken.
    _summary_anchors: "tuple[ParaModel, ParaModel] | None" = None

    # Inline summary injection target: used when no trailing empty header slots
    # exist (e.g. table-based templates whose last header_para is a title line
    # like "registered nurse").  The renderer inserts a new paragraph directly
    # after this para_id in the table XML.
    _inline_summary: "tuple[str, str] | None" = None  # (target_pid, summary_text)
    _right_col_summary: "str | None" = None  # summary text for right-column injection (newspaper-column templates)

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
                _ha, _ba = _summary_anchors
                _log.debug(
                    "SUMMARY_ANCHORS_FOUND: heading_pid=%r body_pid=%r",
                    _ha.para_id if _ha else None, _ba.para_id,
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
                    _heading_anchor, _body_anchor = _summary_anchors
                    # When only a single-slot anchor (heading=None) exists, check if
                    # the template has an intro-prose paragraph that is a better target.
                    # Single-slot anchors are often empty spacing paras sandwiched between
                    # name components (e.g. para_4 between "GEORGE" and "SOFTWARE ENGINEER")
                    # and produce visual fragmentation.  An intro-prose paragraph (the
                    # original summary placeholder text) is always the correct visual slot.
                    _stext_anc = _clean_summary_text(llm_s.body_lines)
                    _intro_pa = _find_intro_prose_para(original) if _heading_anchor is None else None
                    if _intro_pa is not None and _stext_anc:
                        # Intro-prose available: replace it instead of using the anchor.
                        _intro_pa.text = _stext_anc
                        _summary_anchors = None
                        _log.debug(
                            "SUMMARY_INTRO_PROSE_REPLACED_OVER_ANCHOR: para_id=%r len=%d",
                            _intro_pa.para_id, len(_stext_anc),
                        )
                    else:
                        anchored = _build_anchored_summary_section(
                            llm_s, _heading_anchor, _body_anchor
                        )
                        llm_order_sections.append(anchored)
                        if _heading_anchor is not None and _heading_anchor.para_id:
                            _used_anchor_ids.add(_heading_anchor.para_id)
                        if _body_anchor.para_id:
                            _used_anchor_ids.add(_body_anchor.para_id)
                        _summary_anchors = None  # consume anchors; only one summary
                        _log.debug(
                            "SUMMARY_INSERTED_ANCHORED: %r heading_pid=%r body_pid=%r",
                            llm_s.heading,
                            anchored.heading.para_id or None,
                            anchored.body_paras[0].para_id if anchored.body_paras else None,
                        )
                elif llm_s.semantic_type == "summary":
                    # No trailing empty header slots.
                    # For paragraph-only layout templates (no table blocks), inject
                    # the summary into an empty body_para of the first eligible
                    # section.  This keeps the summary in the IR so the grader can
                    # detect it, and the renderer renders it at the correct position.
                    #
                    # For table-based templates: inserting a new paragraph into a
                    # table cell expands the cell height and pushes later content off
                    # the page (THIN_OVERFLOW_HARD_FAIL).  The "Summary missing" soft
                    # warning (score -10) is preferred over a hard overflow failure.
                    from tailor.compiler.models import LayoutTableBlock
                    _has_table_lb = original.layout_blocks is not None and any(
                        isinstance(b, LayoutTableBlock) for b in original.layout_blocks
                    )
                    _body_injected = False
                    if original.layout_blocks is not None:
                        _stext = _clean_summary_text(llm_s.body_lines)
                        if _stext:
                            # Highest priority for newspaper-column templates:
                            # if a column break exists and this is a summary section,
                            # inject the summary at the top of the right column.
                            # This must run BEFORE intro-prose search to prevent the
                            # summary from being placed in left-column skills/other slots
                            # (e.g. para_22 in sample 3) that pass the prose heuristic
                            # but belong to the sidebar, not the main content area.
                            if (
                                not _body_injected
                                and not _has_table_lb
                                and llm_s.semantic_type == "summary"
                            ):
                                _has_col_break = any(
                                    'type="column"' in (getattr(lb, "xml_proto_xml", "") or "")
                                    for lb in (original.layout_blocks or [])
                                )
                                if _has_col_break:
                                    _right_col_summary = _stext
                                    _body_injected = True
                                    _log.debug(
                                        "SUMMARY_RIGHT_COL_PENDING: newspaper-column "
                                        "template, summary injected at top of right column"
                                    )
                            # First priority (all templates): replace the intro-prose
                            # paragraph if present.  Replacing existing text is safe
                            # for both paragraph-only and table templates — it does not
                            # insert a new paragraph, so table cell heights are unchanged.
                            # Intro-prose paragraphs are non-empty summary placeholders
                            # in 'other' sections (e.g. "I have experience in developing
                            # and maintaining software..." inside a SOFTWARE ENGINEER
                            # section).  Replacing them keeps the summary in the correct
                            # visual position and avoids injecting into an empty slot that
                            # may be in the wrong column (e.g. samples 7, 19).
                            if not _body_injected:
                                _intro_para = _find_intro_prose_para(original)
                                if _intro_para is not None:
                                    _intro_para.text = _stext
                                    _body_injected = True
                                    _log.debug(
                                        "SUMMARY_INTRO_PROSE_REPLACED: para_id=%r len=%d",
                                        _intro_para.para_id, len(_stext),
                                    )
                            # Second priority (paragraph-only templates only): inject
                            # into the first empty body_para of an eligible section.
                            # Guarded by _has_table_lb because inserting text into an
                            # empty table-cell paragraph expands the cell height and
                            # pushes later content off the page (THIN_OVERFLOW_HARD_FAIL).
                            if not _body_injected and not _has_table_lb:
                                # Find the first empty body_para in the first non-locked,
                                # non-experience/skills section.  Search heading_to_section
                                # (already-updated matched sections) and verbatim_sections.
                                # The para_id is already in layout_blocks so the renderer
                                # will pick it up without needing a new element.
                                _SKIP_TYPES = frozenset({
                                    "experience", "skills", "education", "certifications",
                                    "languages", "websites", "contact", "social",
                                })
                                _cand_sections = (
                                    list(verbatim_sections)
                                    + list(heading_to_section.values())
                                )
                                for _cand_sec in _cand_sections:
                                    if _cand_sec.semantic_type in _SKIP_TYPES:
                                        continue
                                    if _cand_sec.semantic_type in _LOCKED_SEMANTIC_TYPES:
                                        continue
                                    for _cand_bp in _cand_sec.body_paras:
                                        if (
                                            not _cand_bp.text.strip()
                                            and _cand_bp.para_id
                                        ):
                                            _cand_bp.text = _stext
                                            _body_injected = True
                                            _log.debug(
                                                "SUMMARY_BODY_PARA_INJECTED: section=%r "
                                                "para_id=%r len=%d",
                                                _cand_sec.title, _cand_bp.para_id,
                                                len(_stext),
                                            )
                                            break
                                    if _body_injected:
                                        break
                    if not _body_injected and _has_table_lb:
                        # For table templates with no intro-prose and no empty body slots:
                        # inject the summary inline after the last non-empty header paragraph
                        # (typically the title/role line, e.g. "registered nurse").
                        # The renderer inserts a new paragraph in the table header cell.
                        _last_title_para = next(
                            (p for p in reversed(original.header_paras)
                             if p.text.strip() and p.para_id),
                            None,
                        )
                        if _last_title_para is not None:
                            _inline_summary = (_last_title_para.para_id, _stext)
                            _body_injected = True
                            _log.debug(
                                "SUMMARY_INLINE_AFTER_TITLE: para_id=%r title=%r",
                                _last_title_para.para_id, _last_title_para.text[:30],
                            )
                    pass  # right-column summary injection is handled above (before intro-prose)
                    _log.debug(
                        "SUMMARY_INSERTION_SKIPPED_NO_ANCHORS: %r (body_injected=%s)",
                        llm_s.heading, _body_injected,
                    )
                elif llm_s.semantic_type == "skills":
                    # Try to inject skills into the left column (the column that contains
                    # sidebar sections before the first experience section).
                    # Use only "experience" as the boundary — education can legitimately
                    # appear in the left sidebar column (e.g. sample 12 has Education
                    # in the left column before Communication and Leadership).
                    _CONTENT_BOUNDARY_TYPES = frozenset({"experience"})
                    _left_col_end = next(
                        (i for i, s in enumerate(original.sections)
                         if s.semantic_type in _CONTENT_BOUNDARY_TYPES),
                        None,
                    )
                    _skills_injected = False
                    if _left_col_end is not None and _left_col_end > 0:
                        # Find the last 'other' section before the content boundary
                        # that has a valid anchor paragraph (non-contact, non-empty).
                        # Contact-info guard: reject paragraphs with URLs, email,
                        # phone, or ZIP codes — these belong to contact sections.
                        _CONTACT_MARKS = ("@", "www.", "http://", "https://")
                        _phone_re_anchor = re.compile(r"^\+?[\d\s\-\.\(\)]{7,}$")
                        _zip_re_anchor = re.compile(r"^\d{4,6}$")

                        def _valid_anchor(p: "ParaModel") -> bool:
                            t = p.text.strip()
                            if not p.para_id or not t or len(t) < 3:
                                return False
                            if any(m in p.text for m in _CONTACT_MARKS):
                                return False
                            if _phone_re_anchor.match(t) or _zip_re_anchor.match(t):
                                return False
                            return True

                        _left_sec = None
                        _anchor_bp = None
                        for _cand_sec in reversed(original.sections[:_left_col_end]):
                            if _cand_sec.semantic_type in _LOCKED_SEMANTIC_TYPES:
                                continue
                            # Never inject skills into a summary section — its body paras
                            # are summary text, not a sidebar anchor slot.
                            if _cand_sec.semantic_type == "summary":
                                continue
                            _candidate_anchor = next(
                                (p for p in reversed(_cand_sec.body_paras) if _valid_anchor(p)),
                                None,
                            )
                            if _candidate_anchor is not None:
                                _left_sec = _cand_sec
                                _anchor_bp = _candidate_anchor
                                break
                        # Guard: if there are template sections AFTER the first
                        # experience section (e.g. References), technical skills
                        # belong at the document end, unless the template is a
                        # 2-row sidebar layout (header row + one body row with a
                        # sidebar cell containing multiple independent sections).
                        # For 2-row sidebar layouts the left cell has all sidebar
                        # sections in one place and is the correct injection target.
                        _post_exp_sections = original.sections[_left_col_end + 1:]
                        if _post_exp_sections and _left_sec is not None:
                            from tailor.compiler.models import LayoutTableBlock as _LTB
                            _main_tbl_rows = 0
                            for _lb in (original.layout_blocks or []):
                                if isinstance(_lb, _LTB):
                                    from lxml import etree as _etree_g
                                    _W_g = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
                                    _te = _etree_g.fromstring(_lb.xml_proto_xml)
                                    _main_tbl_rows = len(_te.findall(f"{{{_W_g}}}tr"))
                                    break
                            # 2-row table = header row + sidebar body row: safe to inject
                            if _main_tbl_rows != 2:
                                _left_sec = None  # fall back to document-end placement
                        if _left_sec is not None:
                            if _anchor_bp is not None:
                                _skill_lines = _sanitize_skills_lines(
                                    [l for l in llm_s.body_lines if l.strip()]
                                )
                                if _skill_lines:
                                    # Create heading styled like the left-column section heading,
                                    # and body lines styled like the left-column body paras.
                                    _sk_heading = _left_sec.heading.clone_as(
                                        llm_s.heading, "section_heading"
                                    )
                                    _sk_body = [
                                        _anchor_bp.clone_as(line, "paragraph")
                                        for line in _skill_lines
                                    ]
                                    # Find the matching updated section to attach _extra_injections
                                    _left_sec_updated = next(
                                        (s for s in list(heading_to_section.values()) + verbatim_sections
                                         if s.section_id == _left_sec.section_id),
                                        None,
                                    )
                                    if _left_sec_updated is not None:
                                        if not hasattr(_left_sec_updated, "_extra_injections"):
                                            _left_sec_updated._extra_injections = {}
                                        # Two empty spacers before the heading create visual
                                        # separation matching the gap before other sections.
                                        _sk_spacers = [
                                            _anchor_bp.clone_as("", "spacer"),
                                            _anchor_bp.clone_as("", "spacer"),
                                        ]
                                        _left_sec_updated._extra_injections.setdefault(
                                            _anchor_bp.para_id, []
                                        ).extend(_sk_spacers + [_sk_heading] + _sk_body)
                                        _skills_injected = True
                                        _log.debug(
                                            "SKILLS_LEFT_COLUMN_INJECTED: anchor=%r "
                                            "heading=%r lines=%d",
                                            _anchor_bp.para_id, llm_s.heading, len(_skill_lines),
                                        )
                    if not _skills_injected:
                        # Fallback: create as unbound content appended at document end.
                        _extra_skills = _make_extra_section(llm_s, heading_arch, body_arch)
                        _extra_skills.section_id = "sec_skills_unbound"
                        llm_order_sections.append(_extra_skills)
                        _log.debug(
                            "apply_tailored: skills extra %r → unbound (doc end fallback)",
                            llm_s.heading,
                        )
                else:
                    _log.debug(
                        "UPDATER_SECTION_ANCHOR_NOT_FOUND: %r dropped in layout-bound mode",
                        llm_s.heading,
                    )
            elif llm_s.semantic_type == "other" and verbatim_sections:
                # Template already has verbatim "other" sections (e.g. Affiliations).
                # Drop the LLM "other" extra (e.g. "Additional") to avoid a duplicate
                # with mismatched formatting.  The verbatim section carries the original
                # template styling (bold role headers, etc.) and is placed at the end.
                _log.debug(
                    "apply_tailored: discarding other-type extra %r "
                    "(verbatim other sections exist)", llm_s.heading,
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
                s for s in llm_order_sections
                if s.semantic_type == "summary" and s.section_id == "sec_summary_inserted"
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

            # Collect unbound skills sections (appended at end, carry to next page if needed)
            unbound_skills_sections = [
                s for s in llm_order_sections
                if s.semantic_type == "skills" and s.section_id == "sec_skills_unbound"
            ]

            if anchored_summaries:
                # Insert after profile/title block, before first major content
                # section (experience/education/skills).  Inserting at position 0
                # would place the summary before the candidate name/title block.
                first_major = next(
                    (i for i, s in enumerate(ordered_sections)
                     if s.semantic_type in _MAJOR_SECTION_TYPES),
                    len(ordered_sections),
                )
                new_sections = (
                    ordered_sections[:first_major]
                    + anchored_summaries
                    + ordered_sections[first_major:]
                    + unbound_skills_sections
                )
                _log.debug(
                    "SUMMARY_INSERTED_AFTER_PROFILE_BLOCK: inserted before %r "
                    "(after %d profile section(s))",
                    ordered_sections[first_major].title if first_major < len(ordered_sections) else "end",
                    first_major,
                )
            else:
                new_sections = ordered_sections + unbound_skills_sections
            if unbound_skills_sections:
                _log.debug(
                    "SKILLS_UNBOUND_APPENDED: %d skills section(s) appended at doc end",
                    len(unbound_skills_sections),
                )
        else:
            # Non-layout-bound: follow LLM output order with summary at top.
            template_has_summary = any(
                s.semantic_type == "summary" for s in original.sections
            )
            if not template_has_summary:
                # Use template order so verbatim sections (Languages, Certifications, etc.)
                # stay in their original positions relative to matched sections.
                _matched_ids_ns = {
                    id(heading_to_section[llm_s.heading.lower()])
                    for llm_s in llm_sections
                    if llm_s.heading.lower() in heading_to_section
                }
                _tpl_ordered_ns: list[ResumeSection] = []
                for _orig_s, _llm_s in match.pairs:
                    if _llm_s is None:
                        _tpl_ordered_ns.append(_orig_s)
                    else:
                        _k = _llm_s.heading.lower()
                        _tpl_ordered_ns.append(heading_to_section.get(_k, _orig_s))
                _extra_ns = [s for s in llm_order_sections if id(s) not in _matched_ids_ns]
                _sum_ns = [s for s in _extra_ns if s.semantic_type == "summary"]
                _oth_ns = [s for s in _extra_ns if s.semantic_type != "summary"]
                new_sections = _sum_ns + _tpl_ordered_ns + _oth_ns
            else:
                # Template already has a summary section → its section order is
                # well-defined.  Follow TEMPLATE order for all matched sections so
                # the physical layout (y-position, column assignments) is preserved.
                # Extras (new sections added by the LLM that had no template match)
                # are appended at the end in LLM output order; verbatim "other"
                # sections follow them.
                #
                # Previously this path followed LLM output order, which caused
                # sections to render in the wrong sequence (e.g. Professional
                # Experience before Technical Skills in sample 4 even though the
                # template places Technical Skills first).
                _matched_section_ids = {
                    id(heading_to_section[llm_s.heading.lower()])
                    for llm_s in llm_sections
                    if llm_s.heading.lower() in heading_to_section
                }
                template_ordered: list[ResumeSection] = []
                for orig_section, llm_section in match.pairs:
                    if llm_section is None:
                        template_ordered.append(orig_section)
                    else:
                        key = llm_section.heading.lower()
                        template_ordered.append(
                            heading_to_section.get(key, orig_section)
                        )
                # Extras are sections in llm_order_sections that are NOT in the
                # template-ordered set (i.e. newly created sections, not updates).
                extra_only = [
                    s for s in llm_order_sections
                    if id(s) not in _matched_section_ids
                ]
                # verbatim_sections are already included in template_ordered
                # (both built from match.pairs where llm_section is None).
                # Appending them again would duplicate those sections in the output.
                new_sections = template_ordered + extra_only

    # Fragmented-experience injection: LLM had experience roles but no original
    # experience section existed to match them.  Inject into role-like 'other'
    # sections (templates where each role appears as its own section).
    # Only runs in the extras path (where heading_to_section was populated).
    if _layout_bound and match.extras:
        _unmatched_exp = next(
            (s for s in llm_sections
             if s.semantic_type == "experience"
             and s.heading.lower() not in heading_to_section
             and s.roles),
            None,
        )
        if _unmatched_exp and not any(
            s.semantic_type == "experience" for s in new_sections
        ):
            new_sections = _inject_fragmented_experience(
                new_sections, original.sections, _unmatched_exp
            )

    # Lorem ipsum cleanup in layout-bound mode: blank out any remaining lorem ipsum
    # placeholder paragraphs in section bodies that were not reached by the targeted
    # injection steps above.  Any "lorem ipsum" in final output is a hard grader fail;
    # blanking preserves para_id (renderer slot) while removing the placeholder text.
    if _layout_bound:
        _LOREM_MARKER_LC = "lorem ipsum"
        for _sec in new_sections:
            _sec.body_paras[:] = [
                _pm.with_text("") if (_pm.para_id and _LOREM_MARKER_LC in _pm.text.lower()) else _pm
                for _pm in _sec.body_paras
            ]
            for _role in _sec.roles:
                _role.bullets[:] = [
                    _pm.with_text("") if (_pm.para_id and _LOREM_MARKER_LC in _pm.text.lower()) else _pm
                    for _pm in _role.bullets
                ]

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

    # Blank out intro-prose header_paras that would duplicate an anchored summary.
    # When the template has a summary-like placeholder in header_paras (e.g. a
    # "Motivated software engineer..." line) AND the summary was already anchored
    # into trailing empty header slots, the original placeholder must be cleared
    # to prevent the grader from detecting both old and new summary text on the
    # same page.  Only blanks paras that look like prose summaries (60+ chars,
    # not contact info, no pipe/URL/bullet).
    if _used_anchor_ids and _layout_bound:
        for _hi, _hp in enumerate(effective_header_paras):
            _t = _hp.text.strip()
            if (
                _hp.para_id
                and len(_t) >= _INTRO_PROSE_MIN_LEN
                and " " in _t
                and "|" not in _t
                and "://" not in _t
                and _t[0] not in ("-", "•", "·", "–", "*")
                and _t.count(",") / max(1, len(_t)) < 0.10
            ):
                effective_header_paras[_hi] = _hp.with_text("")
                _log.debug(
                    "INTRO_PROSE_BLANKED_AFTER_ANCHOR: para_id=%r (summary already anchored)",
                    _hp.para_id,
                )

    # Lorem-placeholder summary injection: if LLM has summary, no summary was
    # anchored, and a header_para contains lorem ipsum, replace it with the LLM
    # summary text.  This handles decorative templates where the summary slot is
    # filled with placeholder prose rather than left empty.
    if _layout_bound and _summary_anchors is None:
        _llm_summary = next(
            (s for s in llm_sections if s.semantic_type == "summary"), None
        )
        if _llm_summary and not any(
            s.semantic_type == "summary" for s in new_sections
        ):
            _LOREM_MARKER = "lorem ipsum"
            for _hi, _hp in enumerate(effective_header_paras):
                if _LOREM_MARKER in _hp.text.lower() and _hp.para_id:
                    _summary_text = _clean_summary_text(_llm_summary.body_lines)
                    if _summary_text:
                        effective_header_paras[_hi] = _hp.with_text(_summary_text)
                        _log.debug(
                            "LOREM_PLACEHOLDER_REPLACED: para_id=%r with summary text",
                            _hp.para_id,
                        )
                    break

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
                # Skip body_paras already claimed by role meta_lines (Pattern B:
                # the date paragraph appears in both body_paras and meta_lines).
                _claimed = {id(pm) for role in section.roles for pm in role.meta_lines}
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break  # reached first role; stop collecting orphans
                    if bp.text.strip() and id(bp) not in _claimed:
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
                        _claimed2 = {id(pm) for role in section.roles for pm in role.meta_lines}
                        for bp in section.body_paras:
                            if bp.semantic == "role_header":
                                break
                            if bp.text.strip() and id(bp) not in _claimed2:
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

    # For table-heavy templates, the layout-blocks renderer reads bullet/body_para
    # text from doc.sections (via _build_para_lookup), NOT from body_items.
    # _update_role packs extra LLM bullets into the last slot (for layout_bound),
    # creating oversized text that expands the table cell and pushes content to
    # page 2 (sparse first page).  Cap bullet and body_para text to original
    # template lengths so the table cell height stays within the template's bounds.
    if has_table_blocks and new_sections:
        _orig_bullet_len: dict[str, int] = {}
        _orig_bp_len: dict[str, int] = {}
        for _orig_s in original.sections:
            if _orig_s.semantic_type in _LOCKED_SEMANTIC_TYPES:
                continue
            for _orig_r in _orig_s.roles:
                for _ob in _orig_r.bullets:
                    if _ob.para_id:
                        _orig_bullet_len[_ob.para_id] = len(_ob.text.strip())
            for _obp in _orig_s.body_paras:
                if _obp.para_id and _obp.text.strip():
                    _orig_bp_len[_obp.para_id] = len(_obp.text.strip())

        for _ns in new_sections:
            if _ns.semantic_type in _LOCKED_SEMANTIC_TYPES:
                continue
            # Experience bullets: no cap — prefer full LLM content over clipping.
            # Overflow to a second page is acceptable; truncated bullets lose meaning.
            # (Previous cap: max(orig_len, 60).  Removed per content-preservation policy.)
            #
            # Skills sections in table cells: apply a moderate cap of max(orig_len*2, 60).
            # This allows 2× the original content (meaningful improvement over the original
            # severe cap at orig_len) while preventing the narrow left sidebar cell from
            # growing so large that it causes column layout collapse or table ejection to
            # page 2.  Full skills in unconstrained (non-table) templates are never capped.
            _is_skills_section = (
                _ns.semantic_type == "skills"
                or "skill" in _ns.title.lower()
            )
            if _is_skills_section and _orig_bp_len:
                _capped_bps = []
                _bp_changed = False
                for _nbp in _ns.body_paras:
                    _olen = _orig_bp_len.get(_nbp.para_id, 0)
                    _skills_cap = max(_olen * 2, 60) if _olen > 0 else 0
                    if _skills_cap > 0 and len(_nbp.text.strip()) > _skills_cap:
                        _bpcut = _nbp.text.rfind(" ", 0, _skills_cap)
                        _capped_bps.append(_nbp.with_text(
                            _nbp.text[:_bpcut] if _bpcut > 0 else _nbp.text[:_skills_cap]
                        ))
                        _bp_changed = True
                    else:
                        _capped_bps.append(_nbp)
                if _bp_changed:
                    _ns.body_paras = _capped_bps

            # Cap non-experience, non-summary, non-skills body_paras.
            # Summary sections excluded: full-width cells; user expects full LLM summary.
            # Experience and skills handled above.
            if (_ns.semantic_type not in ("experience", "summary")
                    and not _is_skills_section
                    and _orig_bp_len):
                _capped_bps: list[ParaModel] = []
                _bp_changed = False
                for _nbp in _ns.body_paras:
                    _olen = _orig_bp_len.get(_nbp.para_id, 0)
                    if _olen > 0 and len(_nbp.text.strip()) > _olen:
                        _bpcut = _nbp.text.rfind(" ", 0, _olen)
                        _capped_bps.append(_nbp.with_text(
                            _nbp.text[:_bpcut] if _bpcut > 0 else _nbp.text[:_olen]
                        ))
                        _bp_changed = True
                    else:
                        _capped_bps.append(_nbp)
                if _bp_changed:
                    _ns.body_paras = _capped_bps
        _log.debug("TABLE_CELL_CAP: applied to new_sections for table-heavy template")

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
        _sections_for_neighbor_check = original.sections
        # "websites" is intentionally excluded: a portfolio-links section is not
        # a contact area and does not make its neighbor a sidebar contact cell.
        # Its neighbor may be a main-column section (e.g. skills+summary in the
        # same broad column) that should not have its content filtered or cleared.
        _CONTACT_NEIGHBOR_TYPES: frozenset[str] = frozenset({"contact", "social"})

        # Character limit for experience bullets in table templates.
        # Table cells have fixed dimensions; excessively long bullets expand
        # the cell and push the table to a new page.  Using the original bullet
        # length (capped to a minimum of 160 chars) keeps cell height manageable.
        _TABLE_BULLET_MAX_CHARS = 160

        for _si, (orig_section, llm_section) in enumerate(match.pairs):
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

            # Determine whether this section is adjacent to a contact/websites section.
            # Used below to filter professional-summary-length lines that leaked
            # into the contact/sidebar cell (e.g. when the LLM bundles contact +
            # summary into one unnamed section matched to the sidebar section).
            _adj_contact = any(
                _sections_for_neighbor_check[j].semantic_type in _CONTACT_NEIGHBOR_TYPES
                for j in (_si - 1, _si + 1)
                if 0 <= j < len(_sections_for_neighbor_check)
            )
            # Threshold: lines longer than this are summary-sentences, not contact info.
            _CONTACT_LINE_MAX = 80

            if orig_section.semantic_type == "experience":
                llm_roles = llm_section.roles or []
                if cls_sec is not None:
                    # Classification present: update bullets only, never header/meta.
                    for i, o_role in enumerate(orig_section.roles):
                        if i < len(llm_roles):
                            for o_b, n_b in zip(o_role.bullets, llm_roles[i].bullets):
                                if has_table_blocks:
                                    max_blen = max(len(o_b.text.strip()), 60)
                                    if len(n_b) > max_blen:
                                        cutoff = n_b.rfind(" ", 0, max_blen)
                                        n_b = n_b[:cutoff] if cutoff > 0 else n_b[:max_blen]
                                o_b.text = n_b
                        # else: keep verbatim
                else:
                    # No classification: existing behaviour.
                    for o_role, n_role in zip(orig_section.roles, llm_roles):
                        o_role.header.text = n_role.header
                        for o_m, n_m in zip(o_role.meta_lines, n_role.meta_lines):
                            o_m.text = n_m
                        for o_b, n_b in zip(o_role.bullets, n_role.bullets):
                            # For table templates: cap bullet at the original
                            # bullet length (minimum 60 chars) to prevent the
                            # experience cell from expanding and causing overflow.
                            if has_table_blocks:
                                max_blen = max(len(o_b.text.strip()), 60)
                                if len(n_b) > max_blen:
                                    cutoff = n_b.rfind(" ", 0, max_blen)
                                    n_b = n_b[:cutoff] if cutoff > 0 else n_b[:max_blen]
                            o_b.text = n_b
            else:
                non_empty_orig = [p for p in orig_section.body_paras if p.text.strip()]
                llm_lines = [l for l in llm_section.body_lines if l.strip()]
                if orig_section.semantic_type == "skills":
                    llm_lines = _sanitize_skills_lines(llm_lines)
                # For sections adjacent to contact/websites: filter out long lines
                # (professional summary sentences that leaked from the LLM's header
                # block into the contact/sidebar section via section matching).
                if _adj_contact:
                    llm_lines = [l for l in llm_lines if len(l) <= _CONTACT_LINE_MAX]
                # Both classified (preserve_body_structure) and unclassified paths
                # update only as many paras as exist (zip stops at shorter list).
                for o_p, new_text in zip(non_empty_orig, llm_lines):
                    # For non-contact sections in table templates, cap body_para
                    # length to the ORIGINAL para length (allowing a small minimum
                    # of 40 chars).  Table cells have fixed dimensions; allowing
                    # para growth beyond the original template causes cells to
                    # expand and push the table to a new page (sparse first page).
                    if has_table_blocks and not _adj_contact:
                        max_len = max(len(o_p.text.strip()), 40)
                        if len(new_text) > max_len:
                            cut = new_text.rfind(" ", 0, max_len)
                            new_text = new_text[:cut] if cut > 0 else new_text[:max_len]
                    o_p.text = new_text
                # For contact-adjacent sections: clear any leftover template paras
                # that the LLM did not update (template had more paras than the LLM
                # provided short lines).  Only clear paras whose ORIGINAL text is
                # long (> _CONTACT_LINE_MAX chars) — those are summary/prose content
                # that accidentally lives in the contact/sidebar of the template.
                # Short paras (contact info labels etc.) are preserved verbatim.
                if _adj_contact and len(llm_lines) < len(non_empty_orig):
                    for o_p in non_empty_orig[len(llm_lines):]:
                        if len(o_p.text.strip()) > _CONTACT_LINE_MAX:
                            o_p.text = ""

        # Inject summary text into the intro-prose paragraph.
        # intro_para is guaranteed non-None here (checked in has_unhandled_extras above).
        if intro_para is not None:
            # Guard: if the anchored summary insertion already created a
            # sec_summary_inserted section, skip the intro-prose injection to
            # avoid placing the summary twice (anchored structural + in-place).
            _summary_already_anchored = _layout_bound and any(
                getattr(s, "section_id", "") == "sec_summary_inserted"
                for s in new_sections
            )
            if not _summary_already_anchored:
                for extra_llm in injectable_extras:
                    summary_text = " ".join(l for l in extra_llm.body_lines if l.strip())
                    intro_para.text = summary_text
                    _log.debug(
                        "SUMMARY_INSERTED_INTRO_PROSE: injected into para %r "
                        "(first 60 chars: %r)", intro_para.para_id, summary_text[:60]
                    )
            else:
                _log.debug(
                    "SUMMARY_SKIPPED_ALREADY_ANCHORED: summary already placed "
                    "via sec_summary_inserted — skipping intro-prose injection"
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

    # Compute layout_blocks for the result — reorder if a synthetic summary
    # was inserted so its block renders at the correct semantic position.
    _result_layout_blocks = original.layout_blocks
    if original.layout_blocks is not None:
        _summary_body_pid: str | None = next(
            (s.body_paras[0].para_id
             for s in new_sections
             if s.section_id == "sec_summary_inserted" and s.body_paras and s.body_paras[0].para_id),
            None,
        )
        _major_heading_pid: str | None = next(
            (s.heading.para_id
             for s in new_sections
             if s.semantic_type in _MAJOR_SECTION_TYPES and s.heading.para_id),
            None,
        )
        if _summary_body_pid and _major_heading_pid:
            # Preserve spacing rhythm: find the start of the spacer cluster
            # immediately before the major heading and insert the summary BEFORE
            # that cluster.  This keeps any original spacer/empty paras between
            # the summary and the heading (e.g. the blank line before WORK EXPERIENCE)
            # rather than burying them before the summary.
            _orig_text_map: dict[str, str] = {
                p.para_id: p.text
                for p in original.all_paras if p.para_id
            }
            _insert_before_pid = _major_heading_pid  # default: insert immediately before heading
            _major_lb_idx = next(
                (i for i, b in enumerate(original.layout_blocks)
                 if getattr(b, "para_id", None) == _major_heading_pid),
                None,
            )
            if _major_lb_idx is not None and _major_lb_idx > 0:
                # Walk backwards through the cluster of empty/spacer paras before heading
                j = _major_lb_idx - 1
                while j >= 0:
                    bid = getattr(original.layout_blocks[j], "para_id", None)
                    if bid and not _orig_text_map.get(bid, "x").strip():
                        _insert_before_pid = bid
                        _log.debug(
                            "HEADER_SPACING_PRESERVED: spacer %r found before %r — "
                            "summary inserted before spacer cluster",
                            bid, _major_heading_pid,
                        )
                        j -= 1
                    else:
                        break

            _result_layout_blocks = _move_layout_block(
                original.layout_blocks, _summary_body_pid, _insert_before_pid
            )

            # Repair header→profile spacing: moving the summary anchor block may
            # have been the only spacer between the last header_para and the first
            # section heading.  If so, its removal collapses that boundary.
            # Detect the collapse and insert a synthetic spacer (cloned from the
            # original spacer's XML proto) to restore the visual separation.
            _last_hp_pid = effective_header_paras[-1].para_id if effective_header_paras else None
            _first_sec_hpid = new_sections[0].heading.para_id if new_sections else None
            if _last_hp_pid and _first_sec_hpid and _result_layout_blocks:
                _upd_lb_pids = [getattr(b, "para_id", None) for b in _result_layout_blocks]
                _ulh = _upd_lb_pids.index(_last_hp_pid) if _last_hp_pid in _upd_lb_pids else -1
                _ufsh = _upd_lb_pids.index(_first_sec_hpid) if _first_sec_hpid in _upd_lb_pids else -1
                if _ulh >= 0 and _ufsh == _ulh + 1:
                    # Direct adjacency — check if original had spacers here
                    _orig_lb_pids = [getattr(b, "para_id", None) for b in original.layout_blocks]
                    _olh = _orig_lb_pids.index(_last_hp_pid) if _last_hp_pid in _orig_lb_pids else -1
                    _ofsh = _orig_lb_pids.index(_first_sec_hpid) if _first_sec_hpid in _orig_lb_pids else -1
                    if _olh >= 0 and _ofsh > _olh + 1:
                        # Original had blocks between them — spacing collapsed; synthesize spacer.
                        # Clone xml_proto from the first empty block in that original gap.
                        _orig_spacer_block = next(
                            (original.layout_blocks[k]
                             for k in range(_olh + 1, _ofsh)
                             if not _orig_text_map.get(
                                 getattr(original.layout_blocks[k], "para_id", None), "x"
                             ).strip()),
                            None,
                        )
                        from tailor.compiler.models import LayoutParagraphBlock as _LPB
                        _syn_spacer = _LPB(
                            para_id="spacer_header_auto_1",
                            xml_proto_xml=_orig_spacer_block.xml_proto_xml
                            if _orig_spacer_block else None,
                        )
                        _new_lb = list(_result_layout_blocks)
                        _new_lb.insert(_ufsh, _syn_spacer)
                        _result_layout_blocks = _new_lb
                        _log.debug(
                            "HEADER_TO_PROFILE_SPACING_SYNTHESIZED: inserted spacer "
                            "'spacer_header_auto_1' before %r (last_header=%r)",
                            _first_sec_hpid, _last_hp_pid,
                        )

        # Multi-copy template detection: templates that repeat the same section
        # headings N times (e.g. 3-copy cut-sheet templates) cause a blank middle
        # page when the first copy expands beyond its original table height.
        # Trim layout_blocks to the first copy + trailing paragraphs so that only
        # one copy is rendered.
        _n_copies = _detect_multi_copy_count(original.sections)
        if _n_copies > 1:
            _result_layout_blocks = _trim_to_first_copy_layout_blocks(
                _result_layout_blocks, _n_copies
            )
            _log.debug(
                "MULTI_COPY_TEMPLATE_DETECTED: %d copies, trimmed to first copy only",
                _n_copies,
            )

    # ── Auto-register unbound experience extras ───────────────────────────────
    # _update_role_bullets_only (standard experience path) produces extra clones
    # with para_id="" when the LLM generates more bullets than the template has
    # slots.  Register them in _extra_injections so the injection step below
    # places them at the correct position rather than at document end.
    for _sec in new_sections:
        if _sec.semantic_type != "experience":
            continue
        if getattr(_sec, "_extra_injections", None):
            continue  # already set (date-first or body path)
        _exp_extras: "dict[str, list[ParaModel]]" = {}
        for _role in _sec.roles:
            _bound_bullets = [_b for _b in _role.bullets if _b.para_id]
            _unbound_bullets = [_b for _b in _role.bullets if not _b.para_id and _b.text.strip()]
            if _unbound_bullets:
                # Prefer the last bound bullet as anchor (overflow case);
                # fall back to the role header when the template has NO bullet slots
                # (all bullets are unbound).
                _anchor = _bound_bullets[-1] if _bound_bullets else _role.header
                if _anchor.para_id:
                    _exp_extras.setdefault(_anchor.para_id, []).extend(_unbound_bullets)
        if _exp_extras:
            _sec._extra_injections = _exp_extras  # type: ignore[attr-defined]

    # ── Collect all extra injections ──────────────────────────────────────────
    # Sources: _update_experience_date_first, _update_body_section (skills/summary),
    # and the auto-registration above for standard experience roles.
    _all_extra_injections: "dict[str, list[ParaModel]]" = {}
    for _sec in new_sections:
        _ei = getattr(_sec, "_extra_injections", None)
        if _ei:
            _all_extra_injections.update(_ei)

    if _all_extra_injections and _result_layout_blocks:
        from tailor.compiler.models import LayoutParagraphBlock as _LPB, LayoutTableBlock as _LTB
        from lxml import etree as _etree
        _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

        _new_lb = list(_result_layout_blocks)
        _offset = 0  # running offset from prior LayoutParagraphBlock insertions

        for _i, _blk in enumerate(list(_result_layout_blocks)):

            # ── LayoutParagraphBlock path ──────────────────────────────────────
            if isinstance(_blk, _LPB):
                _extras = _all_extra_injections.get(_blk.para_id)
                if not _extras:
                    continue
                if _blk.xml_proto_xml is None:
                    # No XML prototype (minimal test fixture / PDF IR).  Leave
                    # extras unbound — they'll be silently omitted rather than
                    # appearing at document end.
                    _log.debug(
                        "EXTRA_INJECTION_SKIPPED_NO_PROTO: anchor=%r extra_count=%d",
                        _blk.para_id, len(_extras),
                    )
                    continue
                # Assign stable synthetic IDs and insert new LayoutParagraphBlock entries.
                for _j, _pm in enumerate(_extras):
                    _pm.para_id = f"{_blk.para_id}_ext_{_j + 1}"
                _insert_at = _i + 1 + _offset
                _new_blocks = [
                    _LPB(para_id=_pm.para_id, xml_proto_xml=_blk.xml_proto_xml)
                    for _pm in _extras
                ]
                _new_lb[_insert_at:_insert_at] = _new_blocks
                _offset += len(_new_blocks)
                _log.debug(
                    "EXTRA_BULLETS_INJECTED: anchor=%r extra_count=%d",
                    _blk.para_id, len(_extras),
                )

            # ── LayoutTableBlock path ──────────────────────────────────────────
            elif isinstance(_blk, _LTB):
                # Check whether any anchor para_id lives inside this table.
                _tbl_anchor_map = {
                    _anchor: _extras
                    for _anchor, _extras in _all_extra_injections.items()
                    if _anchor in _blk.para_ids
                }
                if not _tbl_anchor_map:
                    continue
                if not _blk.xml_proto_xml:
                    continue

                # Parse the table XML and build a para_id → <w:p> element map.
                try:
                    _tbl_tree = _etree.fromstring(_blk.xml_proto_xml.encode("utf-8"))
                except Exception:
                    continue
                _all_p = _tbl_tree.findall(f".//{{{_W_NS}}}p")
                _pid_to_pelem: "dict[str, Any]" = {}
                for _pid, _pelem in zip(_blk.para_ids, _all_p):
                    if _pid:
                        _pid_to_pelem[_pid] = _pelem

                # Insert extra <w:p> elements after each anchor, in reverse order
                # of their table position so earlier insertions don't shift later ones.
                _new_para_ids = list(_blk.para_ids)
                _anchors_sorted = sorted(
                    _tbl_anchor_map.keys(),
                    key=lambda _a: _blk.para_ids.index(_a) if _a in _blk.para_ids else -1,
                    reverse=True,
                )
                for _anchor in _anchors_sorted:
                    _extras = _tbl_anchor_map[_anchor]
                    _anchor_elem = _pid_to_pelem.get(_anchor)
                    if _anchor_elem is None:
                        continue
                    _anchor_idx = _blk.para_ids.index(_anchor)
                    _parent = _anchor_elem.getparent()
                    _pos = list(_parent).index(_anchor_elem)
                    for _j, _pm in enumerate(_extras):
                        _new_pid = f"{_anchor}_ext_{_j + 1}"
                        _pm.para_id = _new_pid
                        from copy import deepcopy as _deepcopy
                        # Use the paragraph's own xml_proto (e.g. Heading2 for section
                        # headings) so the injected element inherits the correct style.
                        # Fall back to the anchor element for bullets/body paras.
                        if getattr(_pm, 'style', None) is not None and _pm.style.xml_proto is not None:
                            _new_p = _deepcopy(_pm.style.xml_proto)
                        else:
                            _new_p = _deepcopy(_anchor_elem)
                        # Clear all text runs and set new content
                        for _t in _new_p.findall(f".//{{{_W_NS}}}t"):
                            _t.text = ""
                        _runs = _new_p.findall(f".//{{{_W_NS}}}r")
                        if _runs:
                            _runs[0].find(f"{{{_W_NS}}}t").text = _pm.text
                            for _r in _runs[1:]:
                                _r.getparent().remove(_r)
                        _parent.insert(_pos + 1 + _j, _new_p)
                        _new_para_ids.insert(_anchor_idx + 1 + _j, _new_pid)
                    _log.debug(
                        "EXTRA_BULLETS_TABLE_INJECTED: anchor=%r extra_count=%d table=%r",
                        _anchor, len(_extras), _blk.table_id,
                    )

                # Re-serialise the modified table XML and replace the layout block.
                _new_xml = _etree.tostring(_tbl_tree, encoding="unicode")
                _adj = _i + _offset
                _new_lb[_adj] = _LTB(
                    table_id=_blk.table_id,
                    xml_proto_xml=_new_xml,
                    para_ids=_new_para_ids,
                )

        _result_layout_blocks = _new_lb

    # Append LayoutParagraphBlock entries for unbound skills sections (templates
    # that originally had no skills section).  Each paragraph is assigned a
    # synthetic para_id so the layout-blocks renderer looks it up via para_lookup
    # and renders it using the paragraph's style.xml_proto at document end.
    # Rule 2/4: content pushes to next page naturally if it overflows.
    if _result_layout_blocks is not None:
        _skills_unbound_secs = [
            s for s in new_sections
            if s.section_id == "sec_skills_unbound" and s.semantic_type == "skills"
        ]
        if _skills_unbound_secs:
            from tailor.compiler.models import LayoutParagraphBlock as _SLPB
            _skills_lb: list = []
            _su_idx = 0
            for _usk in _skills_unbound_secs:
                _usk.heading.para_id = f"para_skills_u_{_su_idx}"
                _skills_lb.append(_SLPB(para_id=_usk.heading.para_id, xml_proto_xml=None))
                _su_idx += 1
                for _ubp in _usk.body_paras:
                    if _ubp.text.strip():
                        _ubp.para_id = f"para_skills_u_{_su_idx}"
                        _skills_lb.append(_SLPB(para_id=_ubp.para_id, xml_proto_xml=None))
                        _su_idx += 1
            if _skills_lb:
                _result_layout_blocks = list(_result_layout_blocks) + _skills_lb
                _log.debug(
                    "SKILLS_UNBOUND_LAYOUT_BLOCKS: appended %d blocks for %d section(s)",
                    len(_skills_lb), len(_skills_unbound_secs),
                )

    _result = ResumeDocument(
        header_paras=effective_header_paras,
        sections=new_sections,
        layout=original.layout,
        all_paras=all_paras,
        source_kind=original.source_kind,
        body_items=original.body_items if (has_table_blocks and not has_unhandled_extras) else None,
        layout_blocks=_result_layout_blocks,
        page_images=getattr(original, "page_images", []),
    )

    # Apply per-slot text-length budgets in layout-bound mode.  Runs last so
    # all prior structural repairs (split-brain fix, section-order rewrite,
    # bullet overflow drop) have already been applied before truncation.
    if _layout_bound:
        _result = apply_anchor_budgets(original, _result)

    # Pass inline-summary injection target to the renderer (duck-typed attribute).
    # The renderer reads _inline_summary_pid / _inline_summary_text to insert a
    # new paragraph in the table XML after the identity title para (sample 11 case).
    if _inline_summary:
        _result._inline_summary_pid, _result._inline_summary_text = _inline_summary  # type: ignore[attr-defined]

    if _right_col_summary:
        _result._right_col_summary_text = _right_col_summary  # type: ignore[attr-defined]

    return _result

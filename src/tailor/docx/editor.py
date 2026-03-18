"""Structural DOCX editor.

Applies LLM-generated tailored content to a DOCX template using block-level
editing: reusing existing paragraph nodes when possible, cloning formatting
archetypes when extra blocks are needed, and removing surplus blocks.

Public API
----------
apply_tailored_content(template_path, output_path, llm_text, debug_log=None)
    Main entry point. Calls the structural pipeline and saves the result.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

from docx import Document
from docx.text.paragraph import Paragraph as DocxParagraph

from tailor.docx import _W
from tailor.docx.structural_model import (
    DocumentModel, LlmRole, LlmSection, ParagraphModel, RoleBlock, SectionBlock,
)
from tailor.docx.parser import parse_docx, parse_llm_text

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Low-level paragraph editing helpers
# ---------------------------------------------------------------------------

def _strip_section_break(p_elem) -> None:
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is not None:
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is not None:
            pPr.remove(sectPr)


def _strip_pPr_rPr_color(p_elem) -> None:
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        return
    rPr = pPr.find(f"{{{_W}}}rPr")
    if rPr is None:
        return
    color = rPr.find(f"{{{_W}}}color")
    if color is not None:
        rPr.remove(color)
    if len(rPr) == 0:
        pPr.remove(rPr)


def _replace_text_preserving_format(p_elem, new_text: str, debug_path: str = "") -> None:
    """Replace all text in *p_elem* while keeping its paragraph and first-run formatting.

    Algorithm:
    1. Strip section break and stale pPr color (housekeeping).
    2. Collect all w:r and hyperlink-child-w:r elements.
    3. If none exist, fall back to setting text via paragraph.text.
    4. Clear text from all runs; set new_text on first run.
    """
    _strip_section_break(p_elem)
    _strip_pPr_rPr_color(p_elem)

    # Gather all run elements (direct and hyperlink children)
    all_runs: list = []
    for child in p_elem:
        tag = child.tag
        if tag == f"{{{_W}}}r":
            all_runs.append(child)
        elif tag == f"{{{_W}}}hyperlink":
            for r in child.findall(f"{{{_W}}}r"):
                all_runs.append(r)

    if not all_runs:
        # Paragraph has no runs — create a minimal text node via python-docx
        p = DocxParagraph(p_elem, p_elem.getparent())
        p.text = new_text
        return

    # Clear text from all runs
    for r in all_runs:
        for t in r.findall(f"{{{_W}}}t"):
            t.text = ""
        for t in r.findall(f"{{{_W}}}delText"):
            t.text = ""

    # Set new text on first run
    first = all_runs[0]
    t_elems = first.findall(f"{{{_W}}}t")
    if t_elems:
        t_elems[0].text = new_text
        # Preserve xml:space="preserve" for leading/trailing spaces
        if new_text and (new_text[0] == " " or new_text[-1] == " "):
            t_elems[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    else:
        from lxml import etree
        t = etree.SubElement(first, f"{{{_W}}}t")
        t.text = new_text


def _remove_para(p_elem) -> None:
    parent = p_elem.getparent()
    if parent is not None:
        parent.remove(p_elem)


def _clear_para(p_elem) -> None:
    """Clear text and remove list numbering, but keep the paragraph node."""
    _replace_text_preserving_format(p_elem, "")
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is not None:
        numPr = pPr.find(f"{{{_W}}}numPr")
        if numPr is not None:
            pPr.remove(numPr)


def _clone_para_after(ref_p_elem, text: str, style_source_elem=None) -> Any:
    """Clone *style_source_elem* (or *ref_p_elem*) XML, set text, insert after ref.

    Returns the new lxml paragraph element.
    """
    source = style_source_elem if style_source_elem is not None else ref_p_elem
    new_p = deepcopy(source)
    ref_p_elem.addnext(new_p)
    _replace_text_preserving_format(new_p, text, debug_path="clone")
    return new_p


# ---------------------------------------------------------------------------
# Archetype discovery
# ---------------------------------------------------------------------------

def _find_bullet_archetype(role: RoleBlock) -> ParagraphModel | None:
    """Return the best bullet paragraph to use as a cloning source."""
    if role.bullet_paragraphs:
        return role.bullet_paragraphs[0]
    return None


def _find_bullet_archetype_in_section(section: SectionBlock) -> ParagraphModel | None:
    for role in section.roles:
        arch = _find_bullet_archetype(role)
        if arch is not None:
            return arch
    return None


def _find_bullet_archetype_in_doc(model: DocumentModel) -> ParagraphModel | None:
    for section in model.sections:
        arch = _find_bullet_archetype_in_section(section)
        if arch is not None:
            return arch
    return None


# ---------------------------------------------------------------------------
# Section matching
# ---------------------------------------------------------------------------

def _match_sections(
    tmpl_sections: list[SectionBlock],
    llm_sections: list[LlmSection],
) -> list[tuple[SectionBlock, LlmSection]]:
    """Return ordered pairs (template_section, llm_section) for matched sections.

    Matching strategy:
    1. Exact heading text match (case-insensitive)
    2. Same semantic type (experience → experience, skills → skills, etc.)
       — uses first unmatched template section of that type
    3. Position-based fallback
    """
    used_tmpl: set[int] = set()
    used_llm: set[int] = set()
    pairs: list[tuple[SectionBlock, LlmSection]] = []

    # Pass 1: exact heading match
    for li, ls in enumerate(llm_sections):
        for ti, ts in enumerate(tmpl_sections):
            if ti in used_tmpl:
                continue
            if ts.raw_heading_text.lower() == ls.heading.lower():
                pairs.append((ts, ls))
                used_tmpl.add(ti)
                used_llm.add(li)
                break

    # Pass 2: semantic type match
    for li, ls in enumerate(llm_sections):
        if li in used_llm:
            continue
        for ti, ts in enumerate(tmpl_sections):
            if ti in used_tmpl:
                continue
            if ts.semantic_type == ls.semantic_type and ls.semantic_type != "other":
                pairs.append((ts, ls))
                used_tmpl.add(ti)
                used_llm.add(li)
                break

    # Pass 3: position-based for remaining unmatched (other sections)
    tmpl_remaining = [i for i in range(len(tmpl_sections)) if i not in used_tmpl]
    llm_remaining = [i for i in range(len(llm_sections)) if i not in used_llm]
    for ti, li in zip(tmpl_remaining, llm_remaining):
        pairs.append((tmpl_sections[ti], llm_sections[li]))

    return pairs


# ---------------------------------------------------------------------------
# Experience section editing
# ---------------------------------------------------------------------------

def _apply_role(
    tmpl_role: RoleBlock,
    llm_role: LlmRole,
    section: SectionBlock,
    model: DocumentModel,
    edit_log: list[dict],
) -> None:
    """Apply one LLM role onto one template role, in place."""

    # --- Update role header(s) ---
    if tmpl_role.header_paragraphs:
        _replace_text_preserving_format(
            tmpl_role.header_paragraphs[0].xml_ref, llm_role.header,
            debug_path="role_header",
        )
        edit_log.append({
            "action": "reuse", "target": "role_header",
            "original_style": tmpl_role.header_paragraphs[0].fmt.style_name,
            "text": llm_role.header,
        })
        # Remove extra header paragraphs (multi-line header → single line)
        for extra in tmpl_role.header_paragraphs[1:]:
            _remove_para(extra.xml_ref)
            edit_log.append({"action": "delete", "target": "extra_header_para"})

    # --- Update meta lines (date/location) ---
    for j, meta_text in enumerate(llm_role.meta_lines):
        if j < len(tmpl_role.meta_paragraphs):
            _replace_text_preserving_format(
                tmpl_role.meta_paragraphs[j].xml_ref, meta_text,
                debug_path="role_meta",
            )
            edit_log.append({"action": "reuse", "target": "role_meta", "text": meta_text})
        else:
            # Clone last meta para if available, else first header para
            anchor = tmpl_role.meta_paragraphs[-1] if tmpl_role.meta_paragraphs else tmpl_role.header_paragraphs[-1]
            _clone_para_after(anchor.xml_ref, meta_text)
            edit_log.append({"action": "clone", "target": "role_meta", "text": meta_text})

    # Remove surplus meta paragraphs
    for j in range(len(llm_role.meta_lines), len(tmpl_role.meta_paragraphs)):
        _remove_para(tmpl_role.meta_paragraphs[j].xml_ref)
        edit_log.append({"action": "delete", "target": "surplus_meta"})

    # --- Update bullets ---
    llm_bullets = llm_role.bullets
    tmpl_bullets = tmpl_role.bullet_paragraphs

    # Find bullet archetype for cloning
    arch = (
        _find_bullet_archetype(tmpl_role)
        or _find_bullet_archetype_in_section(section)
        or _find_bullet_archetype_in_doc(model)
    )
    if arch is None:
        arch_src = None
    else:
        arch_src = arch.xml_ref

    # Determine insertion anchor (last meta para or last header para)
    if tmpl_role.meta_paragraphs:
        insert_after = tmpl_role.meta_paragraphs[-1].xml_ref
    elif tmpl_role.header_paragraphs:
        insert_after = tmpl_role.header_paragraphs[-1].xml_ref
    else:
        insert_after = None

    last_bullet_elem = None

    for j, bullet_text in enumerate(llm_bullets):
        if j < len(tmpl_bullets):
            _replace_text_preserving_format(
                tmpl_bullets[j].xml_ref, bullet_text, debug_path="bullet",
            )
            last_bullet_elem = tmpl_bullets[j].xml_ref
            edit_log.append({"action": "reuse", "target": "bullet", "text": bullet_text})
        else:
            # Clone archetype bullet
            anchor = (
                last_bullet_elem
                if last_bullet_elem is not None
                else (tmpl_bullets[-1].xml_ref if tmpl_bullets else insert_after)
            )
            if anchor is not None:
                last_bullet_elem = _clone_para_after(anchor, bullet_text, style_source_elem=arch_src)
                edit_log.append({
                    "action": "clone",
                    "target": "bullet",
                    "text": bullet_text,
                    "fallback": arch is None,
                })
            else:
                log.warning("No anchor for bullet insertion: %s", bullet_text)
                edit_log.append({"action": "warning", "msg": "no_anchor_for_bullet", "text": bullet_text})

    # Remove surplus template bullets
    for j in range(len(llm_bullets), len(tmpl_bullets)):
        p = tmpl_bullets[j]
        if p.text.strip():
            _remove_para(p.xml_ref)
            edit_log.append({"action": "delete", "target": "surplus_bullet"})
        else:
            _clear_para(p.xml_ref)

    # Keep trailing blank separators (they maintain spacing between roles)


def _apply_experience_section(
    tmpl_section: SectionBlock,
    llm_section: LlmSection,
    model: DocumentModel,
    edit_log: list[dict],
) -> None:
    """Apply a full experience section: match roles by position."""
    tmpl_roles = tmpl_section.roles
    llm_roles = llm_section.roles

    # Update heading text
    if tmpl_section.heading is not None:
        _replace_text_preserving_format(
            tmpl_section.heading.xml_ref,
            llm_section.heading,
            debug_path="section_heading",
        )
        edit_log.append({
            "action": "reuse",
            "target": "section_heading",
            "text": llm_section.heading,
        })

    # Match roles by position
    for idx, tmpl_role in enumerate(tmpl_roles):
        if idx >= len(llm_roles):
            # Remove entire template role (no LLM content for it)
            all_role_paras = (
                tmpl_role.header_paragraphs
                + tmpl_role.meta_paragraphs
                + tmpl_role.bullet_paragraphs
                + tmpl_role.trailing_paragraphs
            )
            for pm in all_role_paras:
                _remove_para(pm.xml_ref)
            edit_log.append({"action": "delete", "target": "entire_role", "role_id": tmpl_role.role_id})
        else:
            _apply_role(tmpl_role, llm_roles[idx], tmpl_section, model, edit_log)

    # If LLM has MORE roles than template, clone the last template role
    if len(llm_roles) > len(tmpl_roles) and tmpl_roles:
        last_tmpl_role = tmpl_roles[-1]
        for idx in range(len(tmpl_roles), len(llm_roles)):
            llm_role = llm_roles[idx]
            # Clone entire last role block (header + meta + bullets)
            all_clone_paras = (
                last_tmpl_role.header_paragraphs
                + last_tmpl_role.meta_paragraphs
                + last_tmpl_role.bullet_paragraphs
            )
            if not all_clone_paras:
                continue

            # Find the last paragraph of the previous role as insertion anchor
            all_prev_paras = (
                last_tmpl_role.trailing_paragraphs
                or last_tmpl_role.bullet_paragraphs
                or last_tmpl_role.meta_paragraphs
                or last_tmpl_role.header_paragraphs
            )
            anchor = all_prev_paras[-1].xml_ref if all_prev_paras else None

            # Clone and insert each paragraph in order (last first, then re-insert in order)
            if anchor is not None:
                # Insert in reverse so addnext gives correct order
                clone_texts = (
                    [llm_role.header]
                    + llm_role.meta_lines
                    + llm_role.bullets
                )
                clone_sources = (
                    last_tmpl_role.header_paragraphs[:1]
                    + last_tmpl_role.meta_paragraphs[:max(len(llm_role.meta_lines), 1)]
                    + last_tmpl_role.bullet_paragraphs[:max(len(llm_role.bullets), 1)]
                )
                current_anchor = anchor
                for txt, src in zip(clone_texts, clone_sources):
                    new_p = _clone_para_after(current_anchor, txt, style_source_elem=src.xml_ref)
                    current_anchor = new_p
                edit_log.append({"action": "clone_role", "idx": idx, "header": llm_role.header})


# ---------------------------------------------------------------------------
# Non-experience section editing (blank-line group approach)
# ---------------------------------------------------------------------------

def _apply_body_section(
    tmpl_section: SectionBlock,
    llm_section: LlmSection,
    edit_log: list[dict],
) -> None:
    """Apply LLM body lines to a non-experience template section."""

    # Update heading
    if tmpl_section.heading is not None:
        _replace_text_preserving_format(
            tmpl_section.heading.xml_ref, llm_section.heading, debug_path="section_heading"
        )
        edit_log.append({"action": "reuse", "target": "section_heading", "text": llm_section.heading})

    llm_lines = [l for l in llm_section.body_lines if l.strip()]
    tmpl_paras = [pm for pm in tmpl_section.body_paragraphs]

    # Separate into groups by blank paragraphs in template
    tmpl_groups: list[list[ParagraphModel]] = [[]]
    sep_paras: list[ParagraphModel] = []
    for pm in tmpl_paras:
        if not pm.text.strip():
            tmpl_groups.append([])
            sep_paras.append(pm)
        else:
            tmpl_groups[-1].append(pm)

    # LLM lines are flat (no blank-line grouping for body sections)
    # Distribute evenly across template groups or fill first group
    llm_groups: list[list[str]] = [[]]
    for line in llm_lines:
        if not line.strip():
            llm_groups.append([])
        else:
            llm_groups[-1].append(line)

    global_last: Any = None

    for i in range(max(len(tmpl_groups), len(llm_groups))):
        tg = tmpl_groups[i] if i < len(tmpl_groups) else []
        lg = llm_groups[i] if i < len(llm_groups) else []

        group_last: Any = None

        for j, ltext in enumerate(lg):
            if j < len(tg):
                _replace_text_preserving_format(tg[j].xml_ref, ltext)
                group_last = tg[j].xml_ref
                edit_log.append({"action": "reuse", "target": "body_para", "text": ltext})
            else:
                anchor = group_last if group_last is not None else global_last
                if anchor is not None:
                    _tg_src = tg[-1].xml_ref if tg else None
                    style_src = _tg_src if _tg_src is not None else anchor
                    group_last = _clone_para_after(anchor, ltext, style_source_elem=style_src)
                    edit_log.append({"action": "clone", "target": "body_para", "text": ltext})
                else:
                    log.warning("No anchor for body para insertion: %s", ltext)

        # Remove surplus template paragraphs in this group
        for j in range(len(lg), len(tg)):
            _clear_para(tg[j].xml_ref)
            edit_log.append({"action": "clear", "target": "surplus_body_para"})

        if group_last is not None:
            global_last = group_last
        elif tg:
            global_last = tg[-1].xml_ref

        if i < len(sep_paras):
            global_last = sep_paras[i].xml_ref


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def apply_tailored_content(
    template_path: str,
    output_path: str,
    llm_text: str,
    debug_log: dict | None = None,
) -> None:
    """Apply LLM-tailored content to *template_path* and save to *output_path*.

    Uses the structural DOCX model: parses the template, parses the LLM text,
    matches sections semantically, and applies block-level edits preserving all
    paragraph and run formatting.

    Parameters
    ----------
    template_path:
        Path to the source DOCX template.
    output_path:
        Path where the modified DOCX will be saved.
    llm_text:
        Plain-text LLM output (resume or cover letter).
    debug_log:
        Optional dict to populate with debug information. Mutated in place.
    """
    if debug_log is None:
        debug_log = {}

    doc = Document(template_path)
    model = parse_docx(doc)
    llm_sections = parse_llm_text(llm_text)

    debug_log["document_structure"] = model.debug_info
    debug_log["llm_sections"] = [
        {"heading": s.heading, "type": s.semantic_type, "role_count": len(s.roles)}
        for s in llm_sections
    ]

    # If no experience section in template, fall back to full-document _apply_groups
    has_exp_section = any(s.semantic_type == "experience" for s in model.sections)
    if not has_exp_section or not llm_sections:
        debug_log["fallback"] = "no_experience_section_or_empty_llm"
        _fallback_apply(doc, llm_text)
        doc.save(output_path)
        return

    matched_pairs = _match_sections(model.sections, llm_sections)
    debug_log["section_matches"] = [
        {"template": ts.raw_heading_text, "llm": ls.heading, "type": ts.semantic_type}
        for ts, ls in matched_pairs
    ]

    edit_log: list[dict] = []

    for tmpl_section, llm_section in matched_pairs:
        if tmpl_section.semantic_type == "experience":
            _apply_experience_section(tmpl_section, llm_section, model, edit_log)
        else:
            _apply_body_section(tmpl_section, llm_section, edit_log)

    debug_log["edit_log"] = edit_log
    debug_log["warnings"] = [e for e in edit_log if e.get("action") == "warning"]

    doc.save(output_path)


def _fallback_apply(doc: Document, text: str) -> None:
    """Full-document _apply_groups fallback (when structural path can't run)."""
    # Import here to avoid circular dependency
    from tailor.docx.template_fill import _apply_groups
    _apply_groups(doc.paragraphs, text, doc)

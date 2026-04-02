"""Parse a DOCX file into a ResumeDocument IR.

Semantic classification uses style names, indentation, bold flags, and text
heuristics — no dependency on specific heading names or style IDs.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from docx import Document
from docx.oxml.ns import qn

from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
    TableBlock,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# ---------------------------------------------------------------------------
# Section semantic classification
# ---------------------------------------------------------------------------

_EXPERIENCE_NAMES: frozenset[str] = frozenset({
    "experience", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "about me", "career summary",
    "executive summary",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "skill", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees",
})

_ALL_HEADING_NAMES: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES
    | frozenset({
        "projects", "certifications", "certification", "publications",
        "awards", "honors", "languages", "references", "activities",
        "volunteer", "volunteering", "leadership", "interests",
        "additional information",
    })
)

# D: keyword set for noncanonical skills-like section headings.
# Applied as a word-level fallback after all exact-set checks fail.
# Covers headings like "Core Technologies", "Key Proficiencies",
# "OPTIONAL PERSONAL, PATENTS, AWARDS, TECHNOLOGIES, KEYWORDS", etc.
_SKILLS_LIKE_WORDS: frozenset[str] = frozenset({
    "skill", "technical", "technologies", "technology",
    "keywords", "competencies", "competency",
    "expertise", "proficiencies", "proficiency",
})

_HEADING_STYLE_RE = re.compile(r"^heading\s*\d", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _classify_section(heading_text: str) -> str:
    t = heading_text.strip().lower()
    if t in _EXPERIENCE_NAMES:
        return "experience"
    if t in _SUMMARY_NAMES:
        return "summary"
    if t in _SKILLS_NAMES:
        return "skills"
    if t in _EDUCATION_NAMES:
        return "education"
    # D: skills-like keyword detection for noncanonical headings.
    # Split on whitespace and common delimiters so compound headings like
    # "OPTIONAL PERSONAL, PATENTS, AWARDS, TECHNOLOGIES, KEYWORDS" are
    # matched by individual words ("technologies", "keywords").
    words = re.split(r"[\s/&,]+", t)
    if any(w in _SKILLS_LIKE_WORDS for w in words if w):
        return "skills"
    return "other"


# ---------------------------------------------------------------------------
# Low-level XML helpers
# ---------------------------------------------------------------------------

def _get_para_text(p_elem) -> str:
    parts: list[str] = []
    for elem in p_elem.iter():
        if elem.tag == f"{{{_W}}}t":
            parts.append(elem.text or "")
        elif elem.tag == f"{{{_W}}}br":
            parts.append("\n")
    return "".join(parts)


def _parse_para_style(p_elem, style_map: dict[str, str]) -> ParaStyle:
    pPr = p_elem.find(f"{{{_W}}}pPr")
    style_name = alignment = None
    indent_left = indent_right = hanging = None
    spacing_before = spacing_after = line_spacing = None
    keep_with_next = None
    numbering = None

    if pPr is not None:
        ps = pPr.find(f"{{{_W}}}pStyle")
        if ps is not None:
            sid = ps.get(f"{{{_W}}}val")
            style_name = style_map.get(sid, sid)

        jc = pPr.find(f"{{{_W}}}jc")
        if jc is not None:
            alignment = jc.get(f"{{{_W}}}val")

        ind = pPr.find(f"{{{_W}}}ind")
        if ind is not None:
            v = ind.get(f"{{{_W}}}left")
            if v:
                indent_left = int(v)
            v = ind.get(f"{{{_W}}}right")
            if v:
                indent_right = int(v)
            v = ind.get(f"{{{_W}}}hanging")
            if v:
                hanging = int(v)

        spc = pPr.find(f"{{{_W}}}spacing")
        if spc is not None:
            v = spc.get(f"{{{_W}}}before")
            if v:
                spacing_before = int(v)
            v = spc.get(f"{{{_W}}}after")
            if v:
                spacing_after = int(v)
            v = spc.get(f"{{{_W}}}line")
            if v:
                line_spacing = int(v)

        kn = pPr.find(f"{{{_W}}}keepNext")
        if kn is not None:
            v = kn.get(f"{{{_W}}}val", "true")
            keep_with_next = v.lower() not in ("0", "false")

        numPr = pPr.find(f"{{{_W}}}numPr")
        if numPr is not None:
            ilvl_el = numPr.find(f"{{{_W}}}ilvl")
            numId_el = numPr.find(f"{{{_W}}}numId")
            if ilvl_el is not None and numId_el is not None:
                numbering = {
                    "ilvl": int(ilvl_el.get(f"{{{_W}}}val", "0")),
                    "numId": int(numId_el.get(f"{{{_W}}}val", "0")),
                }

    # Run-level defaults: inspect first run
    bold = italic = font_name = color = None
    font_size_pt = None
    for r in p_elem.findall(f"{{{_W}}}r"):
        rPr = r.find(f"{{{_W}}}rPr")
        if rPr is not None:
            b = rPr.find(f"{{{_W}}}b")
            if b is not None:
                v = b.get(f"{{{_W}}}val", "true")
                bold = v.lower() not in ("0", "false")
            i = rPr.find(f"{{{_W}}}i")
            if i is not None:
                v = i.get(f"{{{_W}}}val", "true")
                italic = v.lower() not in ("0", "false")
            fonts = rPr.find(f"{{{_W}}}rFonts")
            if fonts is not None:
                font_name = (
                    fonts.get(f"{{{_W}}}ascii")
                    or fonts.get(f"{{{_W}}}cs")
                    or fonts.get(f"{{{_W}}}hAnsi")
                )
            sz = rPr.find(f"{{{_W}}}sz")
            if sz is not None:
                v = sz.get(f"{{{_W}}}val")
                if v:
                    font_size_pt = int(v) / 2.0
            col = rPr.find(f"{{{_W}}}color")
            if col is not None:
                color = col.get(f"{{{_W}}}val")
        break  # only read first run for defaults

    return ParaStyle(
        style_name=style_name,
        alignment=alignment,
        indent_left=indent_left,
        indent_right=indent_right,
        hanging=hanging,
        spacing_before=spacing_before,
        spacing_after=spacing_after,
        line_spacing=line_spacing,
        keep_with_next=keep_with_next,
        numbering=numbering,
        bold=bold,
        italic=italic,
        font_name=font_name,
        font_size_pt=font_size_pt,
        color=color,
        xml_proto=deepcopy(p_elem),
    )


def _build_style_map(doc) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        for style in doc.styles:
            if style.style_id:
                result[style.style_id] = style.name
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Semantic type inference
# ---------------------------------------------------------------------------

def _infer_semantic(pm: ParaModel) -> str:
    text = pm.text.strip()
    style_name = (pm.style.style_name or "").strip()

    if not text:
        return "empty"

    if _HEADING_STYLE_RE.match(style_name):
        return "section_heading"
    if style_name.lower() in {"title", "subtitle"}:
        return "section_heading"

    # Known section names: bold paragraph whose text exactly matches a recognized
    # section name is a section heading regardless of font size or spacing.
    # This handles templates that use small bold text (e.g. 10.5pt) for headings.
    if pm.style.bold and text.lower() in _ALL_HEADING_NAMES:
        return "section_heading"

    # Heuristic: bold, short (≥2 words), title-case, no bullets, has spacing.
    # Single-word bold lines are excluded: they are almost always role-header
    # continuations (e.g. a city name split onto its own line by Word) or
    # location fragments, not section headings.
    if (
        not pm.style.numbering
        and "|" not in text
        and not text.startswith(("-", "•", "·", "–"))
        and len(text) <= 60
        and pm.style.bold
        and not _YEAR_RE.search(text)
        and (
            (pm.style.spacing_before and pm.style.spacing_before >= 80)
            or (pm.style.font_size_pt and pm.style.font_size_pt >= 12)
        )
    ):
        words = text.split()
        if len(words) >= 2:
            cap_ratio = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
            if cap_ratio >= 0.7:
                return "section_heading"

    if "|" in text and not text.startswith(("-", "•")):
        return "role_header"

    if (
        pm.style.numbering
        or "list" in style_name.lower()
        or text.startswith(("- ", "• ", "· ", "– ", "* "))
    ):
        return "bullet"

    if _YEAR_RE.search(text) and len(text) <= 80 and "|" not in text:
        return "role_meta"

    return "paragraph"


# ---------------------------------------------------------------------------
# Group experience body paragraphs into RoleEntry list
# ---------------------------------------------------------------------------

def _group_roles(body_paras: list[ParaModel]) -> list[RoleEntry]:
    roles: list[RoleEntry] = []
    header: ParaModel | None = None
    header_extra: list[ParaModel] = []
    meta: list[ParaModel] = []
    bullets: list[ParaModel] = []
    state = "init"

    def _flush():
        nonlocal header
        if header is None:
            return
        roles.append(RoleEntry(
            header=header,
            header_extra=list(header_extra),
            meta_lines=list(meta),
            bullets=list(bullets),
            role_id=header.text.strip(),
        ))
        header = None
        header_extra.clear()
        meta.clear()
        bullets.clear()

    for pm in body_paras:
        s = pm.semantic

        if s == "role_header":
            _flush()
            header = pm
            state = "header"
        elif state == "init":
            pass  # pre-role content; skip
        elif state == "header":
            if s == "role_meta":
                meta.append(pm)
                state = "meta"
            elif s == "bullet":
                bullets.append(pm)
                state = "bullets"
            elif s == "paragraph":
                # Multi-line role header: Word can wrap long headers across
                # two paragraphs.  Collect as header_extra; do not render.
                header_extra.append(pm)
            elif s == "empty":
                pass
            else:
                meta.append(pm)
        elif state == "meta":
            if s == "role_meta":
                meta.append(pm)
            elif s in ("bullet", "paragraph"):
                bullets.append(pm)
                state = "bullets"
            elif s == "empty":
                pass
            else:
                bullets.append(pm)
                state = "bullets"
        elif state == "bullets":
            if s in ("bullet", "paragraph", "role_meta"):
                # role_meta can appear mid-bullet-list when a bullet line contains a
                # year (e.g. "Resolved 150 bugs since 2023 for apps post-launch to")
                # but is semantically a continuation bullet, not a date/meta line.
                bullets.append(pm)
            # role_header handled at top; ignore empty/other
        else:
            pass  # unreachable

    _flush()
    return roles


# ---------------------------------------------------------------------------
# Layout extraction
# ---------------------------------------------------------------------------

_EMU_PER_PT = 12700
_TWIP_PER_PT = 20


def _pt_from_emu(emu: int | None, fallback: float) -> float:
    if emu is None:
        return fallback
    return emu / _EMU_PER_PT


def _extract_layout(doc) -> LayoutProfile:
    section = doc.sections[0] if doc.sections else None
    if section is None:
        return LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )

    pw = _pt_from_emu(section.page_width.emu if section.page_width else None, 612)
    ph = _pt_from_emu(section.page_height.emu if section.page_height else None, 792)
    mt = _pt_from_emu(section.top_margin.emu if section.top_margin else None, 72)
    mb = _pt_from_emu(section.bottom_margin.emu if section.bottom_margin else None, 72)
    ml = _pt_from_emu(section.left_margin.emu if section.left_margin else None, 72)
    mr = _pt_from_emu(section.right_margin.emu if section.right_margin else None, 72)

    font_name = "Calibri"
    font_size_pt = 11.0
    try:
        default_style = doc.styles["Normal"]
        if default_style.font.name:
            font_name = default_style.font.name
        if default_style.font.size:
            font_size_pt = default_style.font.size.pt
    except Exception:
        pass

    return LayoutProfile(
        page_width_pt=pw, page_height_pt=ph,
        margin_top_pt=mt, margin_bottom_pt=mb,
        margin_left_pt=ml, margin_right_pt=mr,
        default_font_name=font_name,
        default_font_size_pt=font_size_pt,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_docx(path: str) -> ResumeDocument:
    """Parse a DOCX file into a ResumeDocument IR.

    Raises
    ------
    ValueError
        If the file cannot be opened or parsed.
    """
    doc = Document(path)
    style_map = _build_style_map(doc)
    layout = _extract_layout(doc)
    body = doc.element.body

    all_paras: list[ParaModel] = []
    body_items: list[ParaModel | TableBlock] = []

    for child in body:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local == "p":
            text = _get_para_text(child)
            style = _parse_para_style(child, style_map)
            pm = ParaModel(text=text, style=style, semantic="")
            pm.semantic = _infer_semantic(pm)
            all_paras.append(pm)
            body_items.append(pm)
        elif local == "tbl":
            table_paras: list[ParaModel] = []
            for p_elem in child.findall(f".//{{{_W}}}p"):
                text = _get_para_text(p_elem)
                style = _parse_para_style(p_elem, style_map)
                pm = ParaModel(text=text, style=style, semantic="")
                pm.semantic = _infer_semantic(pm)
                all_paras.append(pm)
                table_paras.append(pm)
            body_items.append(TableBlock(xml_proto=deepcopy(child), para_models=table_paras))
        # sectPr and other elements are ignored (preserved in the body XML)

    # Group into sections
    header_paras: list[ParaModel] = []
    sections: list[ResumeSection] = []
    current: ResumeSection | None = None
    found_heading = False

    for pm in all_paras:
        if pm.semantic == "section_heading":
            found_heading = True
            # Approach A: absorb non-standard heading paragraphs that appear inside an
            # experience section and are not recognised section names.  Handles templates
            # where role titles ("Senior Software Developer" without a pipe separator)
            # are formatted as bold headings but should remain body content.
            if (
                current is not None
                and current.semantic_type == "experience"
                and pm.text.strip().lower() not in _ALL_HEADING_NAMES
            ):
                pm.semantic = "paragraph"
                current.body_paras.append(pm)
                continue
            if current is not None:
                _finalise(current)
                sections.append(current)
            current = ResumeSection(
                title=pm.text.strip(),
                heading=pm,
                semantic_type=_classify_section(pm.text),
            )
        elif not found_heading:
            header_paras.append(pm)
        elif current is not None:
            current.body_paras.append(pm)

    if current is not None:
        _finalise(current)
        sections.append(current)

    return ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=all_paras,
        body_items=body_items,
    )


def _finalise(section: ResumeSection) -> None:
    if section.semantic_type == "experience":
        section.roles = _group_roles(section.body_paras)

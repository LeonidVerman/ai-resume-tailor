"""Parse a DOCX file and LLM plain text into structural models.

Two parsers live here:
  parse_docx(path_or_doc)  ->  DocumentModel
  parse_llm_text(text)     ->  list[LlmSection]

The DOCX parser uses style names, indent, bold, and text heuristics to infer
semantic types without depending on any specific style name or section title.
The LLM text parser splits on known section names (case-insensitive) plus
fallback title-case heuristics.
"""
from __future__ import annotations

import re
from typing import Any

from docx import Document
from docx.text.paragraph import Paragraph as DocxParagraph

from tailor.docx import _W
from tailor.docx.structural_model import (
    DocumentModel, LlmRole, LlmSection,
    ParagraphFormat, ParagraphModel, RoleBlock,
    RunModel, RunStyle, SectionBlock,
)

# ---------------------------------------------------------------------------
# Section semantic classification tables
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
    "technical skills", "skills", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees",
})

# All known section names — used by LLM text parser
_ALL_KNOWN_SECTION_NAMES: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES | frozenset({
        "certifications", "certification", "licenses", "publications",
        "projects", "volunteer", "volunteering", "awards", "honors",
        "references", "languages", "interests", "activities",
        "leadership", "leadership experience", "additional information",
    })
)

_HEADING_STYLE_RE = re.compile(r'^heading\s*\d', re.IGNORECASE)
_YEAR_RE = re.compile(r'\b(19|20)\d{2}\b')


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
    return "other"


# ---------------------------------------------------------------------------
# DOCX low-level helpers
# ---------------------------------------------------------------------------

def _parse_run_style(r_elem) -> RunStyle:
    rPr = r_elem.find(f"{{{_W}}}rPr")
    bold = italic = underline = None
    font_name = color = None
    font_size_pt = None
    caps = small_caps = None

    if rPr is not None:
        def _bool_elem(tag):
            el = rPr.find(f"{{{_W}}}{tag}")
            if el is None:
                return None
            v = el.get(f"{{{_W}}}val", "true")
            return v.lower() not in ("0", "false")

        bold = _bool_elem("b")
        italic = _bool_elem("i")
        caps = _bool_elem("caps")
        small_caps = _bool_elem("smallCaps")

        u = rPr.find(f"{{{_W}}}u")
        if u is not None:
            underline = u.get(f"{{{_W}}}val", "none") != "none"

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

    return RunStyle(
        bold=bold, italic=italic, underline=underline,
        font_name=font_name, font_size_pt=font_size_pt,
        caps=caps, small_caps=small_caps, color=color,
    )


def _parse_runs(p_elem) -> list[RunModel]:
    runs: list[RunModel] = []
    for child in p_elem:
        tag = child.tag
        if tag == f"{{{_W}}}r":
            text = "".join(t.text or "" for t in child.findall(f"{{{_W}}}t"))
            runs.append(RunModel(text=text, style=_parse_run_style(child), xml_ref=child))
        elif tag == f"{{{_W}}}hyperlink":
            for r in child.findall(f"{{{_W}}}r"):
                text = "".join(t.text or "" for t in r.findall(f"{{{_W}}}t"))
                runs.append(RunModel(text=text, style=_parse_run_style(r), xml_ref=r))
    return runs


def _get_para_text(p_elem) -> str:
    parts: list[str] = []
    for elem in p_elem.iter():
        if elem.tag == f"{{{_W}}}t":
            parts.append(elem.text or "")
        elif elem.tag == f"{{{_W}}}tab":
            parts.append("\t")
        elif elem.tag == f"{{{_W}}}br":
            parts.append("\n")
    return "".join(parts)


def _parse_para_format(p_elem, style_map: dict[str, str]) -> ParagraphFormat:
    """Extract paragraph formatting from w:p XML. style_map: id -> name."""
    pPr = p_elem.find(f"{{{_W}}}pPr")
    style_id = style_name = alignment = None
    indent_left = indent_right = hanging = None
    spacing_before = spacing_after = line_spacing = None
    keep_together = keep_with_next = page_break_before = None
    tabs: list[Any] = []
    numbering = None

    if pPr is not None:
        ps = pPr.find(f"{{{_W}}}pStyle")
        if ps is not None:
            style_id = ps.get(f"{{{_W}}}val")
            style_name = style_map.get(style_id, style_id)

        jc = pPr.find(f"{{{_W}}}jc")
        if jc is not None:
            alignment = jc.get(f"{{{_W}}}val")

        ind = pPr.find(f"{{{_W}}}ind")
        if ind is not None:
            for attr, dest in [("left", "l"), ("right", "r"), ("hanging", "h")]:
                v = ind.get(f"{{{_W}}}{attr}")
                if v:
                    if attr == "left":
                        indent_left = int(v)
                    elif attr == "right":
                        indent_right = int(v)
                    else:
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

        def _bool_ppr(tag):
            el = pPr.find(f"{{{_W}}}{tag}")
            if el is None:
                return None
            v = el.get(f"{{{_W}}}val", "true")
            return v.lower() not in ("0", "false")

        keep_together = _bool_ppr("keepLines")
        keep_with_next = _bool_ppr("keepNext")
        page_break_before = _bool_ppr("pageBreakBefore")

        tabs_el = pPr.find(f"{{{_W}}}tabs")
        if tabs_el is not None:
            tabs = list(tabs_el)

        numPr = pPr.find(f"{{{_W}}}numPr")
        if numPr is not None:
            ilvl_el = numPr.find(f"{{{_W}}}ilvl")
            numId_el = numPr.find(f"{{{_W}}}numId")
            if ilvl_el is not None and numId_el is not None:
                numbering = {
                    "ilvl": int(ilvl_el.get(f"{{{_W}}}val", "0")),
                    "numId": int(numId_el.get(f"{{{_W}}}val", "0")),
                }

    return ParagraphFormat(
        style_id=style_id, style_name=style_name, alignment=alignment,
        indent_left=indent_left, indent_right=indent_right, hanging=hanging,
        spacing_before=spacing_before, spacing_after=spacing_after,
        line_spacing=line_spacing, keep_together=keep_together,
        keep_with_next=keep_with_next, page_break_before=page_break_before,
        tabs=tabs, numbering=numbering,
    )


def _build_style_map(doc) -> dict[str, str]:
    """Return {style_id: style_name} for all styles in document."""
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

def _infer_semantic_type(pm: ParagraphModel) -> str:
    text = pm.text.strip()
    fmt = pm.fmt
    style_name = (fmt.style_name or "").strip()

    if not text:
        return "empty"

    # Explicit heading styles take priority
    if _HEADING_STYLE_RE.match(style_name):
        return "section_heading"
    if style_name.lower() in {"title", "subtitle"}:
        return "section_heading"

    # Heuristic heading detection: bold, short, no numbering, no bullets
    if (
        not fmt.numbering
        and "|" not in text
        and not text.startswith(("-", "•", "·", "–"))
        and len(text) <= 60
        and pm.runs
        and all(r.style.bold for r in pm.runs if r.text.strip())
        and (
            fmt.spacing_before and fmt.spacing_before >= 80
            or any(
                r.style.font_size_pt and r.style.font_size_pt >= 12
                for r in pm.runs
            )
        )
    ):
        words = text.split()
        cap_ratio = sum(1 for w in words if w and w[0].isupper()) / max(len(words), 1)
        if cap_ratio >= 0.7:
            return "section_heading"

    # Role header: contains pipe (Company | Role pattern)
    if "|" in text and not text.startswith(("-", "•")):
        return "role_header"

    # Bullet: has numbering, list-like style, or leading bullet char
    if (
        fmt.numbering
        or "list" in style_name.lower()
        or text.startswith(("- ", "• ", "· ", "– ", "* "))
    ):
        return "bullet"

    # Date/meta line: contains year, short, no pipe
    if _YEAR_RE.search(text) and len(text) <= 80 and "|" not in text:
        return "role_meta"

    return "paragraph"


# ---------------------------------------------------------------------------
# Group role blocks from an experience section's paragraphs
# ---------------------------------------------------------------------------

def _group_into_roles(paragraphs: list[ParagraphModel]) -> list[RoleBlock]:
    roles: list[RoleBlock] = []
    header: list[ParagraphModel] = []
    meta: list[ParagraphModel] = []
    bullets: list[ParagraphModel] = []
    trailing: list[ParagraphModel] = []
    role_idx = 0

    def _flush():
        nonlocal role_idx
        if not header and not bullets:
            return
        htext = " ".join(p.text.strip() for p in header)
        roles.append(RoleBlock(
            header_paragraphs=list(header),
            meta_paragraphs=list(meta),
            bullet_paragraphs=list(bullets),
            trailing_paragraphs=list(trailing),
            role_id=htext or f"role_{role_idx}",
        ))
        role_idx += 1
        header.clear(); meta.clear(); bullets.clear(); trailing.clear()

    state = "init"
    for pm in paragraphs:
        t = pm.semantic_type

        if t == "role_header":
            _flush()
            header.append(pm)
            state = "header"

        elif state == "init":
            trailing.append(pm)  # pre-first-role content

        elif state == "header":
            if t == "role_meta":
                meta.append(pm); state = "meta"
            elif t == "bullet":
                bullets.append(pm); state = "bullets"
            elif t == "empty":
                trailing.append(pm); state = "trailing"
            else:
                header.append(pm)  # multi-line header

        elif state == "meta":
            if t == "bullet":
                bullets.append(pm); state = "bullets"
            elif t == "role_meta":
                meta.append(pm)
            elif t == "empty":
                trailing.append(pm); state = "trailing"
            elif t == "role_header":
                _flush(); header.append(pm); state = "header"
            else:
                bullets.append(pm); state = "bullets"

        elif state == "bullets":
            if t == "bullet" or t == "paragraph":
                bullets.append(pm)
            elif t == "empty":
                trailing.append(pm); state = "trailing"
            elif t == "role_header":
                _flush(); header.append(pm); state = "header"
            else:
                bullets.append(pm)

        elif state == "trailing":
            if t == "role_header":
                _flush(); header.append(pm); state = "header"
            elif t == "empty":
                trailing.append(pm)
            elif t in ("bullet", "paragraph"):
                # late content — attach to current role
                bullets.extend(trailing); trailing.clear()
                bullets.append(pm); state = "bullets"
            else:
                trailing.append(pm)

    _flush()
    return roles


# ---------------------------------------------------------------------------
# DOCX structural parser
# ---------------------------------------------------------------------------

def parse_docx(path_or_doc) -> DocumentModel:
    """Parse a DOCX file (path string or Document object) into a DocumentModel."""
    if isinstance(path_or_doc, str):
        doc = Document(path_or_doc)
    else:
        doc = path_or_doc

    style_map = _build_style_map(doc)
    body = doc.element.body

    all_paragraphs: list[ParagraphModel] = []

    # Iterate body children in document order, including tables
    for child in body:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag

        if local == "p":
            text = _get_para_text(child)
            runs = _parse_runs(child)
            fmt = _parse_para_format(child, style_map)
            pm = ParagraphModel(text=text, runs=runs, fmt=fmt, xml_ref=child)
            pm.semantic_type = _infer_semantic_type(pm)
            all_paragraphs.append(pm)

        elif local == "tbl":
            # Traverse table cells; mark paragraphs as table content
            for row in child.findall(f".//{{{_W}}}tr"):
                for cell in row.findall(f".//{{{_W}}}tc"):
                    for p_elem in cell.findall(f"{{{_W}}}p"):
                        text = _get_para_text(p_elem)
                        runs = _parse_runs(p_elem)
                        fmt = _parse_para_format(p_elem, style_map)
                        pm = ParagraphModel(text=text, runs=runs, fmt=fmt, xml_ref=p_elem)
                        pm.semantic_type = _infer_semantic_type(pm)
                        all_paragraphs.append(pm)

    # Group into sections
    header_paras: list[ParagraphModel] = []
    sections: list[SectionBlock] = []
    current: SectionBlock | None = None
    found_heading = False

    for pm in all_paragraphs:
        if pm.semantic_type == "section_heading":
            found_heading = True
            if current is not None:
                sections.append(_finalize_section(current))
            current = SectionBlock(
                heading=pm,
                body_paragraphs=[],
                roles=[],
                semantic_type=_classify_section(pm.text),
                raw_heading_text=pm.text.strip(),
            )
        elif not found_heading:
            header_paras.append(pm)
        elif current is not None:
            current.body_paragraphs.append(pm)

    if current is not None:
        sections.append(_finalize_section(current))

    debug_info = {
        "total_paragraphs": len(all_paragraphs),
        "sections": [
            {
                "heading": s.raw_heading_text,
                "type": s.semantic_type,
                "body_count": len(s.body_paragraphs),
                "role_count": len(s.roles),
            }
            for s in sections
        ],
        "archetypes_discovered": {},
    }

    return DocumentModel(
        header_paragraphs=header_paras,
        sections=sections,
        all_paragraphs=all_paragraphs,
        debug_info=debug_info,
    )


def _finalize_section(section: SectionBlock) -> SectionBlock:
    if section.semantic_type == "experience":
        section.roles = _group_into_roles(section.body_paragraphs)
    return section


# ---------------------------------------------------------------------------
# LLM plain-text parser
# ---------------------------------------------------------------------------

def _is_llm_section_heading(line: str) -> bool:
    """Return True if *line* looks like a section heading in LLM output.

    Primary signal: exact match against the comprehensive known-section-names list
    (case-insensitive).  A tight title-case heuristic fires only for very short
    lines without commas, colons, or other list punctuation.
    """
    s = line.strip()
    if not s:
        return False
    if s.startswith(("-", "•", "·", "–", "*")):
        return False
    if "|" in s:
        return False
    if len(s) > 60:
        return False
    if s.lower() in _ALL_KNOWN_SECTION_NAMES:
        return True
    # Title-case fallback: only for 1–3 word lines with no list punctuation
    if "," in s or ";" in s or ":" in s or s[-1] in ".!?":
        return False
    words = s.split()
    if len(words) > 3:
        return False
    if not all(w[0].isupper() for w in words if w):
        return False
    # Exclude date-like patterns
    if _YEAR_RE.search(s):
        return False
    return True


def _is_llm_role_header(line: str) -> bool:
    s = line.strip()
    return bool(s) and "|" in s and not s.startswith(("-", "•"))


def _is_llm_date_line(line: str) -> str:
    """Return True if line looks like a date/location meta line."""
    s = line.strip()
    return bool(s) and _YEAR_RE.search(s) and "|" not in s and not s.startswith("-")


def parse_llm_text(text: str) -> list[LlmSection]:
    """Parse LLM plain-text resume output into a list of LlmSection objects."""
    lines = text.split("\n")
    sections: list[LlmSection] = []
    current: LlmSection | None = None

    def _flush():
        if current is not None:
            sections.append(current)

    # State machine per line
    state = "pre"   # pre | body | role_header | meta | bullets
    cur_role: LlmRole | None = None

    def _finish_role():
        nonlocal cur_role
        if cur_role is not None and current is not None:
            current.roles.append(cur_role)
            cur_role = None

    for line in lines:
        stripped = line.strip()

        if _is_llm_section_heading(line):
            _finish_role()
            _flush()
            sem = _classify_section(stripped)
            current = LlmSection(heading=stripped, body_lines=[], roles=[], semantic_type=sem)
            state = "body"
            cur_role = None
            continue

        if current is None:
            # Text before first section heading — ignore (header info)
            continue

        if not stripped:
            # Blank line — skip (don't treat as content)
            continue

        if _is_llm_role_header(line):
            _finish_role()
            cur_role = LlmRole(header=stripped, meta_lines=[], bullets=[])
            state = "role_header"
            continue

        if state == "body" or cur_role is None:
            # Non-experience section body line
            if stripped.startswith("- "):
                current.body_lines.append(stripped[2:])
            else:
                current.body_lines.append(stripped)

        elif state == "role_header":
            # Line after a role header: could be meta (date) or bullet
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            elif _is_llm_date_line(line):
                cur_role.meta_lines.append(stripped)
                state = "meta"
            else:
                cur_role.meta_lines.append(stripped)
                state = "meta"

        elif state == "meta":
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            elif _is_llm_date_line(line):
                cur_role.meta_lines.append(stripped)
            else:
                cur_role.bullets.append(stripped)
                state = "bullets"

        elif state == "bullets":
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
            else:
                # Plain line in bullet area — treat as bullet text without dash
                cur_role.bullets.append(stripped)

    _finish_role()
    _flush()
    return sections

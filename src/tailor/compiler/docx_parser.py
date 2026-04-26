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
    LayoutParagraphBlock,
    LayoutProfile,
    LayoutTableBlock,
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
    "experience", "experiences", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background",
    # Non-canonical but common variants found in real templates:
    "employment summary", "work summary", "experience summary",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "about me", "career summary",
    "executive summary", "overview",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "skill", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack", "relevant skills",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees", "educational history",
})
_CERTIFICATIONS_NAMES: frozenset[str] = frozenset({
    "certifications", "certification", "licenses", "license",
    "certifications and training", "training and certifications",
    "training", "courses", "professional development",
})
_LANGUAGES_NAMES: frozenset[str] = frozenset({
    "languages", "language skills",
})
_WEBSITES_NAMES: frozenset[str] = frozenset({
    "websites", "profiles", "social profiles", "links",
    "portfolio", "web profiles", "online profiles",
})

_ALL_HEADING_NAMES: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES
    | _CERTIFICATIONS_NAMES | _LANGUAGES_NAMES | _WEBSITES_NAMES
    | frozenset({
        "projects", "publications",
        "awards", "honors", "references", "activities",
        "volunteer", "volunteering", "leadership", "interests",
        "additional information", "communication",
        "affiliations", "affiliations and awards", "affiliations & awards",
        "contact",
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

# E: keyword set for noncanonical websites/portfolio headings.
# Applied as a word-level fallback so compound headings like
# "Websites, Portfolios, Profiles" are classified as "websites".
_WEBSITES_LIKE_WORDS: frozenset[str] = frozenset({
    "website", "websites", "portfolio", "portfolios",
    "profile", "profiles", "social", "links", "online",
})

_HEADING_STYLE_RE = re.compile(r"^heading\s*\d", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_DATE_PLACEHOLDER_RE = re.compile(r"\b20[Xx]{2}\b", re.IGNORECASE)
_DATE_RANGE_RE = re.compile(r"[-\u2013\u2014]")  # dash/en-dash/em-dash in date ranges


def _heading_level(pm: "ParaModel") -> int | None:
    """Return the numeric level from a 'Heading N' style, or None."""
    style_name = (pm.style.style_name or "").strip()
    m = re.match(r"^heading\s*(\d+)", style_name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _classify_section(heading_text: str) -> str:
    """Classify a section heading into a semantic type.

    Returns one of: experience | summary | skills | education |
    certifications | languages | websites | other.

    Locked types (certifications, languages, websites) map to those specific
    return values so apply_tailored can enforce write-protection without
    requiring caller-side heading-name checks.
    """
    t = heading_text.strip().lower()
    if t in _EXPERIENCE_NAMES:
        return "experience"
    if t in _SUMMARY_NAMES:
        return "summary"
    if t in _SKILLS_NAMES:
        return "skills"
    if t in _EDUCATION_NAMES:
        return "education"
    if t in _CERTIFICATIONS_NAMES:
        return "certifications"
    if t in _LANGUAGES_NAMES:
        return "languages"
    if t in _WEBSITES_NAMES:
        return "websites"
    # D/E: word-level fallback for noncanonical compound headings.
    # Split on whitespace and common delimiters so headings like
    # "Websites, Portfolios, Profiles" or "Core Technologies" match.
    words = re.split(r"[\s/&,]+", t)
    # E: websites check before skills — "profiles" must not fall through to "skills".
    if any(w in _WEBSITES_LIKE_WORDS for w in words if w):
        return "websites"
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

    # Style "Heading" (bare, without a level digit) paired with a known section
    # name.  Some templates use a custom "Heading" paragraph style for section
    # titles that does not inherit a numbered Word heading style (e.g. "Heading 1"),
    # so the regex above does not match.  Guarded by the known-name set to avoid
    # false positives on non-heading paragraphs that share the same style (e.g.
    # the candidate's own name in the document header area).
    if style_name.lower() == "heading" and text.lower() in _ALL_HEADING_NAMES:
        return "section_heading"

    # Known section names (bold): bold paragraph exactly matching a recognized
    # section name is a heading regardless of font size or spacing.
    if pm.style.bold and text.lower() in _ALL_HEADING_NAMES:
        return "section_heading"

    # Known section names (plain paragraph, no formatting at all): a paragraph
    # with no named style, not bold, and no explicit spacing_before that exactly
    # matches a known section name.  Targets templates like 20-Software-Engineer
    # where section titles are plain 12pt text with no paragraph-level styling.
    # Guarded by spacing_before=None so that PDF-generated DOCX headings (which
    # carry spacing_before=20 from the style template) are not affected.
    if (
        not style_name
        and not pm.style.bold
        and pm.style.spacing_before is None
        and text.lower() in _ALL_HEADING_NAMES
    ):
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
    # " / " separator (space-slash-space): role_header only when neither the
    # segment before nor after the slash looks like a date/year.
    # "Lamna Health / General Practitioner" → role_header ✓
    # "January 2022 - current / New York, NY" → date line, NOT role_header ✓
    if " / " in text and not text.startswith(("-", "•")):
        before_slash, after_slash = text.split(" / ", 1)
        if not _YEAR_RE.search(before_slash) and not _YEAR_RE.search(after_slash.strip()):
            return "role_header"

    if (
        pm.style.numbering
        or "list" in style_name.lower()
        or text.startswith(("- ", "• ", "· ", "– ", "* ", "\u25cf", "\u25e6"))
    ):
        return "bullet"

    # Fused year-prefix: "2023CompanyName" — year glued to next word so the
    # \b boundary in _YEAR_RE doesn't fire.  Treat as role_meta.
    if re.match(r"^(19|20)\d{2}[A-Za-z]", text) and len(text) <= 80 and "|" not in text:
        return "role_meta"

    # NBSP/space-column role header: job title and date/company are placed on
    # the same paragraph and aligned using non-breaking spaces (\\xa0) or tab
    # stops instead of a "|" separator.  Detect by splitting on 3+ consecutive
    # nbsp/space characters (or a tab): if the first segment looks like a job
    # title (short, no year) and the full text contains a year, it is a role
    # header rather than a date-only meta line or a body paragraph.
    if _YEAR_RE.search(text):
        first_seg = re.split(r"[\xa0 ]{3,}|\t+", text.strip())[0].strip()
        if (
            first_seg
            and len(first_seg) <= 60
            and first_seg[-1] not in ".!?,;:"
            and not _YEAR_RE.search(first_seg)
        ):
            return "role_header"

    if _YEAR_RE.search(text) and len(text) <= 80 and "|" not in text:
        return "role_meta"

    return "paragraph"


# ---------------------------------------------------------------------------
# Group experience body paragraphs into RoleEntry list
# ---------------------------------------------------------------------------

def _relabel_implicit_role_headers(body_paras: list[ParaModel]) -> None:
    """Relabel 'paragraph' semantics to 'role_header' for standalone job-title
    lines that lack the usual |/NBSP/tab marker but precede a date/meta line.

    Many resume templates place the job title on its own paragraph and the
    company + date either immediately below or one paragraph below (company
    name sandwiched between title and date).  This pass detects that pattern
    before _group_roles() runs so the grouping state-machine finds the correct
    role boundaries.

    A paragraph is relabeled when ALL of the following hold:
      1. Current semantic is 'paragraph' (not already detected as something else)
      2. Text is short (≤ 60 chars), contains no year, and does not start with
         a bullet character
      3. The text contains at least one word from _JOB_TITLE_WORDS (job title
         signal; guards against relabeling company-name or content lines)
      4. The FIRST non-empty paragraph that follows is NOT already a role_header
         (avoids double-marking when the company|date line already has a pipe)
      5. Within the next two non-empty paragraphs there is either a role_meta
         paragraph OR a paragraph whose text contains a four-digit year

    Mutations are applied in place; no new ParaModel objects are created.
    """
    n = len(body_paras)
    for i, pm in enumerate(body_paras):
        if pm.semantic != "paragraph":
            continue
        text = pm.text.strip()
        if not text or len(text) > 60 or _YEAR_RE.search(text):
            continue
        if text[0] in "-\u2022\u00b7\u2013*":
            continue
        words = set(re.split(r"\W+", text.lower()))
        if not (words & _JOB_TITLE_WORDS):
            continue

        # Collect the next two non-empty paragraphs.
        ahead: list[ParaModel] = []
        for j in range(i + 1, min(i + 8, n)):
            nxt = body_paras[j]
            if nxt.text.strip():
                ahead.append(nxt)
                if len(ahead) >= 2:
                    break

        if not ahead:
            continue
        # Guard: if immediately followed by an existing role_header, the
        # company|date line is already correctly labeled — skip to avoid
        # creating a duplicate boundary.
        if ahead[0].semantic == "role_header":
            continue
        # Relabel if any of the next two substantive paragraphs is role_meta
        # or contains a year.  The placeholder check ("20xx") is restricted to
        # the FIRST lookahead only — checking the second would falsely promote
        # a job title whose first lookahead is content and whose second is the
        # *next* role's date (e.g. 6-Template1 / "Jan 20XX - Current" pattern).
        for a in ahead:
            if a.semantic == "role_meta" or (
                a.semantic == "paragraph" and _YEAR_RE.search(a.text)
            ):
                pm.semantic = "role_header"
                break
            if (
                a is ahead[0]
                and a.semantic == "paragraph"
                and _DATE_PLACEHOLDER_RE.search(a.text)
            ):
                pm.semantic = "role_header"
                break


_EDUCATION_INSTITUTION_WORDS = frozenset(
    ["university", "college", "school", "institute", "academy", "polytechnic"]
)
_EDUCATION_DEGREE_WORDS = frozenset(
    ["bachelor", "master", "b.sc", "m.sc", "ph.d", "diploma", "associate", "undergraduate"]
)


def _is_education_intrusion_meta(pm: "ParaModel", recent_bullets: list["ParaModel"]) -> bool:
    """Return True when a role_meta looks like an education institution line
    (e.g. 'Your University May 2020') that has wandered into an experience role
    body due to a two-column table layout.

    Two signals must both fire:
    1. The role_meta text contains an institution keyword.
    2. At least one of the last 4 non-empty bullets contains a degree keyword.
    """
    txt_lower = pm.text.strip().lower()
    if not any(w in txt_lower for w in _EDUCATION_INSTITUTION_WORDS):
        return False
    recent_non_empty = [b for b in recent_bullets if b.text.strip()][-4:]
    return any(
        any(w in b.text.strip().lower() for w in _EDUCATION_DEGREE_WORDS)
        for b in recent_non_empty
    )


def _group_roles(body_paras: list[ParaModel]) -> list[RoleEntry]:
    roles: list[RoleEntry] = []
    header: ParaModel | None = None
    header_extra: list[ParaModel] = []
    meta: list[ParaModel] = []
    bullets: list[ParaModel] = []
    state = "init"
    # True when the current role was started by a role_meta line (Pattern B —
    # no role_header precedes the first date/company line).  In Pattern B
    # documents every role boundary IS a role_meta, so a new role_meta that
    # appears after bullets have started must be treated as the next boundary,
    # not absorbed as a continuation bullet.  Mirrored from pdf_parser.py
    # used_pattern_b logic.
    header_is_role_meta = False

    def _flush():
        nonlocal header, header_is_role_meta
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
        header_is_role_meta = False
        header_extra.clear()
        meta.clear()
        bullets.clear()

    for pm in body_paras:
        s = pm.semantic

        if s == "role_header":
            _flush()
            header = pm
            header_is_role_meta = False
            state = "header"
        elif state == "init":
            if s == "role_meta":
                # Pattern B: date/meta line precedes any role_header (e.g.
                # "2014-2016\nCompany Name\nJob Title\n…").  Use the meta line
                # as a synthetic role boundary so subsequent content is captured.
                _flush()
                header = pm
                header_is_role_meta = True
                state = "header"
            # all other pre-role content is silently skipped
        elif state == "header":
            if s == "role_meta":
                meta.append(pm)
                state = "meta"
            elif s == "bullet":
                bullets.append(pm)
                state = "bullets"
            elif s == "paragraph":
                _txt = pm.text.strip()
                # Date-range line in header state (e.g. "January 20xx - Current"):
                # route to meta so it doesn't appear in the rendered role header.
                if (
                    (_YEAR_RE.search(_txt) or _DATE_PLACEHOLDER_RE.search(_txt))
                    and len(_txt) <= 80
                ):
                    pm.semantic = "role_meta"
                    meta.append(pm)
                    state = "meta"
                else:
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
            if s == "role_meta" and header_is_role_meta:
                # Pattern B continuation: current role was started by a role_meta
                # boundary; a new role_meta after bullets signals the next job.
                # Flush the current role and start the new one.
                _flush()
                header = pm
                header_is_role_meta = True
                state = "header"
            elif s in ("bullet", "paragraph", "role_meta"):
                # role_meta can appear mid-bullet-list when a bullet line contains a
                # year (e.g. "Resolved 150 bugs since 2023 for apps post-launch to")
                # but is semantically a continuation bullet, not a date/meta line.
                # This branch is only reached when header_is_role_meta is False
                # (role started via role_header), so role_meta here is a false positive —
                # UNLESS it looks like an education institution line that wandered in from
                # a two-column table layout (e.g. "Your University May 2020").
                if s == "role_meta" and _is_education_intrusion_meta(pm, bullets):
                    pass  # drop — do not add to this role's bullets
                else:
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

    # Fix newspaper/table multi-column layout: when the document uses Word's
    # newspaper-column feature, content from different visual columns is interleaved
    # via <w:br type="column"> breaks.  This reorders all_paras to column-first order
    # (all col-0 paragraphs, then col-1, then col-2) before section grouping runs.
    # body_items is intentionally not modified.
    all_paras, _table_col_fixed, _table_col_meta = _apply_multicolumn_newspaper_fix(
        all_paras, body
    )

    # Fix label-column layout: when the document uses a narrow left column of section
    # labels and a wide right column of content, the linear XML order puts all labels
    # before all content.  This reorders all_paras so each heading is adjacent to its
    # content before section grouping runs.  body_items is intentionally not modified.
    all_paras, _label_col_fixed = _apply_label_column_fix(all_paras, body)

    # Group into sections
    header_paras: list[ParaModel] = []
    sections: list[ResumeSection] = []
    current: ResumeSection | None = None
    found_heading = False

    for pm in all_paras:
        if pm.semantic == "section_heading":
            # Pre-section guard: before the first known section, headings that are
            # not recognised section names AND contain no job-title words go to
            # header_paras.  This prevents candidate names ("Sheetal Parmar",
            # "HARPER RUSSO") from becoming bogus sections while still allowing
            # job-title headings ("Software Engineer", "Senior Developer") that
            # will later be consolidated into a synthetic experience section.
            if not found_heading and pm.text.strip().lower() not in _ALL_HEADING_NAMES:
                _guard_words = set(re.split(r"\W+", pm.text.strip().lower())) - {""}
                if not (_guard_words & _JOB_TITLE_WORDS):
                    header_paras.append(pm)
                    continue
            found_heading = True
            # Approach A: absorb sub-entry heading paragraphs that appear inside an
            # entry-type section and are not recognised top-level section names.
            #
            # For "experience": absorb any non-known heading — handles role titles
            # like "Senior Software Developer" formatted as bold headings without a
            # pipe separator.
            #
            # For "education" / "certifications": absorb ONLY when the paragraph does
            # not classify as a distinct semantic section (i.e. _classify_section
            # returns "other").  This keeps institution names / degree lines / cert
            # entries inside their parent section while still promoting a subsequent
            # "SKILLS & ABILITIES" (semantic_type="skills") to a peer section.
            t_lower = pm.text.strip().lower()
            # Heading-level guard: never absorb a Heading-N paragraph into a
            # section whose own heading uses Heading-M with M >= N.  In
            # table-based templates the section labels ("Communication",
            # "Leadership") use the same "Heading 1" style as top-level section
            # headings ("Experience"), so absorbing them would incorrectly merge
            # distinct sections.  Only fire this guard when both paragraphs have
            # an explicit numbered heading style — avoids interfering with
            # bold/heuristic headings.
            new_level = _heading_level(pm)
            cur_level = _heading_level(current.heading) if current is not None else None
            _same_or_higher = (
                new_level is not None
                and cur_level is not None
                and new_level <= cur_level
            )
            if (
                current is not None
                and not _same_or_higher
                and t_lower not in _ALL_HEADING_NAMES
                and (
                    current.semantic_type == "experience"
                    or (
                        current.semantic_type in {
                            "education", "certifications", "other",
                        }
                        and _classify_section(pm.text.strip()) == "other"
                    )
                )
            ):
                # Preserve role_header for pipe-separated or slash-separated lines
                # (e.g. "Title | Company", "Lamna Health / General Practitioner")
                # that were styled as Heading N and therefore initially classified as
                # section_heading but are semantically role headers inside experience.
                _is_slash_role = (
                    " / " in pm.text
                    and not _YEAR_RE.search(pm.text.split(" / ", 1)[0])
                    and not _YEAR_RE.search(pm.text.split(" / ", 1)[1].strip())
                )
                if (
                    current.semantic_type == "experience"
                    and ("|" in pm.text or _is_slash_role)
                    and not pm.text.lstrip().startswith(("-", "\u2022", "\u00b7", "\u2013"))
                ):
                    pm.semantic = "role_header"
                elif (
                    current.semantic_type == "experience"
                    and new_level is not None
                    and new_level <= 2  # Heading 2 absorbed into Heading 1 section
                    and len(pm.text.strip()) <= 60
                    and _DATE_RANGE_RE.search(pm.text)
                ):
                    # Absorbed Heading 2 that looks like a date range (e.g. "June
                    # 20XX – Present").  Promote to role_meta so _group_roles can
                    # detect role boundaries even when the year regex cannot match
                    # placeholder text.  Heading 3+ is left as paragraph to avoid
                    # misidentifying sub-heading dates in other templates.
                    pm.semantic = "role_meta"
                else:
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

    # Approach B: consolidate consecutive 'other' sections that look like standalone
    # job entries into a single synthetic 'experience' section.  Handles templates
    # that title each job role as a section heading ("Software Engineer", "Software
    # Engineer Intern") without a containing "Work Experience" / "Experience" header.
    sections = _consolidate_job_entry_sections(sections)

    doc = ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=all_paras,
        body_items=body_items,
        label_column_fixed=_label_col_fixed,
        table_column_layout_fixed=_table_col_fixed,
    )
    from tailor.compiler.models import assign_stable_ids
    assign_stable_ids(doc)

    # Build serializable layout tree (Option B: XML prototypes as strings).
    # Iterates body_items (original physical document order, never reordered)
    # and serializes each w:p / w:tbl element as a Unicode XML string.
    # para_id="" for structural/orphan paragraphs that are not in the semantic
    # model (e.g. column-break transitions); the renderer renders these verbatim.
    from tailor.config import USE_SERIALIZED_LAYOUT_TREE
    if USE_SERIALIZED_LAYOUT_TREE:
        from lxml import etree as _etree
        _layout_blocks: list = []
        _tbl_counter = 0
        for _item in body_items:
            if isinstance(_item, TableBlock):
                _tbl_counter += 1
                _layout_blocks.append(LayoutTableBlock(
                    table_id=f"tbl_{_tbl_counter}",
                    xml_proto_xml=_etree.tostring(_item.xml_proto, encoding="unicode"),
                    para_ids=[p.para_id for p in _item.para_models],
                ))
            else:
                # ParaModel — serialize xml_proto when present
                _xml_str = (
                    _etree.tostring(_item.style.xml_proto, encoding="unicode")
                    if _item.style.xml_proto is not None
                    else None
                )
                _layout_blocks.append(LayoutParagraphBlock(
                    para_id=_item.para_id,
                    xml_proto_xml=_xml_str,
                ))
        doc.layout_blocks = _layout_blocks

    return doc


# G: words that commonly appear in job/role titles.  Used by
# _is_job_entry_section to distinguish role-titled sections (e.g.
# "Software Engineer", "Senior Developer Intern") from company-named,
# school-named, or personal-name sections (e.g. "Liceria & Co.",
# "Harvard University", "John Smith").
_JOB_TITLE_WORDS: frozenset[str] = frozenset({
    "engineer", "developer", "programmer", "designer", "analyst",
    "architect", "manager", "director", "lead", "senior", "junior",
    "intern", "associate", "specialist", "consultant", "coordinator",
    "administrator", "technician", "scientist", "researcher",
    "officer", "executive", "head", "principal", "staff",
})


def _is_job_entry_section(sec: ResumeSection) -> bool:
    """Return True when an 'other'-typed section looks like a standalone job entry.

    Two conditions must hold:
    1. The section heading contains at least one word from _JOB_TITLE_WORDS
       (guards against merging company-name, school-name, or personal-name
       sections that happen to contain a date and body text).
    2. The body contains at least one role_meta paragraph (date/location line)
       AND at least one content paragraph (bullet or paragraph).
    """
    heading_words = set(re.split(r'\W+', sec.title.lower()))
    if not (heading_words & _JOB_TITLE_WORDS):
        return False
    has_meta = any(p.semantic == "role_meta" for p in sec.body_paras)
    has_content = any(
        p.semantic in ("bullet", "paragraph") and p.text.strip()
        for p in sec.body_paras
    )
    return has_meta and has_content


def _consolidate_job_entry_sections(sections: list[ResumeSection]) -> list[ResumeSection]:
    """Merge consecutive 'other' sections that look like job entries into one experience section.

    When a template omits an explicit "Experience" heading and instead headings
    each role individually (e.g. "Software Engineer", "Software Engineer Intern"),
    the parser produces multiple 'other' sections.  Two or more consecutive such
    sections are consolidated into a single synthetic 'experience' section so the
    LLM's Experience output can be matched to them via semantic-type in pass 2 of
    _match_sections.
    """
    result: list[ResumeSection] = []
    i = 0
    while i < len(sections):
        sec = sections[i]
        if sec.semantic_type == "other" and _is_job_entry_section(sec):
            j = i + 1
            while (
                j < len(sections)
                and sections[j].semantic_type == "other"
                and _is_job_entry_section(sections[j])
            ):
                j += 1
            job_secs = sections[i:j]
            if len(job_secs) >= 2:
                result.append(_make_experience_from_job_sections(job_secs))
                i = j
                continue
        result.append(sec)
        i += 1
    return result


def _make_experience_from_job_sections(job_secs: list[ResumeSection]) -> ResumeSection:
    """Build a synthetic 'experience' ResumeSection from a run of job-entry sections.

    The first section's heading provides the style prototype for a synthetic
    "Experience" heading.  Each section's heading becomes a RoleEntry.header and
    its body paragraphs are split into meta_lines (role_meta) and bullets.
    """
    synthetic_heading = job_secs[0].heading.with_text("Experience")

    roles: list[RoleEntry] = []
    all_body_paras: list[ParaModel] = []
    for sec in job_secs:
        # Identify the company-name paragraph: the first non-empty 'paragraph'
        # semantic para that appears before any role_meta para.  In templates
        # where each job is a plain heading + "Company Name" + "Date" + bullets,
        # the company name is structurally part of the meta (it identifies the
        # employer) and should be placed before the date in meta_lines so the
        # rendered order matches the original template (title → company → date).
        company_para: ParaModel | None = None
        for p in sec.body_paras:
            if p.semantic == "role_meta":
                break
            if p.semantic == "paragraph" and p.text.strip():
                company_para = p
                break

        date_meta = [p for p in sec.body_paras if p.semantic == "role_meta"]
        meta_lines = ([company_para] if company_para else []) + date_meta
        bullets = [
            p for p in sec.body_paras
            if p.semantic in ("bullet", "paragraph") and p.text.strip()
            and p is not company_para
        ]
        roles.append(RoleEntry(
            header=sec.heading,
            meta_lines=meta_lines,
            bullets=bullets,
            role_id=sec.heading.text.strip(),
        ))
        all_body_paras.extend(sec.body_paras)

    return ResumeSection(
        title="Experience",
        heading=synthetic_heading,
        semantic_type="experience",
        body_paras=all_body_paras,
        roles=roles,
    )


# ---------------------------------------------------------------------------
# Label-column layout detection and fix
# ---------------------------------------------------------------------------

def _detect_label_column_layout(body) -> bool:
    """Return True when the body sectPr defines exactly 2 columns with a narrow first column.

    A narrow first column is one whose width is less than 35 % of the combined width of
    both columns.  This pattern is used by templates that place section labels in a slim
    left rail and all resume content in a wide right rail.
    """
    body_sectPr = body.find(f"{{{_W}}}sectPr")
    if body_sectPr is None:
        return False
    cols_elem = body_sectPr.find(f"{{{_W}}}cols")
    if cols_elem is None:
        return False
    col_elems = cols_elem.findall(f"{{{_W}}}col")
    if len(col_elems) != 2:
        return False
    try:
        w0 = int(col_elems[0].get(f"{{{_W}}}w") or 0)
        w1 = int(col_elems[1].get(f"{{{_W}}}w") or 0)
    except (ValueError, TypeError):
        return False
    total = w0 + w1
    return total > 0 and (w0 / total) < 0.35


def _find_label_column_split(paras: list[ParaModel]) -> int | None:
    """Return the index of the first right-column (content) paragraph, or None.

    In a label-column layout the left column contains only section headings and
    empty spacing paragraphs.  The split point is the first non-empty paragraph
    that appears after at least two recognised section headings with nothing but
    empty paragraphs between them.

    Only paragraphs whose text matches a known section name (_ALL_HEADING_NAMES)
    are counted — this excludes candidate job-title headings in the header area
    (e.g. "SOFTWARE ENGINEER" styled as Heading N) that are not section labels.
    """
    heading_count = 0
    first_heading_seen = False
    for i, pm in enumerate(paras):
        is_known_section = (
            pm.semantic == "section_heading"
            and pm.text.strip().lower() in _ALL_HEADING_NAMES
        )
        if is_known_section:
            heading_count += 1
            first_heading_seen = True
        elif first_heading_seen and pm.text.strip():
            if heading_count >= 2:
                return i
            return None  # Content appeared before 2 headings accumulated → normal doc
    return None


_DEGREE_TITLE_WORDS_LBL: frozenset[str] = frozenset({
    "bachelor", "master", "doctorate", "phd", "mba", "associate", "diploma",
    "bs", "ms", "ba", "ma", "bsc", "msc",
    "science", "arts", "engineering", "technology", "business",
    "computing", "computer", "information",
})


def _looks_like_degree_title_lbl(text: str) -> bool:
    words = frozenset(re.split(r"\W+", text.lower())) - {"", "of", "in", "the", "and", "a"}
    return bool(words & _DEGREE_TITLE_WORDS_LBL)


def _lbl_find_experience_start(right_paras: list[ParaModel], min_idx: int) -> int:
    """First index >= min_idx where a job entry starts (first paragraph before a role_meta)."""
    for i in range(min_idx, len(right_paras)):
        if not right_paras[i].text.strip():
            continue
        ahead = [
            right_paras[j]
            for j in range(i + 1, min(i + 8, len(right_paras)))
            if right_paras[j].text.strip()
        ][:4]
        if any(p.semantic == "role_meta" for p in ahead):
            return i
    return min_idx


def _lbl_find_education_start(right_paras: list[ParaModel], min_idx: int) -> int:
    """First index >= min_idx whose text looks like a degree or institution title."""
    for i in range(min_idx, len(right_paras)):
        pm = right_paras[i]
        if pm.text.strip() and _looks_like_degree_title_lbl(pm.text):
            return i
    return min_idx


def _lbl_find_skills_start(right_paras: list[ParaModel], min_idx: int) -> int:
    """First index >= min_idx of a skills paragraph: non-role_meta, non-degree, no nearby dates."""
    for i in range(min_idx, len(right_paras)):
        pm = right_paras[i]
        if not pm.text.strip():
            continue
        if pm.semantic == "role_meta":
            continue
        if _looks_like_degree_title_lbl(pm.text):
            continue
        has_nearby_date = any(
            right_paras[j].semantic == "role_meta"
            for j in range(i + 1, min(i + 6, len(right_paras)))
        )
        if not has_nearby_date:
            return i
    return min_idx


def _lbl_find_other_start(right_paras: list[ParaModel], min_idx: int) -> int:
    """First index >= min_idx of an 'other'-type section (affiliations, etc.).

    Detects either a bold entry header, or a paragraph that precedes a role_meta
    within the next four non-empty paragraphs.
    """
    for i in range(min_idx, len(right_paras)):
        pm = right_paras[i]
        if not pm.text.strip():
            continue
        if pm.semantic == "role_meta":
            # Role-meta line — backtrack to the header that precedes it
            for j in range(max(min_idx, i - 4), i):
                if right_paras[j].text.strip() and right_paras[j].semantic != "role_meta":
                    return j
            return i
        if pm.style.bold:
            return i
    return min_idx


def _lbl_injection_index(
    right_paras: list[ParaModel], heading: ParaModel, min_idx: int
) -> int:
    """Return the index in right_paras where heading should be injected (>= min_idx)."""
    sem = _classify_section(heading.text)
    if sem == "experience":
        return _lbl_find_experience_start(right_paras, min_idx)
    if sem == "education":
        return _lbl_find_education_start(right_paras, min_idx)
    if sem == "skills":
        return _lbl_find_skills_start(right_paras, min_idx)
    # "summary" is handled as index 0 by the caller; all other types use other-start
    return _lbl_find_other_start(right_paras, min_idx)


def _apply_label_column_fix(
    all_paras: list[ParaModel], body
) -> tuple[list[ParaModel], bool]:
    """Detect and fix label-column layout; return (new_all_paras, was_fixed).

    In a 2-column newspaper-layout DOCX where column 0 is a narrow label rail
    containing only section headings, all headings appear before all content in the
    linear XML order.  This causes the parser to build empty sections and then dump
    every resume paragraph into the last section.

    When detected, the function reorders all_paras so each heading is positioned
    immediately before the right-column content block it belongs to.  The left-column
    empty spacing paragraphs are dropped from all_paras (they are redundant once the
    headings are relocated).

    body_items is NOT modified — it is used by the DOCX renderer, not by section
    grouping.
    """
    if not _detect_label_column_layout(body):
        return all_paras, False

    split_idx = _find_label_column_split(all_paras)
    if split_idx is None:
        return all_paras, False

    # Section headings from the left column (in document order, empties stripped).
    # Only include recognised section names to exclude candidate job-title headings
    # in the header area (e.g. "SOFTWARE ENGINEER" styled as Heading N).
    left_headings = [
        pm for pm in all_paras[:split_idx]
        if pm.semantic == "section_heading"
        and pm.text.strip().lower() in _ALL_HEADING_NAMES
    ]
    if len(left_headings) < 2:
        return all_paras, False

    right_col = all_paras[split_idx:]

    # Everything before the first recognised section heading (name, contact, etc.).
    # Use the same filter as left_headings so that candidate job-title headings in
    # the header area are preserved in pre_header rather than discarded.
    first_heading_idx = next(
        i for i, pm in enumerate(all_paras)
        if pm.semantic == "section_heading" and pm.text.strip().lower() in _ALL_HEADING_NAMES
    )
    pre_header = all_paras[:first_heading_idx]

    # Compute injection indices: where in right_col each heading should be placed
    injection_indices: list[int] = []
    min_idx = 0
    for k, heading in enumerate(left_headings):
        idx = 0 if k == 0 else _lbl_injection_index(right_col, heading, min_idx)
        injection_indices.append(idx)
        min_idx = idx

    # Bail out if indices are not non-decreasing (semantic detection went wrong)
    if any(b < a for a, b in zip(injection_indices, injection_indices[1:])):
        return all_paras, False

    # Build new all_paras: pre_header + interleaved headings + right-column content
    new_paras: list[ParaModel] = list(pre_header)
    prev_idx = 0
    for heading, inject_at in zip(left_headings, injection_indices):
        new_paras.extend(right_col[prev_idx:inject_at])
        new_paras.append(heading)
        prev_idx = inject_at
    new_paras.extend(right_col[prev_idx:])

    return new_paras, True


# ---------------------------------------------------------------------------
# Newspaper-column / table multi-column layout fix (band-aware)
# ---------------------------------------------------------------------------
# Some DOCX templates use Word's newspaper-column feature (w:sectPr/w:cols) to
# create a two- or three-column visual layout where left-column and right-column
# content is interleaved in the flat paragraph stream.
#
# This fix applies a band-aware reordering that produces two streams:
#   LEFT  stream: col-0 content + band-merged col-1 for 3-col wide-left sections
#   RIGHT stream: col-1 (2-col), col-2 (3-col wide-left), tab-split right sides
#
# The document header section (initial Word section with name/contact) is kept
# in its original document order to prevent contact info from polluting sections.
#
# body_items (used by the renderer) is NOT modified.

_COL_BREAK_TAG = f"{{{_W}}}br"
_KNOWN_SECTION_NAMES_LOWER: frozenset[str] = frozenset(
    t.lower() for t in _ALL_HEADING_NAMES
)

# Fix only triggers when the dual-heading tab split contains a skills-column name.
# This prevents false positives on templates where Experience|Education is split
# correctly without any cross-column contamination.
_SKILLS_COLUMN_NAMES: frozenset[str] = frozenset({
    "skills", "technical skills", "core competencies", "competencies",
    "key skills", "technologies", "tech stack", "expertise",
    "areas of expertise", "technical expertise", "relevant skills",
})


def _has_column_break(p_elem) -> bool:
    """Return True when a w:p element contains a <w:br type='column'>."""
    for br in p_elem.iter(_COL_BREAK_TAG):
        if br.get(f"{{{_W}}}type") == "column":
            return True
    return False


def _text_after_column_break(p_elem) -> str:
    """Return the text that follows the first column break in p_elem, or ''."""
    found_break = False
    parts: list[str] = []
    for elem in p_elem.iter():
        if elem.tag == _COL_BREAK_TAG and elem.get(f"{{{_W}}}type") == "column":
            found_break = True
            continue
        if found_break:
            if elem.tag == f"{{{_W}}}t":
                parts.append(elem.text or "")
            elif elem.tag == _COL_BREAK_TAG:
                break
    return "".join(parts)


def _text_before_column_break(p_elem) -> str:
    """Return the text that precedes the first column break in p_elem."""
    parts: list[str] = []
    for elem in p_elem.iter():
        if elem.tag == _COL_BREAK_TAG and elem.get(f"{{{_W}}}type") == "column":
            break
        if elem.tag == f"{{{_W}}}t":
            parts.append(elem.text or "")
    return "".join(parts)


def _tab_split_texts(p_elem) -> tuple[str, str] | None:
    """If p_elem uses a run-level <w:tab/> to place two heading names side-by-side,
    return (left_text, right_text).

    Only returns a split when:
    - The paragraph does NOT contain a column break.
    - pPr has explicit tab stops defined.
    - A <w:tab/> run character (direct child of a <w:r>, NOT inside <w:tabs>) exists.
    - Both sides have non-empty text.
    """
    if _has_column_break(p_elem):
        return None
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        return None
    if pPr.find(f"{{{_W}}}tabs") is None:
        return None

    left_parts: list[str] = []
    right_parts: list[str] = []
    in_right = False

    for child in p_elem:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local != "r":
            continue
        for sub in child:
            sub_local = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
            if sub_local == "tab":
                in_right = True
            elif sub_local == "t":
                text = sub.text or ""
                if not in_right:
                    left_parts.append(text)
                else:
                    right_parts.append(text)

    left = "".join(left_parts).strip()
    right = "".join(right_parts).strip()
    if not left or not right:
        return None
    return left, right


def _detect_newspaper_multicolumn(body) -> bool:
    """Return True when body has mid-document sectPr with 2+ columns AND column-break paras."""
    has_midoc_multicol = False
    for p_elem in body.findall(f".//{{{_W}}}p"):
        pPr = p_elem.find(f"{{{_W}}}pPr")
        if pPr is None:
            continue
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is None:
            continue
        cols_elem = sectPr.find(f"{{{_W}}}cols")
        if cols_elem is None:
            continue
        if len(cols_elem.findall(f"{{{_W}}}col")) >= 2:
            has_midoc_multicol = True
            break

    if not has_midoc_multicol:
        return False

    return any(_has_column_break(p) for p in body.findall(f".//{{{_W}}}p"))


def _parse_word_section_col_counts(body) -> list[tuple[int, int]]:
    """Return list of (end_body_child_idx, col_count) for each Word section."""
    body_children = list(body)
    result: list[tuple[int, int]] = []
    for i, child in enumerate(body_children):
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local != "p":
            continue
        pPr = child.find(f"{{{_W}}}pPr")
        if pPr is None:
            continue
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is None:
            continue
        cols_elem = sectPr.find(f"{{{_W}}}cols")
        col_elems = cols_elem.findall(f"{{{_W}}}col") if cols_elem is not None else []
        result.append((i, len(col_elems)))
    body_sectPr = body.find(f"{{{_W}}}sectPr")
    if body_sectPr is not None:
        cols_elem = body_sectPr.find(f"{{{_W}}}cols")
        col_elems = cols_elem.findall(f"{{{_W}}}col") if cols_elem is not None else []
        result.append((len(body_children) - 1, len(col_elems)))
    return result


def _parse_word_sections_full(body) -> list[dict]:
    """Return section info including col_widths for each Word section."""
    body_children = list(body)
    result: list[dict] = []

    def _widths_from_sectPr(sectPr) -> list[int]:
        cols_elem = sectPr.find(f"{{{_W}}}cols")
        col_elems = cols_elem.findall(f"{{{_W}}}col") if cols_elem is not None else []
        widths = []
        for c in col_elems:
            try:
                widths.append(int(c.get(f"{{{_W}}}w") or 0))
            except (ValueError, TypeError):
                widths.append(0)
        return widths

    for i, child in enumerate(body_children):
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local != "p":
            continue
        pPr = child.find(f"{{{_W}}}pPr")
        if pPr is None:
            continue
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is None:
            continue
        widths = _widths_from_sectPr(sectPr)
        result.append({"end_idx": i, "col_count": len(widths), "col_widths": widths})

    body_sectPr = body.find(f"{{{_W}}}sectPr")
    if body_sectPr is not None:
        widths = _widths_from_sectPr(body_sectPr)
        result.append({"end_idx": len(body_children) - 1, "col_count": len(widths), "col_widths": widths})

    return result


def _is_wide_left_narrow_right_3col(col_widths: list[int]) -> bool:
    """Return True when the first two columns are together >1.5× wider than the third."""
    if len(col_widths) != 3:
        return False
    left = col_widths[0] + col_widths[1]
    right = col_widths[2]
    return right > 0 and left / right > 1.5


def _split_col_at_empty_clusters(
    paras: list[ParaModel],
    threshold: int = 3,
) -> list[list[ParaModel]]:
    """Split paras into bands at runs of >= threshold consecutive empty paragraphs.

    Empty paragraphs that form the cluster separator are discarded (they are
    just whitespace separating layout bands and add no semantic content).
    Trailing empties are also discarded.
    """
    bands: list[list[ParaModel]] = [[]]
    pending_empties: list[ParaModel] = []

    for pm in paras:
        if not pm.text.strip():
            pending_empties.append(pm)
        else:
            if len(pending_empties) >= threshold and bands[-1]:
                bands.append([pm])
            else:
                bands[-1].extend(pending_empties)
                bands[-1].append(pm)
            pending_empties = []
    # trailing empties discarded
    return bands


def _band_merge_cols(
    col0: list[ParaModel],
    col1: list[ParaModel],
) -> list[ParaModel]:
    """Interleave col0 and col1 using band detection.

    col0 bands are defined by top-level section headings (Heading 1 or
    heuristic-heading paragraphs).  col1 bands are defined by clusters of
    3+ consecutive empty paragraphs, which visually separate layout rows.

    Each col0 band i is paired with col1 band i; extra bands from either
    side are appended at the end.
    """
    # Split col0 at top-level (Heading 1) section headings
    col0_bands: list[list[ParaModel]] = [[]]
    for pm in col0:
        level = _heading_level(pm)
        is_top = (
            pm.semantic == "section_heading"
            and (level is None or level <= 1)
            and bool(col0_bands[-1])
        )
        if is_top:
            col0_bands.append([pm])
        else:
            col0_bands[-1].append(pm)

    col1_bands = _split_col_at_empty_clusters(col1, threshold=3)

    result: list[ParaModel] = []
    n = max(len(col0_bands), len(col1_bands))
    for i in range(n):
        result.extend(col0_bands[i] if i < len(col0_bands) else [])
        result.extend(col1_bands[i] if i < len(col1_bands) else [])
    return result


def _simple_section_count(paras: list[ParaModel]) -> int:
    return sum(1 for p in paras if p.semantic == "section_heading")


def _count_experience_roles(paras: list[ParaModel]) -> int:
    return sum(1 for p in paras if p.semantic == "role_header")


def _apply_multicolumn_newspaper_fix(
    all_paras: list[ParaModel],
    body,
) -> tuple[list[ParaModel], bool, dict]:
    """Band-aware newspaper-column layout fix.

    Separates the document into a LEFT stream (left visual column) and a RIGHT
    stream (right visual column / skills sidebar) then combines them so section
    grouping sees uncontaminated left-column content before right-column content.

    For 3-column sections where col0+col1 >> col2 (wide-left narrow-right),
    col0 and col1 are interleaved using band detection (col0 bands at section
    headings, col1 bands at large empty clusters) so CERTIFICATION entries in
    col1 appear inside the CERTIFICATION section rather than being orphaned.

    The first Word section (name/contact header) is always kept in its original
    document order so phone/email/address never contaminate experience roles.

    Returns (new_all_paras, was_fixed, metadata_dict).
    body_items is NOT modified.
    """
    if not _detect_newspaper_multicolumn(body):
        return all_paras, False, {}

    body_children = list(body)

    # Skills-column guard: only trigger when a tab-split heading has a skills-type
    # section on one side — prevents false positives on Experience|Education splits.
    has_skills_col_split = False
    for child in body_children:
        local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if local != "p":
            continue
        res = _tab_split_texts(child)
        if res is None:
            continue
        lt, rt = res
        if (
            lt.lower() in _KNOWN_SECTION_NAMES_LOWER
            and rt.lower() in _KNOWN_SECTION_NAMES_LOWER
            and (lt.lower() in _SKILLS_COLUMN_NAMES or rt.lower() in _SKILLS_COLUMN_NAMES)
        ):
            has_skills_col_split = True
            break
    if not has_skills_col_split:
        return all_paras, False, {}

    # Only handles documents with no tables (p_child_indices count must match all_paras)
    p_child_indices: list[int] = [
        i for i, c in enumerate(body_children)
        if (c.tag.split("}")[-1] if "}" in c.tag else c.tag) == "p"
    ]
    if len(p_child_indices) != len(all_paras):
        return all_paras, False, {}

    section_infos = _parse_word_sections_full(body)
    if not section_infos:
        return all_paras, False, {}

    def _sec_for(bci: int) -> dict:
        for si in section_infos:
            if bci <= si["end_idx"]:
                return si
        return section_infos[-1]

    # The first Word section (section_infos[0]) is the document header (name/contact).
    # Keep all its paragraphs in their original document order.
    first_sec_end = section_infos[0]["end_idx"]
    header_end_para_idx = sum(1 for ci in p_child_indices if ci <= first_sec_end)

    # Per-section, per-column paragraph lists
    # key: (sec_idx, col_idx) → list[ParaModel]
    per_sec_col: dict[tuple[int, int], list[ParaModel]] = {}
    current_col_by_sec: dict[int, int] = {}

    for para_idx, bci in enumerate(p_child_indices):
        if bci <= first_sec_end:
            continue  # leave header section untouched

        si = _sec_for(bci)
        sec_idx = section_infos.index(si)
        col_count = si["col_count"]

        if sec_idx not in current_col_by_sec:
            current_col_by_sec[sec_idx] = 0

        p_elem = body_children[bci]
        pm = all_paras[para_idx]

        # Tab-split: dual-heading paragraph → left side to col0, right to col1
        split = _tab_split_texts(p_elem)
        if split is not None:
            lt, rt = split
            if lt.lower() in _KNOWN_SECTION_NAMES_LOWER and rt.lower() in _KNOWN_SECTION_NAMES_LOWER:
                lpm = pm.with_text(lt); lpm.semantic = "section_heading"
                rpm = pm.with_text(rt); rpm.semantic = "section_heading"
                per_sec_col.setdefault((sec_idx, 0), []).append(lpm)
                per_sec_col.setdefault((sec_idx, 1), []).append(rpm)
                continue

        # Column break: advance column counter, strip leading \n from text
        if col_count >= 2 and _has_column_break(p_elem):
            before = _text_before_column_break(p_elem).strip()
            if not before:
                current_col_by_sec[sec_idx] += 1

        col = current_col_by_sec[sec_idx] if col_count >= 2 else 0

        if col_count >= 2 and _has_column_break(p_elem):
            after = _text_after_column_break(p_elem).strip()
            if after and after != pm.text.strip():
                pm = pm.with_text(after)
                pm.semantic = _infer_semantic(pm)

        per_sec_col.setdefault((sec_idx, col), []).append(pm)

    # Build left_stream and right_stream across all body Word sections
    left_stream: list[ParaModel] = []
    right_stream: list[ParaModel] = []

    for sec_idx, si in enumerate(section_infos):
        if si["end_idx"] <= first_sec_end:
            continue  # header section, skip

        col_count = si["col_count"]
        widths = si["col_widths"]

        def _col(c: int) -> list[ParaModel]:
            return per_sec_col.get((sec_idx, c), [])

        if col_count < 2:
            # Single-column section: non-split goes left; tab-split right side goes right
            left_stream.extend(_col(0))
            right_stream.extend(_col(1))  # only populated by tab splits
        elif col_count == 2:
            left_stream.extend(_col(0))
            right_stream.extend(_col(1))
        elif col_count == 3 and _is_wide_left_narrow_right_3col(widths):
            # col0+col1 form the left visual column (band-aware merge);
            # col2 is the right visual column (skills sidebar).
            merged = _band_merge_cols(_col(0), _col(1))
            left_stream.extend(merged)
            right_stream.extend(_col(2))
        else:
            # Standard multi-col: col0 left, rest right
            left_stream.extend(_col(0))
            for c in range(1, col_count):
                right_stream.extend(_col(c))

    # Build candidate: header (original order) + left + right
    header_list = all_paras[:header_end_para_idx]
    candidate = header_list + left_stream + right_stream

    if not left_stream and not right_stream:
        return all_paras, False, {}

    # Quality validation: candidate must not lose section headings or experience roles
    orig_sec = _simple_section_count(all_paras)
    cand_sec = _simple_section_count(candidate)
    orig_roles = _count_experience_roles(all_paras)
    cand_roles = _count_experience_roles(candidate)

    if cand_sec < orig_sec or cand_roles < orig_roles:
        return all_paras, False, {
            "aborted": True,
            "reason": (
                f"candidate lost sections ({orig_sec}->{cand_sec}) "
                f"or roles ({orig_roles}->{cand_roles})"
            ),
        }

    headings_per_col: dict[int, list[str]] = {
        0: [p.text.strip() for p in left_stream if p.semantic == "section_heading"],
        1: [p.text.strip() for p in right_stream if p.semantic == "section_heading"],
    }
    paras_per_col = {0: len(left_stream), 1: len(right_stream)}

    return candidate, True, {
        "aborted": False,
        "col_count": 2,
        "headings_per_col": headings_per_col,
        "paras_per_col": paras_per_col,
    }


def _finalise(section: ResumeSection) -> None:
    if section.semantic_type == "experience":
        # Pre-pass: promote plain-paragraph date lines with placeholder years
        # (e.g. "January 20xx - Current") to role_meta so that
        # _relabel_implicit_role_headers and _group_roles can detect role
        # boundaries in templates that never use real 4-digit years.
        # Guard: skip when the section already has pipe-format role_header
        # paragraphs — those templates do not need this heuristic and the
        # pre-pass would create spurious Pattern-B roles before each real header.
        has_role_headers = any(p.semantic == "role_header" for p in section.body_paras)
        if not has_role_headers:
            for p in section.body_paras:
                if (
                    p.semantic == "paragraph"
                    and _DATE_PLACEHOLDER_RE.search(p.text)
                    and len(p.text.strip()) <= 80
                    and "|" not in p.text
                ):
                    p.semantic = "role_meta"
        _relabel_implicit_role_headers(section.body_paras)
        section.roles = _group_roles(section.body_paras)

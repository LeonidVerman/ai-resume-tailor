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
                # (role started via role_header), so role_meta here is a false positive.
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
                        current.semantic_type in {"education", "certifications"}
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
    )
    from tailor.compiler.models import assign_stable_ids
    assign_stable_ids(doc)
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

"""
src/tailor/compiler/classification_normalizer.py

Pre-classification input normalization applied to ClassificationInput before
the LLM classifier is called, and post-classification synthetic para_id
resolution.

Normalization pipeline (applied in order per section):
  1. Remove section_heading paragraphs — already captured in raw_title.
     Headings used as role headers (pattern-C layouts) are preserved.
  2. Clear roles[] for non-experience sections — prevents the LLM from
     treating education / skills / projects role-like structures as experience.
  3. Split compound paragraphs — role_meta paragraphs whose text contains
     embedded content are split into individual synthetic units:
       • Newline split  ("compound_meta_nl")  — para text contains \\n;
         each line is classified (role_meta / paragraph / bullet) and
         emitted as a separate synthetic paragraph.
       • Pipe split  ("compound_meta_pipe")  — para text contains " | "
         but no newlines; each token becomes a synthetic role_meta.
  4. Split over-merged roles — detects roles where bullet_para_ids contains
     paragraphs with parser_semantic == "role_meta" (a new job boundary
     signal) and splits the role into N sub-roles at each such boundary.
     Preceding company-name paragraphs are moved to the new role's meta.

Synthetic para IDs:
    {source_para_id}__nl_{index}    — newline split
    {source_para_id}__pipe_{index}  — pipe split

Sidecar map: synthetic_para_id -> source_para_id
Used by resolve_synthetic_para_ids() after classification.

Post-classification:
    resolve_synthetic_para_ids(classification, sidecar) — replaces synthetic
    para_ids in preserved-type blocks with source para_ids.  Duplicate
    resolutions for the same source (first wins) are deduplicated.
    Rewriteable blocks (role_achievement_bullet, role_responsibility_bullet)
    keep their synthetic para_ids.

Normalization report (returned as third element):
    {
      "structure_confidence":          "high" | "medium" | "low",
      "headings_removed":              [{section_id, para_id, text_preview}],
      "roles_cleared":                 [{section_id, raw_title, role_count}],
      "compound_paras_split":          [{source_para_id, text_preview, split_count, split_kind}],
      "overmerged_roles_split":        [{section_id, original_role_id, split_into, split_count}],
      "flat_experience_roles_rebuilt": [{section_id, para_count, roles_rebuilt, role_ids}],
      "semantic_hints_added":          int,   # total paragraphs that received a hint
    }
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tailor.compiler.classification_models import (
        ClassificationInput,
        ClassificationSectionInput,
        ClassificationParaInput,
        ClassificationRoleInput,
    )

# ---------------------------------------------------------------------------
# Non-experience section title keywords (lowercased)
# ---------------------------------------------------------------------------

_NON_EXP_KEYWORDS: frozenset[str] = frozenset({
    "summary", "professional summary", "executive summary", "career summary",
    "objective", "career objective", "professional objective",
    "profile", "about me", "about", "personal statement",
    "skills", "technical skills", "core skills", "key skills",
    "skills & expertise", "skills and expertise",
    "competencies", "core competencies", "key competencies",
    "education", "academic", "academics", "academic background",
    "academic qualifications", "educational background", "education & training",
    "certifications", "certificates", "professional certifications",
    "licenses", "awards", "honors", "honours", "achievements", "accomplishments",
    "languages", "language skills", "language proficiencies",
    "references", "volunteer", "volunteering", "volunteer experience",
    "interests", "hobbies", "activities", "extracurricular",
    "affiliations", "memberships",
    "publications", "research", "presentations", "conferences",
    "projects", "project",
    "additional", "additional information", "additional skills",
    "other", "other information", "miscellaneous",
})


def _is_non_experience_title(raw_title: str) -> bool:
    t = raw_title.strip().lower()
    if t in _NON_EXP_KEYWORDS:
        return True
    for kw in _NON_EXP_KEYWORDS:
        if t.startswith(kw):
            return True
    return False


# ---------------------------------------------------------------------------
# Experience section title keywords (for flat-role reconstruction)
# ---------------------------------------------------------------------------

_EXPERIENCE_TITLE_KEYWORDS: frozenset[str] = frozenset({
    "experience", "work experience", "professional experience",
    "employment", "work history", "career history",
    "employment history", "professional background",
    "career", "relevant experience", "relevant work experience",
    "professional experience & achievements",
})


def _is_experience_title(raw_title: str) -> bool:
    t = raw_title.strip().lower()
    if t in _EXPERIENCE_TITLE_KEYWORDS:
        return True
    for kw in _EXPERIENCE_TITLE_KEYWORDS:
        if t.startswith(kw):
            return True
    return False


# ---------------------------------------------------------------------------
# Role-header heuristic for flat experience reconstruction
# ---------------------------------------------------------------------------

_ROLE_TITLE_KEYWORDS_RE = re.compile(
    r'\b(?:Engineer|Developer|Lead|Manager|Director|Analyst|Architect|'
    r'Scientist|Designer|Consultant|Specialist|Administrator|Coordinator|'
    r'Officer|President|VP|CTO|CEO|CIO|Head|Principal|Staff|'
    r'Intern|Associate|Assistant|Programmer|Technician|Writer|Researcher|'
    r'Founder|Co-Founder|Partner|Strategist)\b',
    re.IGNORECASE,
)


def _is_role_header_heuristic(para: "ClassificationParaInput") -> bool:
    """True if the paragraph text looks like a job-title / role-header line.

    Strips any embedded "Highlights: ..." suffix before applying rules so that
    paragraphs that merge a title with highlights text are still detected.
    """
    t = para.text.strip()
    hl_idx = t.lower().find("highlights:")
    if hl_idx > 0:
        t = t[:hl_idx].strip()
    if not t or len(t) > 150:
        return False
    if t.lower().startswith(("f ", "highlights:", "•", "-", "*")):
        return False
    return "," in t and bool(_ROLE_TITLE_KEYWORDS_RE.search(t))


# ---------------------------------------------------------------------------
# Flat-experience role reconstructor
# ---------------------------------------------------------------------------

def _rebuild_flat_experience_roles(
    paras: "list[ClassificationParaInput]",
    sec_id: str,
    report_list: list,
) -> "list[ClassificationRoleInput]":
    """Group flat experience paragraphs into roles.

    A role header is any paragraph with parser_semantic == 'role_header' OR
    one that passes _is_role_header_heuristic().  Each detected header starts
    a new role; immediately following role_meta paras become meta_para_ids
    and everything else (non-empty) becomes bullet_para_ids.

    Returns [] when no role headers are detectable (caller keeps roles=[]).
    """
    from tailor.compiler.classification_models import ClassificationRoleInput as CRI

    header_indices = [
        i for i, p in enumerate(paras)
        if p.parser_semantic == "role_header" or _is_role_header_heuristic(p)
    ]
    if not header_indices:
        return []

    roles: list["ClassificationRoleInput"] = []
    for k, start in enumerate(header_indices):
        end = header_indices[k + 1] if k + 1 < len(header_indices) else len(paras)
        role_paras = paras[start:end]

        header_ids = [role_paras[0].para_id]
        meta_ids: list[str] = []
        bullet_ids: list[str] = []

        in_meta = True
        for p in role_paras[1:]:
            if p.parser_semantic == "empty":
                continue
            if in_meta and p.parser_semantic == "role_meta":
                meta_ids.append(p.para_id)
            else:
                in_meta = False
                bullet_ids.append(p.para_id)

        roles.append(CRI(
            role_id=f"{sec_id}_rebuilt_{k + 1}",
            header_para_ids=header_ids,
            meta_para_ids=meta_ids,
            bullet_para_ids=bullet_ids,
        ))

    report_list.append({
        "section_id": sec_id,
        "para_count": len(paras),
        "roles_rebuilt": len(roles),
        "role_ids": [r.role_id for r in roles],
    })
    return roles


# ---------------------------------------------------------------------------
# Compound paragraph line classifier (for newline splits)
# ---------------------------------------------------------------------------

_DATE_TOKEN_RE = re.compile(
    r'\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?'
    r'|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
    r'\s*\d{4}|\b\d{4}\b',
    re.IGNORECASE,
)
_YEAR_RANGE_PARENS_RE = re.compile(r'\(\d{4}[^\)]{1,30}\d{4}\)')
_PIPE_RE = re.compile(r'\s*\|\s*')


def _classify_nl_line(line: str) -> str:
    """Classify a single non-empty line extracted from a newline-split compound paragraph.

    Returns one of: "role_meta" | "paragraph" | "bullet" | "empty"
    """
    s = line.strip()
    if not s:
        return "empty"

    # Italic role description / intro  (*text*)
    if s.startswith("*") and s.endswith("*") and len(s) >= 3:
        return "paragraph"

    # Tech stack header
    if re.match(r"Tech\s+Stack\s*:", s, re.IGNORECASE):
        return "paragraph"

    # Company ; Date ; Location metadata line  (semicolons + recognisable date token)
    if ";" in s and _DATE_TOKEN_RE.search(s):
        return "role_meta"

    # Section header embedded within body text (short line with year range in parens)
    # e.g. "Backend & Full-Stack Development (2007–2019)"
    if len(s) < 100 and _YEAR_RANGE_PARENS_RE.search(s):
        return "paragraph"

    return "bullet"


# ---------------------------------------------------------------------------
# Compound paragraph splitters
# ---------------------------------------------------------------------------

def _split_compound_nl(
    para: "ClassificationParaInput",
    sidecar: "dict[str, str]",
    report_list: list,
) -> "tuple[list[ClassificationParaInput], list[str], list[str]]":
    """Newline-split a compound role_meta paragraph.

    Returns (new_paras, meta_synth_ids, body_synth_ids).
    meta_synth_ids  → should replace the source in role.meta_para_ids
    body_synth_ids  → should be appended to role.bullet_para_ids
    """
    from tailor.compiler.classification_models import ClassificationParaInput as CPI

    lines = [ln for ln in para.text.split("\n") if ln.strip()]
    if len(lines) <= 1:
        return [para], [para.para_id], []

    new_paras: list[CPI] = []
    meta_ids: list[str] = []
    body_ids: list[str] = []

    for idx, line in enumerate(lines):
        sem = _classify_nl_line(line)
        if sem == "empty":
            continue
        synth_id = f"{para.para_id}__nl_{idx}"
        sidecar[synth_id] = para.para_id
        synth = CPI(
            para_id=synth_id,
            text=line.strip(),
            parser_semantic=sem,
            source_para_id=para.para_id,
            synthetic=True,
            split_kind="compound_meta_nl",
        )
        new_paras.append(synth)
        if sem == "role_meta":
            meta_ids.append(synth_id)
        else:
            body_ids.append(synth_id)

    if not new_paras:
        return [para], [para.para_id], []

    report_list.append({
        "source_para_id": para.para_id,
        "text_preview": para.text[:80].replace("\n", " / "),
        "split_count": len(new_paras),
        "split_kind": "compound_meta_nl",
    })
    return new_paras, meta_ids, body_ids


def _split_compound_pipe(
    para: "ClassificationParaInput",
    sidecar: "dict[str, str]",
    report_list: list,
) -> "tuple[list[ClassificationParaInput], list[str], list[str]]":
    """Pipe-split a compound role_meta paragraph (no newlines present).

    Returns (new_paras, meta_synth_ids, []).
    All pipe-split tokens are role_meta.
    """
    from tailor.compiler.classification_models import ClassificationParaInput as CPI

    if " | " not in para.text:
        return [para], [para.para_id], []

    parts = [p.strip() for p in _PIPE_RE.split(para.text) if p.strip()]
    if len(parts) < 2:
        return [para], [para.para_id], []

    new_paras: list[CPI] = []
    meta_ids: list[str] = []

    for idx, part in enumerate(parts):
        synth_id = f"{para.para_id}__pipe_{idx}"
        sidecar[synth_id] = para.para_id
        synth = CPI(
            para_id=synth_id,
            text=part,
            parser_semantic="role_meta",
            source_para_id=para.para_id,
            synthetic=True,
            split_kind="compound_meta_pipe",
        )
        new_paras.append(synth)
        meta_ids.append(synth_id)

    report_list.append({
        "source_para_id": para.para_id,
        "text_preview": para.text[:80],
        "split_count": len(new_paras),
        "split_kind": "compound_meta_pipe",
    })
    return new_paras, meta_ids, []


def _compound_split_kind(para: "ClassificationParaInput") -> str:
    """Return 'nl', 'pipe', or '' indicating which compound split to apply."""
    if para.parser_semantic != "role_meta":
        return ""
    if "\n" in para.text:
        return "nl"
    if " | " in para.text:
        return "pipe"
    return ""


# ---------------------------------------------------------------------------
# Over-merged role splitter
# ---------------------------------------------------------------------------

def _looks_like_company_line(para: "ClassificationParaInput") -> bool:
    """True if para looks like a standalone company/location name (not a tech line)."""
    if para.parser_semantic != "paragraph":
        return False
    # Tech-stack and tool lines typically contain ":"
    return ":" not in para.text


def _split_overmerged_role(
    role: "ClassificationRoleInput",
    para_map: "dict[str, ClassificationParaInput]",
    section_id: str,
    report_list: list,
) -> "list[ClassificationRoleInput]":
    """Split an over-merged role at role_meta boundaries within bullet_para_ids.

    A role is over-merged when bullet_para_ids contains paragraphs whose
    parser_semantic is "role_meta" — these signal the start of a new job.

    Returns the original role (trimmed) plus any split roles.  The first
    result keeps the original role_id; subsequent splits get __split_N suffix.
    """
    from tailor.compiler.classification_models import ClassificationRoleInput as CRI

    has_meta_in_bullets = any(
        para_map.get(pid) is not None and para_map[pid].parser_semantic == "role_meta"
        for pid in role.bullet_para_ids
    )
    if not has_meta_in_bullets:
        return [role]

    result: list[CRI] = []
    cur_header: list[str] = list(role.header_para_ids)
    cur_meta: list[str] = list(role.meta_para_ids)
    cur_bullets: list[str] = []
    split_idx = 0

    for pid in role.bullet_para_ids:
        para = para_map.get(pid)
        if para is not None and para.parser_semantic == "role_meta":
            # Collect immediately preceding company-name paragraph(s) from current bullets.
            # We look back through up to 2 consecutive paragraph-typed paras that lack ":"
            # (company/location lines don't contain colons; tech-stack lines do).
            new_role_meta: list[str] = []
            temp_bullets = list(cur_bullets)
            while temp_bullets and len(new_role_meta) < 2:
                last = temp_bullets[-1]
                lp = para_map.get(last)
                if lp is not None and _looks_like_company_line(lp):
                    new_role_meta.insert(0, temp_bullets.pop())
                else:
                    break

            # Save current role
            role_id = role.role_id if split_idx == 0 else f"{role.role_id}__split_{split_idx}"
            result.append(CRI(
                role_id=role_id,
                header_para_ids=list(cur_header),
                meta_para_ids=list(cur_meta),
                bullet_para_ids=list(temp_bullets),
            ))
            split_idx += 1

            # New role: the role_meta para becomes the header; preceding para(s) = meta
            cur_header = [pid]
            cur_meta = new_role_meta
            cur_bullets = []
        else:
            cur_bullets.append(pid)

    # Flush last accumulated role
    if cur_header or cur_meta or cur_bullets:
        role_id = role.role_id if split_idx == 0 else f"{role.role_id}__split_{split_idx}"
        result.append(CRI(
            role_id=role_id,
            header_para_ids=list(cur_header),
            meta_para_ids=list(cur_meta),
            bullet_para_ids=list(cur_bullets),
        ))

    if not result:
        return [role]

    if len(result) > 1:
        report_list.append({
            "section_id": section_id,
            "original_role_id": role.role_id,
            "split_into": [r.role_id for r in result],
            "split_count": len(result),
        })

    return result


# ---------------------------------------------------------------------------
# Semantic hint detection
# ---------------------------------------------------------------------------

_TECH_STACK_RE = re.compile(
    r'^(?:Tech(?:nology|nologies)?\s+Stack|Key\s+Tech(?:nologies)?|'
    r'Technologies|Tools|Infrastructure|Dev(?:Ops|Tools)|Stack|Frameworks?|'
    r'Languages?|Platforms?)\s*:',
    re.IGNORECASE,
)
_HIGHLIGHT_RE = re.compile(r'^Highlights?\s*:', re.IGNORECASE)
_STRONG_BULLET_GLYPHS: frozenset[str] = frozenset('•◦▪▸●►')
# PDF-converted fake bullet: "f " followed by an uppercase letter
_PDF_FAKE_BULLET_RE = re.compile(r'^f [A-Z]')


def _detect_structure_confidence(ci: "ClassificationInput") -> str:
    """Estimate structure reliability by counting PDF-conversion artifacts.

    A high ratio of paragraphs starting with the fake-bullet pattern "f <Uppercase>"
    (a common PDF→DOCX artifact where the bullet glyph becomes the letter "f")
    signals a low-trust converted document.

    Returns "low" | "medium" | "high".
    """
    f_count = 0
    total = 0
    for sec in ci.sections:
        for para in sec.paragraphs:
            if para.parser_semantic in ("empty", "section_heading"):
                continue
            total += 1
            if _PDF_FAKE_BULLET_RE.match(para.text.strip()):
                f_count += 1
    if total == 0:
        return "high"
    ratio = f_count / total
    if ratio > 0.05:
        return "low"
    if ratio > 0.01:
        return "medium"
    return "high"


def _classify_para_hint(
    para: "ClassificationParaInput",
    structure_confidence: str,
    is_rebuilt_role_header: bool = False,
) -> tuple[str, str]:
    """Return (semantic_hint, bullet_confidence) for a paragraph.

    Returns ("", "") when no hint is warranted.
    Hints are ADVISORY — the LLM decides final semantics.
    """
    t = para.text.strip()
    sem = para.parser_semantic

    if sem in ("section_heading", "empty") or not t:
        return "", ""

    # Tech-stack line always wins regardless of parser_semantic
    if _TECH_STACK_RE.match(t):
        return "tech_stack_candidate", ""

    # Highlight / contextual summary line
    if _HIGHLIGHT_RE.match(t):
        return "highlight_candidate", ""

    # Italic role intro/description (*text* notation from some exporters)
    if t.startswith("*") and t.endswith("*") and len(t) >= 3:
        return "intro_candidate", ""

    # DOCX list bullet or text-inferred bullet (from compound splitting)
    if sem == "bullet":
        if para.synthetic:
            # parser_semantic came from _classify_nl_line() text analysis, not DOCX metadata
            confidence = "weak" if structure_confidence == "low" else "medium"
        else:
            # Came from real DOCX numId/ListParagraph metadata
            confidence = "medium" if structure_confidence == "low" else "strong"
        return "bullet_candidate", confidence

    # PDF-converted fake bullet ("f " + uppercase word)
    if _PDF_FAKE_BULLET_RE.match(t):
        return "bullet_candidate", "weak"

    # Visual bullet glyph
    if t[0] in _STRONG_BULLET_GLYPHS:
        return "bullet_candidate", "medium"

    # Dash-prefixed pseudo-bullet
    if t.startswith("- ") or t.startswith("– "):
        return "bullet_candidate", "medium"

    # Paragraph promoted to role header during flat-experience reconstruction
    if is_rebuilt_role_header and sem != "role_header":
        return "role_header_candidate", ""

    return "", ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def normalize_classification_input(
    ci: "ClassificationInput",
) -> "tuple[ClassificationInput, dict[str, str], dict]":
    """Apply pre-classification normalizations to a ClassificationInput.

    Returns
    -------
    (normalized_input, sidecar_map, normalization_report)

    sidecar_map  — synthetic_para_id -> source_para_id
    normalization_report — summary of all transformations applied
    """
    from tailor.compiler.classification_models import (
        ClassificationInput,
        ClassificationSectionInput,
        ClassificationParaInput,
        ClassificationRoleInput,
    )

    import dataclasses
    sidecar: dict[str, str] = {}
    structure_confidence = _detect_structure_confidence(ci)
    report: dict = {
        "structure_confidence": structure_confidence,
        "headings_removed": [],
        "roles_cleared": [],
        "compound_paras_split": [],
        "overmerged_roles_split": [],
        "flat_experience_roles_rebuilt": [],
        "semantic_hints_added": 0,
    }
    new_sections: list[ClassificationSectionInput] = []

    for sec in ci.sections:
        # Para ids that appear as role headers (must NOT be removed by step 1)
        role_header_ids: set[str] = {
            pid for role in sec.roles for pid in role.header_para_ids
        }

        # ── Step 1: remove section_heading paras not used as role headers ──
        paras_s1: list[ClassificationParaInput] = []
        for p in sec.paragraphs:
            if p.parser_semantic == "section_heading" and p.para_id not in role_header_ids:
                report["headings_removed"].append({
                    "section_id": sec.section_id,
                    "para_id": p.para_id,
                    "text_preview": p.text[:60],
                })
            else:
                paras_s1.append(p)

        # ── Step 2: clear roles for non-experience sections ────────────────
        if _is_non_experience_title(sec.raw_title) and sec.roles:
            report["roles_cleared"].append({
                "section_id": sec.section_id,
                "raw_title": sec.raw_title,
                "role_count": len(sec.roles),
            })
            roles_s2: list[ClassificationRoleInput] = []
        else:
            roles_s2 = list(sec.roles)

        # ── Step 3: split compound paragraphs in role meta_para_ids ───────
        role_meta_pids: set[str] = {
            pid for role in roles_s2 for pid in role.meta_para_ids
        }

        paras_s3: list[ClassificationParaInput] = []
        split_meta_map: dict[str, list[str]] = {}   # source_pid -> [meta_synth_ids]
        split_body_map: dict[str, list[str]] = {}   # source_pid -> [body_synth_ids]

        for p in paras_s1:
            if p.para_id in role_meta_pids:
                sk = _compound_split_kind(p)
                if sk == "nl":
                    new_ps, meta_ids, body_ids = _split_compound_nl(p, sidecar, report["compound_paras_split"])
                    paras_s3.extend(new_ps)
                    if meta_ids:
                        split_meta_map[p.para_id] = meta_ids
                    if body_ids:
                        split_body_map[p.para_id] = body_ids
                elif sk == "pipe":
                    new_ps, meta_ids, _ = _split_compound_pipe(p, sidecar, report["compound_paras_split"])
                    paras_s3.extend(new_ps)
                    if meta_ids:
                        split_meta_map[p.para_id] = meta_ids
                else:
                    paras_s3.append(p)
            else:
                paras_s3.append(p)

        # Update role meta/bullet para_ids with split results
        roles_s3: list[ClassificationRoleInput] = []
        for role in roles_s2:
            new_meta: list[str] = []
            extra_bullets: list[str] = []
            for pid in role.meta_para_ids:
                new_meta.extend(split_meta_map.get(pid, [pid]))
                extra_bullets.extend(split_body_map.get(pid, []))
            roles_s3.append(ClassificationRoleInput(
                role_id=role.role_id,
                header_para_ids=role.header_para_ids,
                meta_para_ids=new_meta,
                bullet_para_ids=list(role.bullet_para_ids) + extra_bullets,
            ))

        # ── Step 4: split over-merged roles ───────────────────────────────
        full_para_map: dict[str, ClassificationParaInput] = {
            p.para_id: p for p in paras_s3
        }
        roles_s4: list[ClassificationRoleInput] = []
        for role in roles_s3:
            roles_s4.extend(
                _split_overmerged_role(role, full_para_map, sec.section_id, report["overmerged_roles_split"])
            )

        # ── Step 5: rebuild flat experience roles ──────────────────────────
        if not roles_s4 and paras_s3 and _is_experience_title(sec.raw_title):
            rebuilt = _rebuild_flat_experience_roles(
                paras_s3, sec.section_id, report["flat_experience_roles_rebuilt"]
            )
            if rebuilt:
                roles_s4 = rebuilt

        # ── Step 6: populate semantic hints ───────────────────────────────
        rebuilt_header_pids: set[str] = {
            pid for role in roles_s4 for pid in role.header_para_ids
        }
        paras_final: list[ClassificationParaInput] = []
        for para in paras_s3:
            is_rh = para.para_id in rebuilt_header_pids
            hint, confidence = _classify_para_hint(para, structure_confidence, is_rh)
            if hint:
                para = dataclasses.replace(para, semantic_hint=hint, bullet_confidence=confidence)
                report["semantic_hints_added"] += 1
            paras_final.append(para)

        new_sections.append(ClassificationSectionInput(
            section_id=sec.section_id,
            raw_title=sec.raw_title,
            paragraphs=paras_final,
            roles=roles_s4,
        ))

    return ClassificationInput(
        document_id=ci.document_id,
        source_kind=ci.source_kind,
        structure_confidence=structure_confidence,
        sections=new_sections,
    ), sidecar, report


# ---------------------------------------------------------------------------
# Post-classification: resolve synthetic para_ids
# ---------------------------------------------------------------------------

# Semantic types that are preserved in the IR — synthetic para_ids for these
# get resolved back to the source para_id.
_PRESERVED_TYPES: frozenset[str] = frozenset({
    "role_meta",
    "role_header",
    "role_intro",
    "role_highlight",
    "role_project_label",
    "role_project_context",
    "role_tech_stack",
    "role_key_technologies",
    "role_tools",
    "role_nested_detail",
    "role_freeform_note",
    "education_entry",
    "skills_paragraph",
    "summary_paragraph",
    "other_paragraph",
    "project_entry",
    "additional_item",
    "section_heading",
})


def resolve_synthetic_para_ids(
    classification: dict,
    sidecar: "dict[str, str]",
) -> dict:
    """Replace synthetic para_ids in preserved-type blocks with source para_ids.

    When multiple synthetic children of the same source are all preserved,
    duplicates are removed (first occurrence wins).

    Rewriteable synthetic blocks (role_achievement_bullet,
    role_responsibility_bullet) keep their synthetic para_ids — they have no
    corresponding IR paragraph until a future IR-split feature.

    The input dict is not modified; a deep copy is returned.
    """
    if not sidecar:
        return classification

    import copy
    result = copy.deepcopy(classification)

    for sec in result.get("sections", []):
        if not isinstance(sec, dict):
            continue
        _resolve_block_list(sec.get("blocks", []), sidecar)
        for role in sec.get("roles", []):
            if not isinstance(role, dict):
                continue
            _resolve_block_list(role.get("header_blocks", []), sidecar)
            _resolve_block_list(role.get("meta_blocks", []), sidecar)
            _resolve_block_list(role.get("body_blocks", []), sidecar)

    return result


def _resolve_block_list(blocks: list, sidecar: "dict[str, str]") -> None:
    """In-place: resolve synthetic para_ids; deduplicate same-source blocks."""
    emitted_source_ids: set[str] = set()
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if not isinstance(block, dict):
            i += 1
            continue
        pid = block.get("para_id", "")
        source_id = sidecar.get(pid)
        if source_id is None:
            i += 1
            continue
        if block.get("semantic_type", "") not in _PRESERVED_TYPES:
            i += 1
            continue
        if source_id in emitted_source_ids:
            blocks.pop(i)   # duplicate — remove
        else:
            block["para_id"] = source_id
            emitted_source_ids.add(source_id)
            i += 1

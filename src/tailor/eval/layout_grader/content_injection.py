"""Content injection check — verifies that LLM output was applied to the template.

Two independent metrics:

  sim_llm      Jaccard(rendered LLM-target sections, LLM output text)
               Measures whether the LLM-rewritten content (Professional
               Summary, Experience, Skills) is faithfully present in the
               rendered output.  HIGH is good (≥ 0.60 → PASS).

  sim_template Jaccard(rendered non-target sections, template text)
               Measures whether sections the LLM does NOT rewrite
               (Education, Languages, References, header contact) were
               preserved verbatim from the template.  HIGH is good
               (1.0 = perfect carry-over).  LOW means those sections were
               unexpectedly modified.

Section classification:
  LLM-targeted:      headings containing "summary", "profile", "objective",
                     "experience", "employment", "skill", "competenc",
                     "technolog", "expertise" (case-insensitive substring).
  Template-preserved: header_paras + all other sections.

Scoring (contribution to composite):
  PERFECT (100): sim_llm = 1.0 (all LLM vocabulary reflected)
  NOTE (100):    sim_llm 0.80–1.0 (minor gap, no penalty)
  WARNING (85):  sim_llm 0.50–0.80 (partial injection, -15)
  FAIL (70):     sim_llm 0.25–0.50 (significant gap, -30)
  HARD FAIL:     sim_llm < 0.25 or lorem ipsum detected
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Common short words that appear in any resume regardless of content
_STOPWORDS: frozenset[str] = frozenset({
    "have", "from", "with", "that", "this", "will", "been", "were",
    "they", "them", "their", "your", "when", "what", "where", "which",
    "also", "more", "both", "into", "over", "each", "than", "then",
    "such", "well", "able", "work", "lead", "team", "role", "year",
    "company", "position", "resume", "section", "summary",
})

# Keywords that identify LLM-targeted sections (case-insensitive substring match)
_LLM_SECTION_KEYWORDS: tuple[str, ...] = (
    "summary", "profile", "objective", "about",
    "experience", "employment", "history",
    "skill", "competenc", "technolog", "expertise",
)


@dataclass
class ContentInjectionResult:
    score: float                          # 0–100
    hard_fail: bool
    sim_llm: float       # Jaccard(rendered LLM-target sections, LLM output)
    sim_template: float  # Jaccard(rendered non-target sections, template) — 1.0 = good
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    """Extract meaningful tokens: length >= 4, not a stopword.

    Pre-splits concatenated digit+letter strings (e.g. "2023Ginyard" →
    "2023 Ginyard") so company names embedded in year-prefixed headers
    produce the same tokens in both the IR and the LLM output.
    """
    # Insert a space between a run of digits and the following letter so
    # "2023Ginyard" tokenizes to "ginyard" the same way "Ginyard" does.
    text = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", text)
    words = re.findall(r"\b[a-z]{4,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _recall(rendered: set, reference: set) -> float:
    """Fraction of *reference* tokens that appear in *rendered*.

    Used for sim_llm: measures LLM-token coverage in the rendered output.
    Returns 1.0 when all reference tokens are present (regardless of extra
    tokens in rendered), 0.0 when none are present.
    """
    if not reference:
        return 0.0
    return len(rendered & reference) / len(reference)


def _is_llm_section(heading: str) -> bool:
    """Return True if this section is typically rewritten by the LLM."""
    h = heading.lower().strip()
    return any(kw in h for kw in _LLM_SECTION_KEYWORDS)


def _parse_llm_target_text(llm_text: str) -> str:
    """Extract only the target-section content from the raw LLM output string.

    The LLM output is free text that may include header contact info, Education,
    Languages, References, and other non-target sections alongside the rewritten
    Summary, Experience, and Skills content.  Including all of it in the Jaccard
    denominator dilutes sim_llm with tokens that should never appear in the
    rendered target sections.

    This function splits the output by candidate section headings (short
    alphabetic lines that match _LLM_SECTION_KEYWORDS) and collects content
    only from target sections.

    Heuristic heading detection:
      - 1–5 words, all alphabetic characters (plus /, space)
      - Does NOT look like a bullet, date, or contact line
    """
    lines = llm_text.splitlines()
    current_is_target = False
    parts: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Detect section heading: short, mostly alphabetic, no digits or @ or |
        words = stripped.split()
        _alpha_only = re.sub(r"[^a-zA-Z ]", "", stripped).strip()
        is_heading = (
            1 <= len(words) <= 5
            and len(_alpha_only) >= len(stripped) * 0.80  # ≥ 80% alphabetic
            and not any(ch in stripped for ch in "@|0123456789+")
        )
        if is_heading:
            current_is_target = _is_llm_section(stripped)
            # Do NOT append the heading itself: template headings replace LLM headings
            # in the rendered output, so including LLM heading words (e.g. "Technical"
            # from "Technical Skills" when the template uses "Skill") would add tokens
            # to llm_target_tokens that can never appear in the rendered IR, causing a
            # spurious gap in sim_llm even when content is fully injected.
        elif current_is_target:
            parts.append(stripped)

    return " ".join(parts)


def _ir_split_text(ir: dict) -> tuple[str, str]:
    """Split IR text into (llm_target_text, template_preserved_text).

    llm_target_text        — text from Summary / Experience / Skills sections
    template_preserved_text — text from header_paras + all other sections
                              (Education, Languages, References, etc.)
    """
    llm_parts: list[str] = []
    tmpl_parts: list[str] = []

    # header_paras = contact info → always template-preserved
    for p in ir.get("header_paras", []):
        t = p.get("text", "").strip()
        if t:
            tmpl_parts.append(t)

    for sec in ir.get("sections", []):
        heading_obj = sec.get("heading") or {}
        heading = heading_obj.get("text", "").strip() if heading_obj else ""

        # sec_summary_inserted is a synthesised LLM section regardless of heading
        sec_id = sec.get("section_id", "")
        is_llm = _is_llm_section(heading) or "summary_inserted" in sec_id

        target = llm_parts if is_llm else tmpl_parts

        if heading:
            target.append(heading)

        for p in sec.get("body_paras", []):
            t = p.get("text", "").strip()
            if t:
                target.append(t)

        for role in sec.get("roles", []):
            rh = role.get("header")
            if rh:
                t = rh.get("text", "").strip()
                if t:
                    target.append(t)
            for list_key in ("meta_lines", "bullets"):
                for p in role.get(list_key, []):
                    t = p.get("text", "").strip()
                    if t:
                        target.append(t)

    return " ".join(llm_parts), " ".join(tmpl_parts)


def _docx_to_text(docx_path: str) -> str:
    """Extract flat text from a DOCX template."""
    try:
        from docx import Document
        doc = Document(docx_path)
        return " ".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_content_injection(
    llm_text: str,
    ir_dict: dict,
    template_docx_path: str,
) -> ContentInjectionResult:
    """Check whether the LLM output was applied to the template.

    Parameters
    ----------
    llm_text:             Raw LLM resume text (from generation JSON).
    ir_dict:              Rendered IR (already loaded, from *_IR.json).
    template_docx_path:   Original template DOCX path.
    """
    llm_target_text, tmpl_preserved_text = _ir_split_text(ir_dict)
    template_text = _docx_to_text(template_docx_path)

    ir_full_lower = (llm_target_text + " " + tmpl_preserved_text).lower()
    evidence: list[str] = []
    hard_fail = False

    # ── Hard check: placeholder / lorem ipsum ────────────────────────────────
    if "lorem ipsum" in ir_full_lower or (
        "lorem" in ir_full_lower.split() and "ipsum" in ir_full_lower.split()
    ):
        return ContentInjectionResult(
            score=0.0,
            hard_fail=True,
            sim_llm=0.0,
            sim_template=0.0,
            evidence=["Lorem ipsum placeholder text detected in rendered output"],
        )

    has_placeholder = any(
        ph in ir_full_lower
        for ph in ("[firstname]", "[lastname]", "[name]", "firstname lastname")
    )
    if has_placeholder:
        evidence.append("Template placeholder text still present in rendered output")

    # ── sim_llm: rendered target sections vs LLM target sections ─────────────
    # Both sides are filtered to Summary/Experience/Skills only.
    # Jaccard(rendered_target, llm_target) answers: "of the unique vocabulary
    # that appeared in the LLM's rewritten sections, what fraction also appears
    # in the rendered output's corresponding sections?"  This avoids dilution
    # from non-target LLM content (Education, Languages, References, header).
    llm_target_portion = _parse_llm_target_text(llm_text)
    llm_target_tokens = _tokenize(llm_target_portion) if llm_target_portion else _tokenize(llm_text)

    if llm_target_text and llm_target_tokens:
        # Use recall (coverage) not Jaccard: measures what fraction of the LLM's
        # target-section vocabulary appears in the rendered output.  Extra tokens
        # in the rendered output don't reduce the score — only missing LLM tokens
        # do.  This matches the warning message ("X% of LLM vocabulary not reflected").
        sim_llm = _recall(_tokenize(llm_target_text), llm_target_tokens)
    else:
        sim_llm = 0.0

    # ── sim_template: non-target sections vs original template ────────────────
    # Measures whether Education / Languages / References were preserved verbatim.
    # 1.0 = perfect carry-over (desired). Low = unexpected modification.
    tmpl_preserved_tokens = _tokenize(tmpl_preserved_text)
    template_tokens = _tokenize(template_text)

    if tmpl_preserved_tokens and template_tokens:
        sim_template = _jaccard(tmpl_preserved_tokens, template_tokens)
    else:
        sim_template = 0.0

    # ── Scoring ───────────────────────────────────────────────────────────────
    score = 100.0

    if has_placeholder:
        score -= 25.0

    # Primary signal: LLM injection quality in target sections.
    # Ideal is sim_llm = 1.0 (all LLM target-section vocabulary reflected in rendered output).
    # Any deviation from 1.0 is reported as a warning. Score penalty applies only when
    # the shortfall is significant.
    if not llm_target_tokens:
        evidence.append("No Summary/Experience/Skills sections found to assess injection")
    elif sim_llm >= 1.0:
        pass  # Perfect — all target-section LLM content is reflected
    elif sim_llm >= 0.80:
        # Minor gap — note it but no score penalty
        evidence.append(
            f"Minor content gap in target sections: sim_llm={sim_llm:.2f} "
            f"(~{round((1.0 - sim_llm) * 100)}% of LLM target vocabulary not reflected)"
        )
    elif sim_llm >= 0.50:
        score -= 15.0
        evidence.append(
            f"Partial content injection in target sections: sim_llm={sim_llm:.2f}, "
            f"sim_template={sim_template:.2f}"
        )
    elif sim_llm >= 0.25:
        score -= 30.0
        evidence.append(
            f"Significant content injection gap: sim_llm={sim_llm:.2f}, "
            f"sim_template={sim_template:.2f}"
        )
    else:
        score -= 50.0
        evidence.append(
            f"Weak content injection in target sections: sim_llm={sim_llm:.2f}, "
            f"sim_template={sim_template:.2f}"
        )
        if sim_llm < 0.10 and llm_target_tokens:
            hard_fail = True
            evidence.append("LLM content appears absent from Summary/Experience/Skills")

    # Informational: template preservation for non-target sections.
    # sim_template close to 1.0 means Education/Languages/References were preserved.
    # Reported in evidence but does NOT affect the score.
    if tmpl_preserved_tokens and sim_template >= 0.70 and sim_template < 1.0:
        evidence.append(
            f"Non-target sections well-preserved from template: sim_template={sim_template:.2f}"
        )

    return ContentInjectionResult(
        score=max(0.0, min(100.0, score)),
        hard_fail=hard_fail,
        sim_llm=round(sim_llm, 3),
        sim_template=round(sim_template, 3),
        evidence=evidence,
    )

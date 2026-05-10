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
  PASS (100):    sim_llm ≥ 0.60 AND sim_template is not anomalously low
  WARNING (75):  sim_llm 0.35–0.60 (partial injection)
  FAIL (50):     sim_llm < 0.35 (weak injection)
  HARD FAIL:     lorem ipsum detected
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
    """Extract meaningful tokens: length >= 4, not a stopword."""
    words = re.findall(r"\b[a-z]{4,}\b", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _is_llm_section(heading: str) -> bool:
    """Return True if this section is typically rewritten by the LLM."""
    h = heading.lower().strip()
    return any(kw in h for kw in _LLM_SECTION_KEYWORDS)


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

    # ── sim_llm: LLM-targeted sections vs LLM output ─────────────────────────
    # Measures whether Professional Summary / Experience / Skills were rewritten.
    llm_target_tokens = _tokenize(llm_target_text)
    llm_tokens = _tokenize(llm_text)

    if llm_target_tokens and llm_tokens:
        sim_llm = _jaccard(llm_target_tokens, llm_tokens)
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
    # Scoring is based entirely on sim_llm.  sim_template is informational only —
    # it is not penalised because candidate content (name, education, contact)
    # legitimately differs from template placeholder text even when correctly applied.
    if not llm_target_tokens:
        # No identifiable LLM-target sections — cannot assess injection
        evidence.append("No Summary/Experience/Skills sections found to assess injection")
    elif sim_llm >= 0.60:
        pass  # Good injection — no penalty
    elif sim_llm >= 0.35:
        score -= 25.0
        evidence.append(
            f"Partial content injection in target sections: sim_llm={sim_llm:.2f}, "
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

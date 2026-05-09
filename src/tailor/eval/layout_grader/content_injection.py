"""Content injection check — verifies that LLM output was applied to the template.

Compares the rendered IR text against two baselines:
  - LLM output (from generation JSON) — rendered should be close to this
  - Template DOCX text — rendered should differ from this

Uses token-level Jaccard similarity on meaningful words (length >= 4,
stopwords excluded) to compute sim_llm and sim_template.

Scoring (contribution to composite):
  PASS (100):    rendered clearly closer to LLM than to template
  WARNING (75):  roughly equal similarity — partial rewrite
  FAIL (40):     rendered clearly closer to template — injection weak
  HARD FAIL:     lorem ipsum detected OR extreme template similarity
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


@dataclass
class ContentInjectionResult:
    score: float                          # 0–100
    hard_fail: bool
    sim_llm: float                        # Jaccard(rendered, LLM text)
    sim_template: float                   # Jaccard(rendered, template text)
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


def _ir_to_text(ir: dict) -> str:
    """Flatten all text paragraphs in an IR dict to a single string."""
    parts: list[str] = []
    for p in ir.get("header_paras", []):
        t = p.get("text", "").strip()
        if t:
            parts.append(t)
    for sec in ir.get("sections", []):
        h = sec.get("heading")
        if h:
            t = h.get("text", "").strip()
            if t:
                parts.append(t)
        for p in sec.get("body_paras", []):
            t = p.get("text", "").strip()
            if t:
                parts.append(t)
        for role in sec.get("roles", []):
            rh = role.get("header")
            if rh:
                t = rh.get("text", "").strip()
                if t:
                    parts.append(t)
            for list_key in ("meta_lines", "bullets"):
                for p in role.get(list_key, []):
                    t = p.get("text", "").strip()
                    if t:
                        parts.append(t)
    return " ".join(parts)


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
    ir_text = _ir_to_text(ir_dict)
    template_text = _docx_to_text(template_docx_path)

    ir_lower = ir_text.lower()
    evidence: list[str] = []
    hard_fail = False

    # ── Hard check: placeholder / lorem ipsum ────────────────────────────────
    if "lorem ipsum" in ir_lower or (
        "lorem" in ir_lower.split() and "ipsum" in ir_lower.split()
    ):
        return ContentInjectionResult(
            score=0.0,
            hard_fail=True,
            sim_llm=0.0,
            sim_template=0.0,
            evidence=["Lorem ipsum placeholder text detected in rendered output"],
        )

    has_placeholder = any(
        ph in ir_lower
        for ph in ("[firstname]", "[lastname]", "[name]", "firstname lastname")
    )

    # ── Token similarity ─────────────────────────────────────────────────────
    ir_tokens = _tokenize(ir_text)
    llm_tokens = _tokenize(llm_text)
    template_tokens = _tokenize(template_text)

    if not ir_tokens or not llm_tokens:
        score = 65.0 if not has_placeholder else 40.0
        if has_placeholder:
            evidence.append("Template placeholder text still present in rendered output")
        return ContentInjectionResult(
            score=score,
            hard_fail=False,
            sim_llm=0.0,
            sim_template=0.0,
            evidence=evidence or ["Insufficient text to compare injection quality"],
        )

    sim_llm = _jaccard(ir_tokens, llm_tokens)
    sim_template = _jaccard(ir_tokens, template_tokens)

    # ratio > 1 → rendered closer to LLM (good); < 1 → closer to template (bad)
    ratio = sim_llm / (sim_template + 0.001)

    score = 100.0

    if has_placeholder:
        score -= 25.0
        evidence.append("Template placeholder text still present in rendered output")

    if ratio >= 1.2:
        pass  # clearly closer to LLM — injection worked
    elif ratio >= 0.6:
        score -= 25.0
        evidence.append(
            f"Partial content injection: sim_llm={sim_llm:.2f}, sim_template={sim_template:.2f}"
        )
    else:
        score -= 50.0
        evidence.append(
            f"Possible injection failure: sim_llm={sim_llm:.2f}, sim_template={sim_template:.2f}"
            f" (ratio={ratio:.2f})"
        )
        if ratio < 0.2 and sim_template > 0.30:
            hard_fail = True
            evidence.append("Content appears largely unchanged from template")

    return ContentInjectionResult(
        score=max(0.0, min(100.0, score)),
        hard_fail=hard_fail,
        sim_llm=round(sim_llm, 3),
        sim_template=round(sim_template, 3),
        evidence=evidence,
    )

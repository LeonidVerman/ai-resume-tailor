"""Cover letter parsing and ledger utilities.

Provides deterministic routines for:
- Splitting a cover letter into header (address block + salutation) and body paragraphs.
- Building a cover_letter_ledger without relying on the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Matches the salutation line via regex search (for ParsedCoverLetter / parse_cover_letter).
# Accepts up to 80 chars after "dear ".
_SALUTATION_RE = re.compile(r"^\s*dear\s+.{1,80}$", re.IGNORECASE | re.MULTILINE)

# Matches the salutation at start-of-stripped-line (for parse_cover_letter_body).
_DEAR_LINE_RE = re.compile(r"^Dear\b", re.IGNORECASE)

# Bridge sentence canonical prefix (case-insensitive search).
_BRIDGE_PREFIX = "while my background is in"


@dataclass
class ParsedCoverLetter:
    """Cover letter split into header and body paragraphs.

    Attributes
    ----------
    header:
        The address block and salutation line (e.g. "Dear Hiring Manager,").
        Empty string when no salutation was detected.
    body_paragraphs:
        Ordered list of body paragraph strings (blank-line separated),
        NOT including the salutation / address header.
    """

    header: str
    body_paragraphs: list[str] = field(default_factory=list)

    def paragraph_text(self, n: int) -> str:
        """Return 1-based paragraph text (empty string if out of range)."""
        idx = n - 1
        if 0 <= idx < len(self.body_paragraphs):
            return self.body_paragraphs[idx]
        return ""


def parse_cover_letter(text: str) -> ParsedCoverLetter:
    """Split a cover letter into header and body paragraphs.

    The header includes everything up to and including the salutation line
    (e.g. "Dear Hiring Manager,").  Everything after the salutation is
    treated as body text and split into paragraphs at blank lines.

    If no salutation is detected the entire text becomes the body (header="").
    """
    m = _SALUTATION_RE.search(text)
    if m is None:
        header = ""
        body_text = text
    else:
        sal_end = m.end()
        header = text[:sal_end].strip()
        body_text = text[sal_end:].strip()

    body_paragraphs = [
        p.strip()
        for p in re.split(r"\n\n+", body_text)
        if p.strip()
    ]
    return ParsedCoverLetter(header=header, body_paragraphs=body_paragraphs)


def parse_cover_letter_body(text: str) -> list[str]:
    """Parse a cover letter and return only the body paragraphs.

    Algorithm
    ---------
    1. Normalize newlines (``\\r\\n`` / ``\\r`` → ``\\n``).
    2. Split into lines.
    3. Find the body start line:
       - Pass 1: first line (stripped) matching ``^Dear\\b`` (case-insensitive).
       - Pass 2 (fallback): first line containing ``\\bDear\\b`` anywhere.
       - If neither found: treat the entire text as body.
    4. Body = everything **after** the salutation line.
    5. Collapse 3+ consecutive blank lines → single blank line.
    6. Split on blank lines → body_paragraphs (stripped, non-empty).

    This is the canonical parser used by all validators and ledger builders.
    It correctly handles address blocks before the salutation so they are
    never counted as body paragraphs.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")

    body_start_idx: int | None = None

    # Pass 1: prefer line that starts with "Dear"
    for i, line in enumerate(lines):
        if _DEAR_LINE_RE.match(line.strip()):
            body_start_idx = i + 1
            break

    # Pass 2: fallback — any line containing "\bDear\b"
    if body_start_idx is None:
        for i, line in enumerate(lines):
            if re.search(r"\bDear\b", line, re.IGNORECASE):
                body_start_idx = i + 1
                break

    # No salutation found: treat entire text as body
    if body_start_idx is None:
        body_start_idx = 0

    body_lines = lines[body_start_idx:]
    body_text = "\n".join(body_lines).strip()

    # Collapse 3+ consecutive newlines to exactly two (one blank line)
    body_text = re.sub(r"\n{3,}", "\n\n", body_text)

    return [p.strip() for p in body_text.split("\n\n") if p.strip()]


def build_cover_letter_ledger(
    cover_letter_text: str,
    cover_letter_plan: dict,
) -> list[dict]:
    """Build a deterministic cover_letter_ledger from the written cover letter.

    For each proof point in cover_letter_plan, searches the required_exact_span
    verbatim in body_paragraphs and records the 1-based paragraph index.
    When the bridge sentence is required, searches for it by prefix.

    If a required item is not found the entry records ``exact_span=""``
    and ``location="missing"`` (consistent with evidence-ledger conventions).

    Returns
    -------
    list[dict]
        Entries with keys: ``proof_id``, ``exact_span``, ``location``.
    """
    body = parse_cover_letter_body(cover_letter_text)
    ledger: list[dict] = []

    # --- P1 / P2 required exact spans ---
    proof_points = cover_letter_plan.get("proof_points", [])
    for proof in proof_points:
        pid = proof.get("proof_id", "")
        span = proof.get("required_exact_span", "")
        if not pid or not span:
            continue

        found_para: int | None = None
        for i, para in enumerate(body):
            if span in para:
                found_para = i + 1  # 1-based
                break

        if found_para is not None:
            ledger.append({
                "proof_id": pid,
                "exact_span": span,
                "location": f"paragraph[{found_para}]",
            })
        else:
            ledger.append({
                "proof_id": pid,
                "exact_span": "",
                "location": "missing",
            })

    # --- BRIDGE sentence ---
    if cover_letter_plan.get("bridge_sentence_required", False):
        bridge_para_idx: int | None = None
        bridge_sentence: str = ""

        for i, para in enumerate(body):
            lower = para.lower()
            start = lower.find(_BRIDGE_PREFIX)
            if start != -1:
                bridge_para_idx = i + 1
                # Extract the sentence up to (and including) the first . or !
                end_dot = para.find(".", start)
                end_bang = para.find("!", start)
                ends = [e for e in (end_dot, end_bang) if e != -1]
                if ends:
                    end_pos = min(ends)
                    bridge_sentence = para[start:end_pos + 1].strip()
                else:
                    bridge_sentence = para[start:].strip()
                break

        if bridge_para_idx is not None and bridge_sentence:
            ledger.append({
                "proof_id": "BRIDGE",
                "exact_span": bridge_sentence,
                "location": f"paragraph[{bridge_para_idx}]",
            })
        else:
            ledger.append({
                "proof_id": "BRIDGE",
                "exact_span": "",
                "location": "missing",
            })

    return ledger

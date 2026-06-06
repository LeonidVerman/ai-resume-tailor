"""PDF-to-DOCX conversion artifact sanitizer.

LibreOffice converts PDF files (especially LaTeX-generated PDFs using FontAwesome
with Identity-H CID encoding) in a way that maps icon glyphs to Latin Extended-A/B
characters (e.g. ć U+0107 for email icon, Ħ U+0126 for phone icon) using Times New
Roman as the fallback font.  These runs appear alongside legitimate content runs that
use the document's actual body font (e.g. Trebuchet MS).

Detection strategy — structural, not glyph-specific:
  1. Determine the dominant explicit body font for the paragraph by printable-character
     count across all explicitly-fonted runs.
  2. For each run whose font differs from the body font:
     - If the run has ≤ 2 printable characters AND any character is in the
       Latin Extended-A/B range (U+0100–U+024F) or Private Use Area (U+E000–U+F8FF)
       → it is a converted icon artifact → clear the run text.
     - Clear immediately-following whitespace-only runs with the same non-body font.

Architecture contract:
  - Applied ONCE, immediately after LibreOffice PDF→DOCX conversion.
  - The parser receives the already-clean DOCX — no artifact handling inside the
    classifier, tailoring logic, updater, or renderer.
"""

from __future__ import annotations

import logging

from docx import Document
from docx.text.paragraph import Paragraph

log = logging.getLogger("tailor.docx_artifact_sanitizer")

# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------

DOCX_ARTIFACT_SANITIZER_ENABLED: bool = True

# ---------------------------------------------------------------------------
# Codepoint ranges for converted icon glyphs
# ---------------------------------------------------------------------------

_LATIN_EXT_LOW  = 0x0100   # Latin Extended-A start
_LATIN_EXT_HIGH = 0x024F   # Latin Extended-B end
_PUA_LOW  = 0xE000          # Private Use Area start
_PUA_HIGH = 0xF8FF          # Private Use Area end

_RECURSE_TAGS = frozenset({"tbl", "tr", "tc", "sdt", "sdtContent"})


def _is_converted_icon(cp: int) -> bool:
    """Return True if *cp* is a likely CID-mapped icon codepoint (Latin Ext or PUA)."""
    return _LATIN_EXT_LOW <= cp <= _LATIN_EXT_HIGH or _PUA_LOW <= cp <= _PUA_HIGH


def _para_body_font(para: Paragraph) -> str | None:
    """Return the dominant explicit font name in *para* by printable-character count.

    Only considers runs with an explicitly set ``run.font.name``.  Returns None when
    no run carries an explicit font (paragraph inherits everything from its style).
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for run in para.runs:
        fn = run.font.name
        if not fn:
            continue
        n = sum(1 for c in run.text if not c.isspace())
        if n:
            counts[fn] += n
    return counts.most_common(1)[0][0] if counts else None


def _sanitize_paragraph(para: Paragraph, body_font: str) -> int:
    """Strip font-mismatch converted-icon runs from *para*.

    A run is stripped when:
    - Its font differs from *body_font*.
    - It has ≤ 2 printable (non-whitespace) characters.
    - At least one character is in the Latin Extended-A/B or PUA codepoint range.

    Immediately-following whitespace-only runs with the same non-body font are also
    cleared (they are the inter-icon spacer runs LibreOffice inserts).

    Returns the number of runs cleared.
    """
    runs = para.runs
    n = len(runs)
    if n == 0:
        return 0

    to_clear = [False] * n

    for i, run in enumerate(runs):
        rn = run.font.name
        if not rn or rn == body_font:
            continue

        text = run.text
        printable = [c for c in text if not c.isspace()]

        if not printable:
            # Whitespace-only: clear if it immediately follows a stripped run
            # with the same non-body font (trailing icon spacer).
            if i > 0 and to_clear[i - 1] and runs[i - 1].font.name == rn:
                to_clear[i] = True
            continue

        if len(printable) > 2:
            continue  # too long to be a single-glyph icon

        if any(_is_converted_icon(ord(c)) for c in printable):
            to_clear[i] = True
            # Eagerly clear immediately-following whitespace runs with same font.
            j = i + 1
            while j < n:
                nxt = runs[j]
                if nxt.font.name != rn:
                    break
                if any(c for c in nxt.text if not c.isspace()):
                    break
                to_clear[j] = True
                j += 1

    stripped = 0
    for i, run in enumerate(runs):
        if to_clear[i]:
            log.debug(
                "ARTIFACT_STRIP rule=font_mismatch_icon run=%r font=%r paragraph=%r",
                run.text,
                run.font.name,
                para.text[:80],
            )
            run.text = ""
            stripped += 1

    return stripped


def _iter_all_paragraphs(doc: Document):
    """Yield every paragraph in *doc* including those nested in table cells."""
    def _walk(elem):
        for child in elem:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "p":
                yield Paragraph(child, doc)
            elif tag in _RECURSE_TAGS:
                yield from _walk(child)

    yield from _walk(doc.element.body)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def sanitize_docx_artifacts(docx_path: str, *, enabled: bool | None = None) -> str:
    """Strip font-mismatch icon artifacts from a LibreOffice-converted DOCX file.

    Reads *docx_path*, removes artifact runs in-place, and re-saves only when at
    least one run was stripped.

    Parameters
    ----------
    docx_path:
        Path to the DOCX file to sanitize (modified in-place when artifacts found).
    enabled:
        Override the module-level ``DOCX_ARTIFACT_SANITIZER_ENABLED`` flag.
        Pass ``False`` to skip sanitization unconditionally.

    Returns
    -------
    str
        The input *docx_path* (unchanged).
    """
    _enabled = enabled if enabled is not None else DOCX_ARTIFACT_SANITIZER_ENABLED
    if not _enabled:
        return docx_path

    doc = Document(docx_path)
    total_stripped = 0

    for para in _iter_all_paragraphs(doc):
        body_font = _para_body_font(para)
        if body_font is None:
            continue
        total_stripped += _sanitize_paragraph(para, body_font)

    if total_stripped:
        log.info(
            "ARTIFACT_SANITIZER path=%s stripped=%d",
            docx_path,
            total_stripped,
        )
        doc.save(docx_path)
    else:
        log.debug("ARTIFACT_SANITIZER path=%s stripped=0 (no changes)", docx_path)

    return docx_path

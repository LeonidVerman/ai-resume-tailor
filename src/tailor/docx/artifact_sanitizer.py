"""PDF-to-DOCX conversion artifact sanitizer.

LibreOffice converts PDF files (especially LaTeX-generated PDFs using FontAwesome
with Identity-H CID encoding) in a way that maps icon glyphs to Latin Extended-A/B
characters (e.g. ć U+0107 for email icon, Ħ U+0126 for phone icon) using Times New
Roman as the fallback font.

Two detection rules — both structural, not glyph-specific:

  Rule 1 — font-mismatch (primary):
    Determine the dominant explicit body font for the paragraph by non-whitespace
    character count.  For each run whose font differs from the body font:
    - Run has ≤ 2 non-whitespace characters
    - At least one is in Latin Extended-A/B (U+0100–U+024F) or PUA (U+E000–U+F8FF)
    → strip the run.  Also strip immediately-following whitespace runs in the same
    non-body font (LibreOffice inter-icon spacers).

  Rule 2 — contact-line fallback (secondary):
    Applied to paragraphs where ``_para_body_font`` returns None (all direct runs
    inherit their font — no explicit font to compare against).  For each direct run:
    - 1–2 non-whitespace characters, all in the artifact codepoint range
    - The remaining paragraph text (subsequent runs + hyperlinks) matches a contact
      pattern (email, phone, URL, social handle)
    → strip the run.

    This handles PDFs where the converter uses no explicit per-run fonts, leaving
    body-font detection blind.  It does NOT fire on legitimate Czech/Slovak text
    in properly authored DOCX files, which always carry explicit font information.

Architecture contract:
  - ``sanitize_docx_artifacts(path)`` is called from ``parse_docx()`` so any
    template DOCX — whether freshly converted or pre-existing — is cleaned before
    the parser reads it.  The renderer then sees already-clean XML.
  - No artifact handling inside the classifier, tailoring logic, updater, or renderer.
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

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _is_converted_icon(cp: int) -> bool:
    """Return True if *cp* is a likely CID-mapped icon codepoint (Latin Ext or PUA)."""
    return _LATIN_EXT_LOW <= cp <= _LATIN_EXT_HIGH or _PUA_LOW <= cp <= _PUA_HIGH


def _para_body_font(para: Paragraph) -> str | None:
    """Return the dominant explicit font name in *para* by non-whitespace character count.

    Only considers runs with an explicitly set ``run.font.name``.  Returns None when
    no run carries an explicit font (paragraph inherits everything from its style).

    Note: ``para.runs`` returns only direct ``w:r`` children — runs inside
    ``w:hyperlink`` elements are excluded.  This is intentional: hyperlink runs
    (which always use the body font) must not inflate the body-font count and hide
    a legitimate mismatch in the remaining runs.
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
    """Strip font-mismatch converted-icon runs from *para* (Rule 1).

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
        non_ws = [c for c in text if not c.isspace()]

        if not non_ws:
            # Whitespace-only: clear if it immediately follows a stripped run
            # with the same non-body font (trailing icon spacer).
            if i > 0 and to_clear[i - 1] and runs[i - 1].font.name == rn:
                to_clear[i] = True
            continue

        if len(non_ws) > 2:
            continue  # too long to be a single-glyph icon

        if any(_is_converted_icon(ord(c)) for c in non_ws):
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


def _para_following_text(para: Paragraph, after_run) -> str:
    """Return all text content after *after_run* in *para* (runs + hyperlinks)."""
    parts: list[str] = []
    recording = False
    for child in para._p:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "r":
            if child is after_run._r:
                recording = True
                continue
            if recording:
                for t in child.findall(f"{{{_W}}}t"):
                    parts.append(t.text or "")
        elif tag == "hyperlink" and recording:
            for r_el in child.findall(f"{{{_W}}}r"):
                for t_el in r_el.findall(f"{{{_W}}}t"):
                    parts.append(t_el.text or "")
    return "".join(parts).strip()


def _sanitize_contact_fallback(para: Paragraph) -> int:
    """Strip contact-icon artifacts using following-text context (Rule 2).

    Fires only when ``_para_body_font`` returns None — i.e., all direct runs
    in the paragraph inherit their font from the paragraph or document style.
    In that case, font-mismatch comparison is impossible, so this rule uses
    the CONTACT CONTEXT of the surrounding text as the artifact signal.

    A run is stripped when:
    - 1–2 non-whitespace characters, all in the artifact codepoint range
    - The remaining paragraph text (subsequent direct runs + hyperlink runs)
      matches a contact pattern (email, phone, URL, social handle)

    Does NOT fire when body_font is known — font-mismatch rule (Rule 1) handles
    those paragraphs, avoiding false positives on legitimate Czech/Slovak text
    where Latin Extended chars appear in the body font.

    Returns the number of runs cleared.
    """
    from tailor.compiler.pdf_text_normalizer import _looks_like_contact

    if _para_body_font(para) is not None:
        return 0  # font-mismatch rule covers this paragraph

    runs = para.runs
    if not runs:
        return 0

    stripped = 0
    for run in runs:
        if not run.text:
            continue
        non_ws = [c for c in run.text if not c.isspace()]
        if not non_ws or len(non_ws) > 2:
            continue
        if not all(_is_converted_icon(ord(c)) for c in non_ws):
            continue

        following = _para_following_text(para, run)
        if _looks_like_contact(following):
            log.debug(
                "ARTIFACT_STRIP rule=contact_fallback run=%r paragraph=%r",
                run.text,
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
    """Strip PDF-conversion icon artifacts from a DOCX file.

    Applies Rule 1 (font-mismatch) and Rule 2 (contact-line fallback) to every
    paragraph in the document, including table cells.  The file is modified
    in-place and re-saved only when at least one run was stripped.

    Safe to call repeatedly — runs that were already cleared become empty strings
    and are skipped on subsequent calls.

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
        if body_font is not None:
            total_stripped += _sanitize_paragraph(para, body_font)
        else:
            total_stripped += _sanitize_contact_fallback(para)

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

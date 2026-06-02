"""
PDF text extraction artifact normalization.

Two normalization levels:

  1. Span-level  (normalize_spans): called at PyMuPDF line-assembly time.
     Uses span font metadata to detect icon-font artifacts (FontAwesome, SymbolMT,
     Wingdings, etc.) and either strip them (contact icons) or convert them to a
     standard bullet marker (bullet glyphs mapped to ASCII/Latin by font encoding).

  2. Text-level  (normalize_text): applied to any assembled string.
     Cleans Unicode extraction artifacts that don't require font context:
     NBSP → space, ZWS → removed, soft-hyphen → removed, ligatures → ASCII.

Architecture rationale (Option A + text-cleanup):
  Icon font glyphs are only reliably identified via font names from PyMuPDF
  span dicts. Pattern-only detection ('f ' prefix, 'ć' prefix) would produce
  false positives on legitimate content. Span-level processing is therefore
  the correct layer for icon normalization. Text-level cleanup handles the
  remaining artifact classes that are font-agnostic.
"""

import logging
import re

log = logging.getLogger("tailor.pdf_normalization")

# ─── Icon font detection ────────────────────────────────────────────────────

_ICON_FONT_FRAGMENTS: tuple[str, ...] = (
    "fontawesome",
    "awesome",
    "wingdings",
    "webdings",
    "symbolmt",
    "zapfdingbats",
    "zapf",
    "materialsymbol",
    "materialicon",
    "icomoon",
    "glyphicon",
    "remixicon",
    "feathericon",
    "ionicons",
)


def _is_icon_font(font_name: str) -> bool:
    """Return True if *font_name* belongs to a known icon/symbol font family."""
    fn = font_name.lower()
    return any(kw in fn for kw in _ICON_FONT_FRAGMENTS)


# ─── Contact-info heuristic ─────────────────────────────────────────────────

_CONTACT_RE = re.compile(
    r"""
    [\w.+%-]+@[\w.-]+\.\w{2,}   # email
    | \+\d                        # international phone
    | \(\d{3}\)                   # US phone area code
    | https?://                   # URL
    | (www|linkedin|github|twitter|gitlab|bitbucket|behance|dribbble)\.
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _looks_like_contact(text: str) -> bool:
    """Return True when *text* is likely a contact-info value."""
    return bool(_CONTACT_RE.search(text.strip()))


# ─── Unicode artifact table ─────────────────────────────────────────────────

_UNICODE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    # Ligatures → component letters
    ("ﬁ", "fi"),   # ﬁ
    ("ﬂ", "fl"),   # ﬂ
    ("ﬃ", "ffi"),  # ﬃ
    ("ﬄ", "ffl"),  # ﬄ
    ("ﬀ", "ff"),   # ﬀ
    ("ﬅ", "ft"),   # ﬅ
    ("ﬆ", "st"),   # ﬆ
    # Invisible / zero-width characters → remove
    ("­", ""),     # soft hyphen (SHY)
    ("​", ""),     # zero-width space
    ("‌", ""),     # zero-width non-joiner
    ("‍", ""),     # zero-width joiner
    ("﻿", ""),     # BOM / zero-width no-break space
    ("⁠", ""),     # word joiner
    # Space variants → regular space
    (" ", " "),    # non-breaking space (NBSP)
    (" ", " "),    # narrow no-break space
    (" ", " "),    # thin space
    (" ", " "),    # figure space
)


# ─── Public API ─────────────────────────────────────────────────────────────


def normalize_spans(spans: "list[dict]") -> "tuple[str, str]":
    """Normalize a list of PyMuPDF spans from one PDF text line.

    Parameters
    ----------
    spans:
        List of span dicts as returned by ``page.get_text("dict")``.

    Returns
    -------
    (cleaned_text, artifact_type)
        *cleaned_text* is the normalized line text ready for the parser IR.
        *artifact_type* is one of:

        * ``''``               — no icon artifact detected
        * ``'bullet'``         — first span was an icon-font bullet marker;
                                 '• ' has been prepended to the content text
        * ``'contact_icon'``   — first span was an icon-font contact icon;
                                 the icon char has been stripped
        * ``'standalone_icon'``— standalone icon with no following content;
                                 *cleaned_text* is empty (caller should skip line)
    """
    if not spans:
        return "", ""

    first = spans[0]
    first_font: str = first.get("font") or ""
    first_text: str = (first.get("text") or "").strip()

    # Only inspect when first span is from a known icon font and has ≤2 chars
    if _is_icon_font(first_font) and first_text and len(first_text) <= 2:
        rest_raw = "".join(s.get("text", "") for s in spans[1:]).strip()

        if rest_raw:
            rest_clean = normalize_text(rest_raw)
            if _looks_like_contact(rest_clean):
                artifact = "contact_icon"
                log.debug(
                    "PDF_NORMALIZATION type=contact_icon font=%r before=%r after=%r",
                    first_font, first_text + " " + rest_raw, rest_clean,
                )
                return rest_clean, artifact
            else:
                artifact = "bullet"
                normed = "• " + rest_clean
                log.debug(
                    "PDF_NORMALIZATION type=bullet font=%r before=%r after=%r",
                    first_font, first_text + " " + rest_raw, normed,
                )
                return normed, artifact
        else:
            # Standalone icon with no following text in same line.
            # PUA-range glyphs are already caught by _line_pua_only in the parser;
            # ASCII-mapped icons (e.g. FontAwesome 'f') are not in PUA range.
            cp = ord(first_text[0]) if first_text else 0
            if 0xE000 <= cp <= 0xF8FF:
                # PUA — let _line_pua_only handle it
                pass
            else:
                log.debug(
                    "PDF_NORMALIZATION type=standalone_icon font=%r char=%r cp=0x%04x",
                    first_font, first_text, cp,
                )
                return "", "standalone_icon"

    raw = "".join(s.get("text", "") for s in spans).strip()
    cleaned = normalize_text(raw)
    return cleaned, ""


def normalize_text(text: str) -> str:
    """Apply Unicode artifact cleanup to *text*.

    Handles: ligatures (ﬁ→fi), soft hyphen, ZWS, NBSP, and other invisible chars.
    Does NOT alter alphanumeric content, punctuation, URLs, version numbers, or
    code identifiers — safe to call on any extracted PDF text.
    """
    for bad, good in _UNICODE_REPLACEMENTS:
        if bad in text:
            text = text.replace(bad, good)
    # Collapse runs of 3+ spaces to two (preserve intentional double-space for
    # column-aligned skills lists but prevent excessive whitespace from icon removal)
    if "   " in text:
        text = re.sub(r"   +", "  ", text)
    return text.strip()

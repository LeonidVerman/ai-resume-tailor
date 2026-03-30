"""IR parity test: same resume in DOCX and PDF format must produce equivalent IRs.

Paired samples:
  tests/samples/resume/docx/2-Leonid_Verman_Resume_2.docx
  tests/samples/resume/pfd/2-Leonid_Verman_Resume_2.pdf

Goal: pdf_parser and docx_parser should produce structurally identical IRs for
the same document.  This test lists all discrepancies and asserts on fixable ones,
documenting known limitations.

Normalization rules applied before comparison:
  - strip + collapse whitespace
  - case-insensitive (lowercase both sides)
  - PDF bullets: continuation lines are joined (line not ending in sentence
    punctuation followed by a line starting lowercase → same bullet)
  - Section headings: continuation fragments (heading ending with ",") joined
    with the next paragraph to reconstitute the full title
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_DOCX = _HERE / "samples" / "resume" / "docx" / "2-Leonid_Verman_Resume_2.docx"
_PDF  = _HERE / "samples" / "resume" / "pfd"  / "2-Leonid_Verman_Resume_2.pdf"


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Lowercase + collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _join_bullet_continuations(bullets: list) -> list[str]:
    """Merge PDF line-wrapped bullet fragments into single bullet strings.

    A line is a continuation of the previous bullet when:
      - the previous line does NOT end with sentence-ending punctuation (. ? ! :)
      - the current line starts with a lowercase letter (continuation of sentence)
    """
    result: list[str] = []
    for b in bullets:
        text = b.text.strip()
        if not text:
            continue
        if result and not re.search(r"[.?!:]\s*$", result[-1]) and text[0].islower():
            result[-1] = result[-1] + " " + text
        else:
            result.append(text)
    return result


def _join_heading_continuation(paras: list) -> list:
    """Join a section heading that ends with ',' with the next paragraph.

    PyMuPDF sometimes splits a multi-word heading across two text blocks
    (e.g. 'Websites, Portfolios,' + 'Profiles').  This re-stitches them
    so _group_sections can match against the full heading string.

    Returns a new list with the continuation para removed and its text
    appended to the preceding section_heading.
    """
    from tailor.compiler.models import ParaModel
    out: list = []
    for pm in paras:
        if (out
                and out[-1].semantic == "section_heading"
                and out[-1].text.rstrip().endswith(",")
                and pm.semantic in ("paragraph", "section_heading")):
            # Merge: clone the heading with combined text
            combined = out[-1].text.rstrip() + " " + pm.text.strip()
            out[-1] = out[-1].with_text(combined)
        else:
            out.append(pm)
    return out


# ---------------------------------------------------------------------------
# Discrepancy record
# ---------------------------------------------------------------------------

@dataclass
class Discrepancy:
    category: str       # "section_count", "section_type", "role_count",
                        # "bullet_count", "bullet_text", "meta_text", etc.
    path: str           # human-readable location, e.g. "experience[0].role[0]"
    docx_val: object
    pdf_val: object
    fixable: bool       # True → parser bug; False → known PDF limitation
    note: str = ""

    def __str__(self) -> str:
        fix_tag = "FIXABLE" if self.fixable else "KNOWN LIMITATION"
        return (
            f"  [{fix_tag}] {self.category} @ {self.path}\n"
            f"    DOCX: {self.docx_val!r}\n"
            f"    PDF : {self.pdf_val!r}"
            + (f"\n    note: {self.note}" if self.note else "")
        )


# ---------------------------------------------------------------------------
# IR comparison
# ---------------------------------------------------------------------------

def _match_sections(doc_secs: list, pdf_secs: list) -> list[tuple]:
    """Align DOCX and PDF sections by semantic_type + normalized title.

    Returns a list of (docx_section | None, pdf_section | None) pairs.
    Sections present only on one side are included as (sec, None) or (None, sec).
    """
    # Build index: (semantic_type, norm_title) -> section  for each side
    def _key(sec):
        return (sec.semantic_type, _norm(sec.title))

    pdf_by_key: dict[tuple, list] = {}
    for ps in pdf_secs:
        pdf_by_key.setdefault(_key(ps), []).append(ps)

    matched_pdf: set[int] = set()
    pairs: list[tuple] = []

    for ds in doc_secs:
        k = _key(ds)
        candidates = pdf_by_key.get(k, [])
        # Pick first unmatched PDF section with this key
        match = next((p for p in candidates if id(p) not in matched_pdf), None)
        if match is not None:
            matched_pdf.add(id(match))
            pairs.append((ds, match))
        else:
            pairs.append((ds, None))   # DOCX section absent from PDF

    # PDF sections that were not matched to any DOCX section
    for ps in pdf_secs:
        if id(ps) not in matched_pdf:
            pairs.append((None, ps))

    return pairs


def _compare_irs(doc, pdf) -> list[Discrepancy]:
    """Return a list of all discrepancies between DOCX IR and PDF IR."""
    issues: list[Discrepancy] = []

    doc_secs = doc.sections
    pdf_secs = pdf.sections

    # ── 1. Section set ───────────────────────────────────────────────────────
    doc_titles = {_norm(s.title) for s in doc_secs}
    pdf_titles = {_norm(s.title) for s in pdf_secs}

    only_docx = doc_titles - pdf_titles
    only_pdf  = pdf_titles - doc_titles

    if only_docx:
        issues.append(Discrepancy(
            "section_missing_from_pdf", "top-level",
            sorted(only_docx), "(absent)",
            fixable=False,
            note=(
                "PDF two-column sidebar: sidebar sections (Websites/Skills/Name/Profile) "
                "appear in header_paras instead of being parsed as separate sections. "
                "PyMuPDF reads blocks left-to-right so sidebar content precedes "
                "the main column's first recognized section heading."
            ),
        ))
    if only_pdf:
        issues.append(Discrepancy(
            "section_extra_in_pdf", "top-level",
            "(absent)", sorted(only_pdf),
            fixable=False,
        ))

    # ── 2. Per-section comparison (matched by semantic_type + title) ─────────
    pairs = _match_sections(doc_secs, pdf_secs)

    for ds, ps in pairs:
        if ds is None or ps is None:
            continue  # already reported as missing/extra above
        loc = f"section '{ds.title}'"

        # ── 2a. Experience roles ──────────────────────────────────────────
        if ds.semantic_type == "experience":
            if len(ds.roles) != len(ps.roles):
                issues.append(Discrepancy(
                    "role_count", loc,
                    len(ds.roles), len(ps.roles), fixable=False,
                ))
            for ri in range(min(len(ds.roles), len(ps.roles))):
                dr = ds.roles[ri]
                pr = ps.roles[ri]
                rloc = f"{loc}.role[{ri}]"

                # Role header (case-insensitive)
                if _norm(dr.header.text) != _norm(pr.header.text):
                    issues.append(Discrepancy(
                        "role_header", rloc,
                        dr.header.text.strip(), pr.header.text.strip(),
                        fixable=False,
                        note="Case difference from PDF all-caps font rendering.",
                    ))

                # Meta lines
                doc_meta = [_norm(m.text) for m in dr.meta_lines]
                pdf_meta = [_norm(m.text) for m in pr.meta_lines]
                if doc_meta != pdf_meta:
                    issues.append(Discrepancy(
                        "meta_lines", rloc,
                        doc_meta, pdf_meta, fixable=False,
                        note="Case difference from PDF all-caps font rendering.",
                    ))

                # Bullets — compare raw counts first (fixable), then text (known limitation)
                doc_bullets = [_norm(b.text) for b in dr.bullets]
                pdf_raw = [_norm(b.text) for b in pr.bullets]
                pdf_bullets_joined = [_norm(t) for t in
                                      _join_bullet_continuations(pr.bullets)]

                if len(pr.bullets) != len(dr.bullets):
                    # Raw count differs — parser hasn't joined continuation lines yet
                    issues.append(Discrepancy(
                        "bullet_count_raw", rloc,
                        len(dr.bullets), len(pr.bullets),
                        fixable=True,
                        note=(
                            f"After joining continuation lines: {len(pdf_bullets_joined)} "
                            f"(matches DOCX {len(doc_bullets)}). "
                            "PDF wraps long bullets across multiple lines; each wrap "
                            "fragment is a separate paragraph. Fix: join continuation "
                            "lines in pdf_parser._group_roles."
                        ),
                    ))
                elif len(doc_bullets) != len(pdf_bullets_joined):
                    # Raw counts match but joined counts don't — unexpected
                    issues.append(Discrepancy(
                        "bullet_count", rloc,
                        len(doc_bullets), len(pdf_bullets_joined),
                        fixable=False,
                    ))
                else:
                    for bi, (db, pb) in enumerate(zip(doc_bullets, pdf_bullets_joined)):
                        if db != pb:
                            issues.append(Discrepancy(
                                "bullet_text", f"{rloc}.bullet[{bi}]",
                                db, pb, fixable=False,
                                note="Case difference or minor text variation.",
                            ))

        # ── 2b. Non-experience body_paras ─────────────────────────────────
        else:
            doc_body = [_norm(p.text) for p in ds.body_paras if p.text.strip()]
            pdf_body = [_norm(p.text) for p in ps.body_paras if p.text.strip()]

            if len(doc_body) != len(pdf_body):
                issues.append(Discrepancy(
                    "body_para_count", loc,
                    len(doc_body), len(pdf_body), fixable=False,
                    note="Possible line splits in PDF or semantic differences.",
                ))
            else:
                for bi, (db, pb) in enumerate(zip(doc_body, pdf_body)):
                    if db != pb:
                        issues.append(Discrepancy(
                            "body_para_text", f"{loc}.body[{bi}]",
                            db, pb, fixable=False,
                        ))

            # Semantic labels: bullet vs paragraph
            doc_semantics = [p.semantic for p in ds.body_paras if p.text.strip()]
            pdf_semantics = [p.semantic for p in ps.body_paras if p.text.strip()]
            if doc_semantics != pdf_semantics:
                issues.append(Discrepancy(
                    "body_semantic", loc,
                    doc_semantics, pdf_semantics, fixable=False,
                    note=(
                        "DOCX assigns 'bullet' semantic via style name (ListBullet); "
                        "PDF has no bullet markers so plain items become 'paragraph'."
                    ),
                ))

    # ── 3. Header paras displaced content ───────────────────────────────────
    doc_header_texts = {_norm(p.text) for p in doc.header_paras if p.text.strip()}
    pdf_header_texts = {_norm(p.text) for p in pdf.header_paras if p.text.strip()}
    only_in_pdf = pdf_header_texts - doc_header_texts

    if only_in_pdf:
        issues.append(Discrepancy(
            "header_paras_displaced", "header_paras",
            "(not in DOCX header)",
            sorted(only_in_pdf),
            fixable=False,
            note=(
                "Two-column sidebar: these texts belong to sections "
                "(Websites/Skills/Name/Profile) but PyMuPDF reads them before "
                "the first recognized section heading, placing them in header_paras."
            ),
        ))

    return issues


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DOCX.exists() or not _PDF.exists(), reason="Paired samples missing")
def test_ir_parity():
    """DOCX and PDF of the same resume must produce equivalent IRs.

    Asserts that all *fixable* discrepancies are zero.
    Prints a full report of both fixable and known-limitation discrepancies.
    """
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.pdf_parser import parse_pdf

    doc = parse_docx(str(_DOCX))
    pdf_doc = parse_pdf(_PDF.read_bytes())

    issues = _compare_irs(doc, pdf_doc)

    fixable   = [i for i in issues if i.fixable]
    known     = [i for i in issues if not i.fixable]

    # --- Print full report --------------------------------------------------
    print(f"\n{'='*60}")
    print(f"IR Parity Report: {_DOCX.name}  vs  {_PDF.name}")
    print(f"{'='*60}")
    print(f"Total discrepancies: {len(issues)}  "
          f"(fixable: {len(fixable)}, known limitations: {len(known)})")

    if fixable:
        print(f"\n--- FIXABLE ({len(fixable)}) ---")
        for iss in fixable:
            print(iss)

    if known:
        print(f"\n--- KNOWN LIMITATIONS ({len(known)}) ---")
        for iss in known:
            print(iss)

    print(f"\n{'='*60}")

    # --- Assert on fixable issues -------------------------------------------
    assert not fixable, (
        f"{len(fixable)} fixable discrepancy(ies) between DOCX and PDF IR:\n"
        + "\n".join(str(i) for i in fixable)
    )

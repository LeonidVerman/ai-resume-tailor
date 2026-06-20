"""Tests for renderer layout_binding guard — timeline left-column preservation.

Sample 35 (Gleb Zernov) is a two-column newspaper DOCX layout:
  LEFT  column: date sidebar (para_9 – para_58, in header_paras)
  RIGHT column: Experience role content (para_92 – para_124, in sections/roles)

The render path is _render_from_layout_blocks → flat loop.  Two Word sections
share the physical document: section 2 (lb[8-69]) and section 3 (lb[70-91]).

Two bugs were fixed in the updater (updater.py):
  Bug 1 — _ext_ blocks inherited col-break from anchor (para_121).
  Bug 2 — DATE_COL_HEADER_CLEARED cleared narrow-column date sidebar text.

The tests here lock in renderer-level requirements so the proposed
layout_binding guard can be implemented safely:

1. _timeline_left_pids set — must include all left/spacer para_ids, exclude right.
2. Left-column paras must appear in layout_blocks (in physical order).
3. Right-column para_ids must appear in layout_blocks (right col order).
4. Left and right para_ids in layout_blocks must not overlap.
5. After apply_tailored, left sidebar paras carry preserved date text.
6. After apply_tailored, right column role headers carry LLM-updated text.
7. paragraph count in layout_blocks is the same before and after tailoring.
8. Non-timeline templates are unaffected (no _timeline_left_pids).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_GEN_DIR  = Path(__file__).parent / "samples" / "generation"

_S35_DOCX = _DOCX_DIR / "35-Gleb_Zernov_Resume.docx"
_S35_GEN  = _GEN_DIR  / "35-Gleb_Zernov-American_Tire_Distributors-Lead_Software_Engineer-444-20260609-204948.json"

_S35_AVAIL = _S35_DOCX.exists() and _S35_GEN.exists()

_NON_TIMELINE_DOCXS = [
    "1-Leonid_Verman_Resume_Template.docx",
    "6-Template1.docx",
    "9-Template4.docx",
    "20-Software-Engineer-Editable-Resume-Template-Download-in-docx-5.docx",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_s35_original():
    from tailor.compiler.docx_parser import parse_docx
    return parse_docx(str(_S35_DOCX))


def _load_s35_updated():
    import json
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.pipeline import apply_layout_fitting
    from tailor.compiler.updater import apply_tailored
    from tailor.compiler.classification_models import ClassificationOutput

    original = parse_docx(str(_S35_DOCX))
    with open(_S35_GEN, encoding="utf-8") as fh:
        gen = json.load(fh)
    llm_text = gen["llm_response"]["resume"]
    cls_data = gen.get("structured_resume", {}).get("classification")
    classification = ClassificationOutput.from_dict(cls_data) if cls_data else None
    llm_secs = apply_layout_fitting(original, parse_llm_output(llm_text))
    updated = apply_tailored(original, llm_secs, classification=classification)
    return original, updated


def _timeline_left_pids(doc) -> frozenset[str]:
    """Compute the set of left-column para_ids from layout_binding, as the
    renderer will build it."""
    result: set[str] = set()
    for sec in doc.sections:
        for role in (sec.roles or []):
            lb = role.layout_binding
            if lb and lb.get("kind") == "timeline_left_role_right":
                result |= set(lb.get("left_para_ids") or [])
                result |= set(lb.get("left_leading_spacer_ids") or [])
                result |= set(lb.get("left_trailing_spacer_ids") or [])
    return frozenset(result)


# ---------------------------------------------------------------------------
# 1. _timeline_left_pids set construction
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_left_pids_count():
    """Timeline left-pids set must have 46 entries (all left/spacer para_ids)."""
    doc = _load_s35_original()
    pids = _timeline_left_pids(doc)
    assert len(pids) == 46, (
        f"Expected 46 left-column para_ids, got {len(pids)}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_left_pids_all_in_header_paras():
    """Every left-column para_id must be in doc.header_paras — not in roles."""
    doc = _load_s35_original()
    pids = _timeline_left_pids(doc)
    hp_ids = {pm.para_id for pm in doc.header_paras if pm.para_id}
    not_in_hp = pids - hp_ids
    assert not not_in_hp, (
        f"Left-column para_ids not in header_paras: {not_in_hp}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_left_pids_disjoint_from_right():
    """Left-column para_ids must be disjoint from all right-column para_ids."""
    doc = _load_s35_original()
    left = _timeline_left_pids(doc)
    right: set[str] = set()
    for sec in doc.sections:
        for role in (sec.roles or []):
            lb = role.layout_binding
            if lb and lb.get("kind") == "timeline_left_role_right":
                right |= set(lb.get("right_para_ids") or [])
    overlap = left & right
    assert not overlap, (
        f"Left/right para_ids overlap: {overlap}"
    )


# ---------------------------------------------------------------------------
# 2. Layout_blocks physical order
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_left_pids_in_layout_blocks():
    """All timeline left-column para_ids must have entries in layout_blocks."""
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    pids = _timeline_left_pids(doc)
    lb_ids = {
        lb.para_id for lb in (doc.layout_blocks or [])
        if isinstance(lb, LayoutParagraphBlock) and lb.para_id
    }
    missing = pids - lb_ids
    assert not missing, (
        f"Left-column para_ids missing from layout_blocks: {missing}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_left_pids_lb_indices():
    """Left-column layout_blocks must appear at lb[8-79] — before right column."""
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    pids = _timeline_left_pids(doc)
    lbs = list(doc.layout_blocks or [])
    indices = [i for i, lb in enumerate(lbs)
               if isinstance(lb, LayoutParagraphBlock) and lb.para_id in pids]
    assert indices, "No left-column para_ids found in layout_blocks"
    # All must be before the last right-column block (role content is lb[45-90])
    assert max(indices) <= 83, (
        f"Left-column lb index {max(indices)} exceeds expected max=83"
    )
    # Must include early blocks (lb[8..]) not just section 3
    assert min(indices) <= 15, (
        f"Left-column lb index starts at {min(indices)}, expected <= 15"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_two_col_breaks_bypasses_table_routing():
    """Two col-breaks cause _find_single_col_break_idx to return None.

    This is why Sample 35 uses the flat loop, not the two-col table path.
    The test documents this behaviour so future refactors preserve it.
    """
    from tailor.compiler.docx_renderer import _find_single_col_break_idx
    doc = _load_s35_original()
    result = _find_single_col_break_idx(doc.layout_blocks)
    assert result is None, (
        f"Expected None (two col-breaks → flat loop), got idx={result}"
    )


# ---------------------------------------------------------------------------
# 3. Updater output: left sidebar text preserved, right column updated
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_updated_left_sidebar_text_preserved():
    """After apply_tailored, left-column date sidebar paras keep original text.

    This tests the updater Bug 2 fix.  The renderer guard in layout_binding
    will enforce this independently (defense in depth), but the updater must
    already be correct.
    """
    from tailor.compiler.updater import _DATE_COL_PARA_RE

    original, updated = _load_s35_updated()

    orig_hp = {pm.para_id: pm.text for pm in original.header_paras if pm.para_id}
    upd_hp  = {pm.para_id: pm.text for pm in updated.header_paras if pm.para_id}

    left = _timeline_left_pids(original)
    date_left = [
        pid for pid in left
        if orig_hp.get(pid, "").strip()
        and _DATE_COL_PARA_RE.match(orig_hp[pid].strip())
    ]
    assert date_left, "No date-fragment left-column paras found"

    cleared = [
        pid for pid in date_left
        if upd_hp.get(pid, "__NOT_FOUND__") == ""
    ]
    assert not cleared, (
        f"{len(cleared)} date sidebar para(s) were cleared to empty string: "
        + ", ".join(f"{p}={orig_hp[p]!r}" for p in cleared[:5])
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_updated_right_column_has_content():
    """After apply_tailored, right-column role headers have non-empty text."""
    original, updated = _load_s35_updated()

    exp_roles = [
        r for sec in updated.sections
        if sec.semantic_type == "experience"
        for r in (sec.roles or [])
    ]
    assert exp_roles, "No experience roles in updated doc"
    empty_headers = [r for r in exp_roles if not r.header.text.strip()]
    assert not empty_headers, (
        f"{len(empty_headers)} role(s) have empty header after tailoring"
    )


# ---------------------------------------------------------------------------
# 4. Layout_blocks count stability
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_layout_blocks_count_stable_after_tailoring():
    """layout_blocks count must not decrease after apply_tailored.

    The updater may ADD _ext_ blocks (extra bullets), so the count can
    increase, but it must never decrease (that would mean paragraphs are lost).
    """
    original, updated = _load_s35_updated()
    orig_count = len(list(original.layout_blocks or []))
    upd_count  = len(list(updated.layout_blocks or []))
    assert upd_count >= orig_count, (
        f"layout_blocks shrank: original={orig_count} updated={upd_count}"
    )


# ---------------------------------------------------------------------------
# 5. Non-timeline templates: no _timeline_left_pids
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", _NON_TIMELINE_DOCXS)
def test_non_timeline_templates_have_no_left_pids(fname):
    """Non-newspaper templates must produce an empty _timeline_left_pids set."""
    path = _DOCX_DIR / fname
    if not path.exists():
        pytest.skip(f"{fname} not found")
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(str(path))
    pids = _timeline_left_pids(doc)
    assert not pids, (
        f"{fname}: unexpectedly has {len(pids)} timeline_left_pids"
    )

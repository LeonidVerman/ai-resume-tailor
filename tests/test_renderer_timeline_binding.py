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


# ---------------------------------------------------------------------------
# 6. _build_timeline_row_table — structural tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_table_row_count():
    """_build_timeline_row_table must produce exactly 6 rows (one per role)."""
    from lxml import etree
    from tailor.compiler.docx_renderer import _build_timeline_row_table
    from tailor.compiler.models import LayoutParagraphBlock

    original, updated = _load_s35_updated()
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    tbl, consumed = _build_timeline_row_table(
        updated,
        {},  # para_lookup not needed for row count test
        list(updated.layout_blocks or []),
        None, None,
        False,
        None,
    )
    assert tbl is not None, "_build_timeline_row_table returned None"
    rows = tbl.findall(f"{{{_W_NS}}}tr")
    assert len(rows) == 6, (
        f"Expected 6 table rows (one per role), got {len(rows)}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_table_consumed_pids_cover_left():
    """consumed_pids must include all 46 left-column para_ids."""
    from tailor.compiler.docx_renderer import _build_timeline_row_table

    original, updated = _load_s35_updated()
    left = _timeline_left_pids(updated)

    tbl, consumed = _build_timeline_row_table(
        updated, {}, list(updated.layout_blocks or []),
        None, None, False, None,
    )
    missing = left - consumed
    assert not missing, (
        f"{len(missing)} left-column para_ids NOT in consumed_pids: {missing}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_table_no_duplicate_left_pids():
    """Each left-column para_id must appear in at most one row's left cell."""
    from lxml import etree
    from tailor.compiler.docx_renderer import _build_timeline_row_table

    original, updated = _load_s35_updated()
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"

    tbl, _ = _build_timeline_row_table(
        updated, {}, list(updated.layout_blocks or []),
        None, None, False, None,
    )
    seen: set[str] = set()
    duplicates: set[str] = set()
    for tr in tbl.findall(f"{{{_W_NS}}}tr"):
        tcs = tr.findall(f"{{{_W_NS}}}tc")
        if not tcs:
            continue
        left_tc = tcs[0]
        for p in left_tc.findall(f".//{{{_W_NS}}}p"):
            pid = p.get(f"{{{_W14_NS}}}paraId") or p.get(f"{{{_W_NS}}}paraId")
            if pid:
                if pid in seen:
                    duplicates.add(pid)
                seen.add(pid)
    assert not duplicates, (
        f"Duplicate left-column para_ids across rows: {duplicates}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_timeline_table_borderless():
    """The generated table must have borderless w:tblBorders (all val='none')."""
    from lxml import etree
    from tailor.compiler.docx_renderer import _build_timeline_row_table

    original, updated = _load_s35_updated()
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    tbl, _ = _build_timeline_row_table(
        updated, {}, list(updated.layout_blocks or []),
        None, None, False, None,
    )
    borders = tbl.find(f"{{{_W_NS}}}tblPr/{{{_W_NS}}}tblBorders")
    assert borders is not None, "w:tblBorders element not found"
    for child in borders:
        val = child.get(f"{{{_W_NS}}}val")
        assert val == "none", (
            f"Border {child.tag} has val={val!r}, expected 'none'"
        )


# ---------------------------------------------------------------------------
# 7. Non-timeline samples unaffected
# ---------------------------------------------------------------------------

_S34_DOCX = _DOCX_DIR / "34-Valerii_Konchin_Resume.docx"
_S36_DOCX = _DOCX_DIR / "36-Asia_Dalakova_Resume.docx"


@pytest.mark.parametrize("docx_path", [_S34_DOCX, _S36_DOCX],
                         ids=["sample34", "sample36"])
def test_non_s35_no_timeline_table(docx_path):
    """Samples 34 and 36 must produce no timeline row-table (no layout_binding)."""
    if not docx_path.exists():
        pytest.skip(f"{docx_path.name} not found")
    from tailor.compiler.docx_renderer import _build_timeline_row_table
    from tailor.compiler.docx_parser import parse_docx

    doc = parse_docx(str(docx_path))
    tbl, consumed = _build_timeline_row_table(
        doc, {}, list(doc.layout_blocks or []),
        None, None, False, None,
    )
    assert tbl is None, (
        f"{docx_path.name}: expected no timeline table, but got one"
    )
    assert len(consumed) == 0, (
        f"{docx_path.name}: expected empty consumed_pids, got {len(consumed)}"
    )


# ---------------------------------------------------------------------------
# 8. Pre-implementation design guards for corrected table renderer (v2)
#
# These tests lock in REQUIRED design properties that the v1 table builder
# violated.  They must all pass before the table path is re-enabled.
# ---------------------------------------------------------------------------

def _explicit_binding_consumed_pids(doc) -> frozenset:
    """Return the consumed_pids set built from explicit layout_binding IDs only.

    This is the CORRECTED definition: no range-sweep, no col-break detection,
    no header_para heuristics.  Only para_ids that role.layout_binding
    explicitly lists as left/spacer/right are consumed.
    """
    consumed: set[str] = set()
    for sec in (doc.sections or []):
        for role in (sec.roles or []):
            lb = role.layout_binding
            if not lb or lb.get("kind") != "timeline_left_role_right":
                continue
            consumed.update(lb.get("left_para_ids") or [])
            consumed.update(lb.get("left_leading_spacer_ids") or [])
            consumed.update(lb.get("left_trailing_spacer_ids") or [])
            consumed.update(lb.get("right_para_ids") or [])
    return frozenset(consumed)


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_experience_heading_not_in_explicit_binding():
    """para_59 (Experience heading + COL_BRK) must NOT be in any role's explicit binding.

    The corrected renderer must emit it verbatim before the table, not consume it.
    """
    doc = _load_s35_original()
    consumed = _explicit_binding_consumed_pids(doc)
    assert "para_59" not in consumed, (
        "para_59 (Experience heading) is in explicit binding — it would be consumed "
        "and lost; must be emitted verbatim before the table instead"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_sectpr_boundary_not_in_explicit_binding():
    """para_120 (sectPr boundary) must NOT be in any role's explicit binding.

    The corrected renderer must emit it verbatim after the first table segment so
    Education/Skills/Projects sections keep their original section geometry.
    """
    doc = _load_s35_original()
    consumed = _explicit_binding_consumed_pids(doc)
    assert "para_120" not in consumed, (
        "para_120 (sectPr boundary) is in explicit binding — consuming it strips "
        "the section geometry that Education/Skills/Projects sections depend on"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_education_blocks_not_in_explicit_binding():
    """para_125 (Education heading) and later must NOT appear in any role binding.

    The corrected consumed_pids must never include non-Experience blocks.
    """
    doc = _load_s35_original()
    consumed = _explicit_binding_consumed_pids(doc)
    from tailor.compiler.models import LayoutParagraphBlock
    lbs = list(doc.layout_blocks or [])
    edu_idx = next(
        (i for i, b in enumerate(lbs)
         if isinstance(b, LayoutParagraphBlock) and b.para_id == "para_125"),
        None,
    )
    assert edu_idx is not None, "para_125 (Education) not found in layout_blocks"
    edu_pids = {
        b.para_id for b in lbs[edu_idx:]
        if isinstance(b, LayoutParagraphBlock) and b.para_id
    }
    leaked = consumed & edu_pids
    assert not leaked, (
        f"{len(leaked)} Education/Skills/Projects para_ids leaked into "
        f"explicit consumed: {sorted(leaked)[:5]}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_roles_split_by_sectpr_boundary():
    """Roles 0-3 right_para_ids all precede para_120 in layout_blocks;
    roles 4-5 right_para_ids all follow para_120.

    This proves the corrected renderer needs TWO table segments — one per
    Word section — with para_120 emitted verbatim between them.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    lbs = list(doc.layout_blocks or [])
    pid_to_idx = {
        b.para_id: i for i, b in enumerate(lbs)
        if isinstance(b, LayoutParagraphBlock) and b.para_id
    }
    sectpr_idx = pid_to_idx.get("para_120")
    assert sectpr_idx is not None, "para_120 not found in layout_blocks"

    roles = [
        r for sec in doc.sections
        for r in (sec.roles or [])
        if r.layout_binding and r.layout_binding.get("kind") == "timeline_left_role_right"
    ]
    roles.sort(key=lambda r: r.layout_binding["row_index"])

    before_boundary = []
    after_boundary = []
    for r in roles:
        rpids = r.layout_binding.get("right_para_ids") or []
        indices = [pid_to_idx[p] for p in rpids if p in pid_to_idx]
        if not indices:
            continue
        if max(indices) < sectpr_idx:
            before_boundary.append(r.layout_binding["row_index"])
        elif min(indices) > sectpr_idx:
            after_boundary.append(r.layout_binding["row_index"])

    assert before_boundary == [0, 1, 2, 3], (
        f"Expected roles 0-3 before para_120, got {before_boundary}"
    )
    assert after_boundary == [4, 5], (
        f"Expected roles 4-5 after para_120, got {after_boundary}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_missing_right_para_ids_identified():
    """para_103, para_104, para_115, para_116 are in right_para_ids for roles 2/3
    but absent from layout_blocks.  These are LLM-compatible paragraphs that must
    be rendered from para_lookup via synthetic element building, not silently skipped.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    original, updated = _load_s35_updated()
    lbs = list(updated.layout_blocks or [])
    lb_pids = {b.para_id for b in lbs if isinstance(b, LayoutParagraphBlock) and b.para_id}

    roles = [
        r for sec in updated.sections
        for r in (sec.roles or [])
        if r.layout_binding and r.layout_binding.get("kind") == "timeline_left_role_right"
    ]
    roles.sort(key=lambda r: r.layout_binding["row_index"])

    missing_by_role: dict[int, list[str]] = {}
    for r in roles:
        ri = r.layout_binding["row_index"]
        missing = [
            p for p in (r.layout_binding.get("right_para_ids") or [])
            if p not in lb_pids
        ]
        if missing:
            missing_by_role[ri] = missing

    assert 2 in missing_by_role, "Role 2 should have missing right_para_ids"
    assert 3 in missing_by_role, "Role 3 should have missing right_para_ids"
    assert "para_103" in missing_by_role.get(2, []), "para_103 must be missing from layout_blocks for role 2"
    assert "para_104" in missing_by_role.get(2, []), "para_104 must be missing from layout_blocks for role 2"
    assert "para_115" in missing_by_role.get(3, []), "para_115 must be missing from layout_blocks for role 3"
    assert "para_116" in missing_by_role.get(3, []), "para_116 must be missing from layout_blocks for role 3"


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_missing_right_para_ids_renderable_via_para_lookup():
    """All right_para_ids missing from layout_blocks must be present in para_lookup
    with a renderable style proto or paragraph_profile.

    The corrected table renderer must fall back to a synthetic element for these
    rather than silently dropping them.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    from tailor.compiler.docx_renderer import _build_para_lookup
    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    lbs = list(updated.layout_blocks or [])
    lb_pids = {b.para_id for b in lbs if isinstance(b, LayoutParagraphBlock) and b.para_id}

    not_renderable: list[str] = []
    for sec in updated.sections:
        for role in (sec.roles or []):
            lb = role.layout_binding
            if not lb or lb.get("kind") != "timeline_left_role_right":
                continue
            for pid in (lb.get("right_para_ids") or []):
                if pid in lb_pids:
                    continue  # has LayoutParagraphBlock — normal path
                pm = para_lookup.get(pid)
                if pm is None or (
                    pm.style.xml_proto is None and pm.paragraph_profile is None
                ):
                    not_renderable.append(pid)

    assert not not_renderable, (
        f"{len(not_renderable)} right_para_ids missing from layout_blocks AND "
        f"not renderable via para_lookup: {not_renderable}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_experience_heading_has_col_break():
    """para_59 carries a w:br type='column' — it is the right-column anchor for
    the Experience section.  The corrected renderer must strip the column break
    before emitting para_59 as a plain body paragraph above the table.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    lbs = list(doc.layout_blocks or [])
    blk = next(
        (b for b in lbs if isinstance(b, LayoutParagraphBlock) and b.para_id == "para_59"),
        None,
    )
    assert blk is not None, "para_59 not found in layout_blocks"
    assert blk.xml_proto_xml and 'type="column"' in blk.xml_proto_xml, (
        "para_59 expected to carry a column break (type='column')"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_sectpr_boundary_para_carries_secpr():
    """para_120 carries an embedded w:sectPr in its pPr.  The corrected renderer
    must emit this paragraph verbatim between table segment 1 and segment 2 so
    subsequent sections (Education/Skills/Projects) inherit the correct geometry.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    lbs = list(doc.layout_blocks or [])
    blk = next(
        (b for b in lbs if isinstance(b, LayoutParagraphBlock) and b.para_id == "para_120"),
        None,
    )
    assert blk is not None, "para_120 not found in layout_blocks"
    assert blk.xml_proto_xml and "sectPr" in blk.xml_proto_xml, (
        "para_120 expected to carry an embedded sectPr"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_explicit_binding_excludes_structural_paras():
    """The corrected consumed_pids (explicit binding only) must exclude:
    - para_59 (Experience heading / col-break anchor)
    - para_120 (sectPr boundary)
    - para_121 col-break (Role 4 header, but binding already includes it via right_para_ids)
    - All Education/Skills/Projects blocks (para_125+)

    This is the definitive gate: if this test passes, the corrected design is safe
    to activate without accidentally consuming structural paragraphs.
    """
    from tailor.compiler.models import LayoutParagraphBlock
    doc = _load_s35_original()
    consumed = _explicit_binding_consumed_pids(doc)

    structural_must_not_be_consumed = {
        "para_59",   # Experience section heading (col-break anchor)
        "para_120",  # sectPr two-column boundary
    }
    wrongly_consumed = consumed & structural_must_not_be_consumed
    assert not wrongly_consumed, (
        f"Structural paragraphs incorrectly in consumed_pids: {wrongly_consumed}"
    )

    # Verify Education/Skills/Projects blocks are clean
    lbs = list(doc.layout_blocks or [])
    edu_idx = next(
        (i for i, b in enumerate(lbs)
         if isinstance(b, LayoutParagraphBlock) and b.para_id == "para_125"),
        None,
    )
    if edu_idx is not None:
        edu_pids = {
            b.para_id for b in lbs[edu_idx:]
            if isinstance(b, LayoutParagraphBlock) and b.para_id
        }
        leaked = consumed & edu_pids
        assert not leaked, (
            f"Education/Skills/Projects pids in explicit consumed: {sorted(leaked)[:5]}"
        )


# ---------------------------------------------------------------------------
# 9. v2 two-segment table reconstruction (feature-flagged)
# ---------------------------------------------------------------------------


def _render_s35_with_v2(updated_doc):
    """Call _render_from_layout_blocks with the v2 flag enabled.

    Returns the rendered body element (lxml) so tests can inspect the DOM.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import (
        _render_from_layout_blocks,
        _set_timeline_row_table_v2,
    )

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _set_timeline_row_table_v2(True)
    try:
        out_doc = DocxDoc()
        body = out_doc.element.body
        sectPr = body.find(f"{{{_W_NS}}}sectPr")
        _render_from_layout_blocks(updated_doc, body, sectPr)
        return body
    finally:
        _set_timeline_row_table_v2(False)


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_build_segments_returns_two_tables():
    """_build_timeline_segments must return two non-None table elements."""
    from tailor.compiler.docx_renderer import (
        _build_para_lookup,
        _build_timeline_segments,
    )
    from tailor.compiler.models import LayoutParagraphBlock

    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    lbs = list(updated.layout_blocks or [])

    # Find main sectPr geometry (same pattern as _render_from_layout_blocks)
    from docx import Document as DocxDoc
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    out_doc = DocxDoc()
    body = out_doc.element.body
    sectPr = body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, consumed, diag = _build_timeline_segments(
        updated, para_lookup, lbs, None, None, sectPr
    )
    assert seg1 is not None, "seg1 table must not be None"
    assert seg2 is not None, "seg2 table must not be None"
    assert diag["seg1_roles"] == [0, 1, 2, 3], (
        f"Segment 1 must contain roles 0-3, got {diag['seg1_roles']}"
    )
    assert diag["seg2_roles"] == [4, 5], (
        f"Segment 2 must contain roles 4-5, got {diag['seg2_roles']}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_consumed_excludes_structural_pids():
    """v2 consumed_pids must exclude para_59 (Experience heading).
    para_120 (sectPr) IS consumed: emitting it as a body paragraph causes an
    unwanted LibreOffice page break between the two table segments."""
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments
    from docx import Document as DocxDoc

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    _, _, consumed, diag = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    assert "para_59" not in consumed, "para_59 must not be consumed (it is the seg1 heading)"
    assert "para_120" in consumed, "para_120 must be consumed to prevent unwanted page break"
    assert "para_128" not in consumed, (
        "para_128 must NOT be consumed — it governs post-table section geometry (top=860 margin)"
    )
    strip_pids = diag.get("multicol_sectpr_strip_pids") or []
    assert "para_128" in strip_pids, (
        "para_128 must appear in multicol_sectpr_strip_pids so the main loop strips w:cols"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_no_skipped_pids():
    """All right_para_ids (including synthetic ones) must be rendered — skipped_pids empty."""
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments
    from docx import Document as DocxDoc

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    _, _, _, diag = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    assert not diag.get("skipped_pids"), (
        f"Skipped pids (not rendered): {diag['skipped_pids']}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_synthetic_pids_reported():
    """Synthetic pids (para_103/104/115/116) must appear in diag['synthetic_pids']."""
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments
    from docx import Document as DocxDoc

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    _, _, _, diag = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    syn = set(diag.get("synthetic_pids", []))
    for pid in ("para_103", "para_104", "para_115", "para_116"):
        assert pid in syn, f"{pid} not in synthetic_pids; got {syn}"


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_rendered_body_has_two_tables():
    """Full render with v2 flag must produce exactly 2 w:tbl elements in the body."""
    from lxml import etree

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)
    tbls = body.findall(f"{{{_W_NS}}}tbl")
    assert len(tbls) == 2, (
        f"Expected 2 tables in rendered body, got {len(tbls)}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_seg1_has_four_rows():
    """Segment 1 table (before sectPr boundary) must have 4 rows (roles 0-3)."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)
    tbls = body.findall(f"{{{_W_NS}}}tbl")
    assert len(tbls) >= 1
    rows = tbls[0].findall(f"{{{_W_NS}}}tr")
    assert len(rows) == 4, f"Segment 1 expected 4 rows, got {len(rows)}"


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_seg2_has_two_rows():
    """Segment 2 table (after sectPr boundary) must have 2 rows (roles 4-5)."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)
    tbls = body.findall(f"{{{_W_NS}}}tbl")
    assert len(tbls) >= 2
    rows = tbls[1].findall(f"{{{_W_NS}}}tr")
    assert len(rows) == 2, f"Segment 2 expected 2 rows, got {len(rows)}"


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_para59_appears_before_first_table():
    """para_59 (Experience heading) must appear as a body paragraph before the first table."""
    from lxml import etree

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)

    children = list(body)
    para59_idx = next(
        (i for i, c in enumerate(children)
         if c.tag == f"{{{_W_NS}}}p"
         and c.get(f"{{{_W14_NS}}}paraId") == "para_59"),
        None,
    )
    first_tbl_idx = next(
        (i for i, c in enumerate(children) if c.tag == f"{{{_W_NS}}}tbl"),
        None,
    )
    assert para59_idx is not None, "para_59 not found in rendered body"
    assert first_tbl_idx is not None, "no table found in rendered body"
    assert para59_idx < first_tbl_idx, (
        f"para_59 (idx={para59_idx}) must come BEFORE first table (idx={first_tbl_idx})"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_para59_no_column_break():
    """para_59 in rendered output must have its column break stripped."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)

    para59 = next(
        (c for c in body
         if c.tag == f"{{{_W_NS}}}p"
         and c.get(f"{{{_W14_NS}}}paraId") == "para_59"),
        None,
    )
    assert para59 is not None, "para_59 not found in rendered body"
    col_breaks = para59.findall(f".//{{{_W_NS}}}br[@{{{_W_NS}}}type='column']")
    assert not col_breaks, (
        f"para_59 must not contain column break after rendering, found {len(col_breaks)}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_para120_consumed_not_in_body():
    """para_120 (sectPr boundary) must be consumed and absent from the rendered body.
    When emitted as a body paragraph the continuous sectPr triggers an unwanted
    page break in LibreOffice between the two table segments."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)

    children = list(body)
    tbl_indices = [i for i, c in enumerate(children) if c.tag == f"{{{_W_NS}}}tbl"]
    assert len(tbl_indices) >= 2, f"Need 2 tables, found {len(tbl_indices)}"

    para120 = next(
        (c for c in children
         if c.tag == f"{{{_W_NS}}}p"
         and c.get(f"{{{_W14_NS}}}paraId") == "para_120"),
        None,
    )
    assert para120 is None, "para_120 must be consumed (absent from rendered body)"


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_para128_emitted_cols_stripped():
    """para_128 must appear exactly once in the rendered body, with w:cols stripped
    and w:type=continuous so Education/Skills content flows without a page break."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)

    children = list(body)
    tbl_indices = [i for i, c in enumerate(children) if c.tag == f"{{{_W_NS}}}tbl"]
    assert len(tbl_indices) >= 2

    para128_els = [
        (i, c) for i, c in enumerate(children)
        if c.tag == f"{{{_W_NS}}}p" and c.get(f"{{{_W14_NS}}}paraId") == "para_128"
    ]
    assert len(para128_els) == 1, (
        f"para_128 must appear exactly once in body, got {len(para128_els)}"
    )
    pos, el = para128_els[0]
    assert pos > max(tbl_indices), (
        f"para_128 (pos={pos}) must appear after both tables (last={max(tbl_indices)})"
    )
    sp = el.find(f"{{{_W_NS}}}pPr/{{{_W_NS}}}sectPr")
    assert sp is not None, "para_128 must retain sectPr"
    cols = sp.find(f"{{{_W_NS}}}cols")
    assert cols is None, "para_128 sectPr must have w:cols stripped"
    type_el = sp.find(f"{{{_W_NS}}}type")
    assert type_el is not None and type_el.get(f"{{{_W_NS}}}val") == "continuous", (
        "para_128 sectPr must be type=continuous to avoid a page break"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_education_after_both_tables():
    """para_125 (Education heading) must appear after the second table."""
    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
    _, updated = _load_s35_updated()
    body = _render_s35_with_v2(updated)

    children = list(body)
    tbl_indices = [i for i, c in enumerate(children) if c.tag == f"{{{_W_NS}}}tbl"]
    assert len(tbl_indices) >= 2

    para125_idx = next(
        (i for i, c in enumerate(children)
         if c.tag == f"{{{_W_NS}}}p"
         and c.get(f"{{{_W14_NS}}}paraId") == "para_125"),
        None,
    )
    assert para125_idx is not None, "para_125 (Education) not found in rendered body"
    assert para125_idx > tbl_indices[1], (
        f"para_125 (idx={para125_idx}) must come AFTER second table (idx={tbl_indices[1]})"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_left_cell_no_before_spacing():
    """Date paragraphs in left cells must have w:spacing w:before stripped.

    The template XML protos carry w:spacing w:before values tuned for the
    newspaper-column layout.  Inside a table cell those values offset the date
    text downward from the cell top, breaking role-header alignment.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    for seg in (seg1, seg2):
        if seg is None:
            continue
        for tr in seg.findall(f"{{{_W_NS}}}tr"):
            cells = tr.findall(f"{{{_W_NS}}}tc")
            if not cells:
                continue
            tc_left = cells[0]  # first cell = left/date column
            for para in tc_left.findall(f"{{{_W_NS}}}p"):
                pPr = para.find(f"{{{_W_NS}}}pPr")
                if pPr is None:
                    continue
                sp = pPr.find(f"{{{_W_NS}}}spacing")
                if sp is None:
                    continue
                before_val = sp.get(f"{{{_W_NS}}}before")
                after_val = sp.get(f"{{{_W_NS}}}after")
                assert before_val is None or int(before_val) == 0, (
                    f"Left cell para has w:spacing w:before={before_val} — "
                    "must be stripped so date text starts at cell top"
                )
                assert after_val is None or int(after_val) == 0, (
                    f"Left cell para has w:spacing w:after={after_val} — "
                    "must be stripped so rows don't add spurious vertical gaps"
                )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_cells_valign_top():
    """Both left and right cells must carry w:vAlign w:val='top'.

    Explicit top-alignment prevents style-sheet inheritance from overriding
    the default and ensures date text and role-header text both anchor to the
    top of their shared auto-height row.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    for seg in (seg1, seg2):
        if seg is None:
            continue
        for tr in seg.findall(f"{{{_W_NS}}}tr"):
            for tc in tr.findall(f"{{{_W_NS}}}tc"):
                tcPr = tc.find(f"{{{_W_NS}}}tcPr")
                assert tcPr is not None, "Every tc must have a tcPr"
                vAlign = tcPr.find(f"{{{_W_NS}}}vAlign")
                assert vAlign is not None, (
                    "tcPr must have w:vAlign for explicit top-alignment"
                )
                assert vAlign.get(f"{{{_W_NS}}}val") == "top", (
                    f"w:vAlign must be 'top', got {vAlign.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')!r}"
                )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_right_cell_first_para_no_before():
    """First paragraph in each right cell must have w:spacing w:before stripped.

    In the newspaper-column template, w:before on the first paragraph after a
    column break has no visual effect (column break resets vertical position).
    Inside a table cell it renders as literal whitespace above the role title,
    inflating row heights and spreading content across extra pages.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    for seg in (seg1, seg2):
        if seg is None:
            continue
        for tr in seg.findall(f"{{{_W_NS}}}tr"):
            cells = tr.findall(f"{{{_W_NS}}}tc")
            if len(cells) < 2:
                continue
            tc_right = cells[1]  # second cell = right/role-content column
            paras = tc_right.findall(f"{{{_W_NS}}}p")
            if not paras:
                continue
            first_para = paras[0]
            pPr = first_para.find(f"{{{_W_NS}}}pPr")
            if pPr is None:
                continue
            sp = pPr.find(f"{{{_W_NS}}}spacing")
            if sp is None:
                continue
            before_val = sp.get(f"{{{_W_NS}}}before")
            assert before_val is None or int(before_val) == 0, (
                f"First right-cell para has w:spacing w:before={before_val} — "
                "must be stripped: in a table cell this adds whitespace above the "
                "role title, inflating row height and page count"
            )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_right_cell_all_paras_no_before():
    """All paragraphs in each right cell must have w:spacing w:before stripped.

    Multi-line role headers carry w:before on ALL header paragraphs, not just
    para[0], so all right-cell paras must be stripped.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    for seg_idx, seg in enumerate((seg1, seg2)):
        if seg is None:
            continue
        for row_idx, tr in enumerate(seg.findall(f"{{{_W_NS}}}tr")):
            cells = tr.findall(f"{{{_W_NS}}}tc")
            if len(cells) < 2:
                continue
            tc_right = cells[1]
            for para_idx, para in enumerate(tc_right.findall(f"{{{_W_NS}}}p")):
                pPr = para.find(f"{{{_W_NS}}}pPr")
                if pPr is None:
                    continue
                sp = pPr.find(f"{{{_W_NS}}}spacing")
                if sp is None:
                    continue
                before_val = sp.get(f"{{{_W_NS}}}before")
                assert before_val is None or int(before_val) == 0, (
                    f"seg{seg_idx} row{row_idx} para{para_idx}: "
                    f"w:spacing w:before={before_val} must be stripped"
                )


# ---------------------------------------------------------------------------
# Role header date-stripping: timeline_left_role_right roles must not have
# date ranges duplicated in the right-cell header (dates live in left cell)
# ---------------------------------------------------------------------------

import re as _re
_DATE_PATTERN = _re.compile(
    r"\b(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\b|\b(20[12]\d|19\d\d)\b",
    _re.IGNORECASE,
)


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_right_cell_headers_no_date_range():
    """For timeline_left_role_right roles, the right-cell first paragraph
    (role header) must not contain a date range.

    Dates are displayed only in the left table cell.  Before the fix the
    updater's date-injection block appended ' | November 2022 – March 2025'
    directly to the role title, duplicating the date from the left column.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    for seg_idx, seg in enumerate((seg1, seg2)):
        if seg is None:
            continue
        for row_idx, tr in enumerate(seg.findall(f"{{{_W_NS}}}tr")):
            cells = tr.findall(f"{{{_W_NS}}}tc")
            if len(cells) < 2:
                continue
            right_paras = cells[1].findall(f"{{{_W_NS}}}p")
            if not right_paras:
                continue
            header_text = "".join(
                t.text or "" for t in right_paras[0].iter(f"{{{_W_NS}}}t")
            )
            assert not _DATE_PATTERN.search(header_text), (
                f"seg{seg_idx} row{row_idx}: right-cell role header contains a date "
                f"range — dates must only appear in the left cell.\n"
                f"  header text: {header_text!r}"
            )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_left_cells_still_have_dates():
    """Left-cell paragraphs must still contain date text after the header fix.

    Stripping dates from the right-cell header must not also clear them from
    the left cell.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    _, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    rows_with_dates = 0
    for seg in (seg1, seg2):
        if seg is None:
            continue
        for tr in seg.findall(f"{{{_W_NS}}}tr"):
            cells = tr.findall(f"{{{_W_NS}}}tc")
            if not cells:
                continue
            left_text = "".join(
                t.text or "" for t in cells[0].iter(f"{{{_W_NS}}}t")
            )
            if _DATE_PATTERN.search(left_text):
                rows_with_dates += 1

    assert rows_with_dates >= 4, (
        f"Expected at least 4 table rows with dates in the left cell, "
        f"got {rows_with_dates} — dates may have been incorrectly cleared"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 not found")
def test_s35_v2_role_count_unchanged():
    """Role count in the timeline table must match the original experience roles.

    The date-header fix must not add or remove table rows.
    """
    from docx import Document as DocxDoc
    from tailor.compiler.docx_renderer import _build_para_lookup, _build_timeline_segments

    _W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    original, updated = _load_s35_updated()
    para_lookup = _build_para_lookup(updated)
    out_doc = DocxDoc()
    sectPr = out_doc.element.body.find(f"{{{_W_NS}}}sectPr")

    seg1, seg2, _, _ = _build_timeline_segments(
        updated, para_lookup, list(updated.layout_blocks or []), None, None, sectPr
    )
    total_rows = sum(
        len(seg.findall(f"{{{_W_NS}}}tr"))
        for seg in (seg1, seg2)
        if seg is not None
    )
    orig_role_count = sum(len(sec.roles or []) for sec in original.sections)
    assert total_rows == orig_role_count, (
        f"Table row count {total_rows} != original role count {orig_role_count} "
        "— the date-header fix must not change table structure"
    )

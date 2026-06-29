"""Tests for the compound-split physical paragraph count invariant.

Core rule: semantic decomposition via _try_split_compound_role must not change
physical paragraph count.  The compound paragraph stays in body_items; only its
para_id and style are updated.  meta_pm and body_pm become semantic-only (unbound).

Covers:
1. Sample 35 layout_blocks count == source DOCX body paragraph count (no inflation).
2. Each compound para in body_items carries title_pm's para_id.
3. meta_pm and body_pm para_ids are not in layout_blocks (unbound).
4. _build_para_lookup maps compound para_id → title_pm (correct rendering target).
5. After apply_tailored, compound role header reflects LLM-updated text.
6. Samples 34 and 36 have no compound splits and correct layout_blocks counts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_GEN_DIR = Path(__file__).parent / "samples" / "generation"

_S35_DOCX = str(_DOCX_DIR / "35-Gleb_Zernov_Resume.docx")
_S34_DOCX = str(_DOCX_DIR / "34-Valerii_Konchin_CV.docx")
_S36_DOCX = str(_DOCX_DIR / "36-Asia_Dalakova.docx")
_S35_GEN = str(
    _GEN_DIR
    / "35-Gleb_Zernov-American_Tire_Distributors-Lead_Software_Engineer-444-20260609-204948.json"
)


def _source_body_para_count(docx_path: str) -> int:
    """Count w:p elements at the direct body level in the source DOCX."""
    from zipfile import ZipFile
    from lxml import etree
    _W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    with ZipFile(docx_path) as z:
        xml = z.read("word/document.xml")
    tree = etree.fromstring(xml)
    body = tree.find(f".//{{{_W}}}body")
    return sum(1 for c in body if c.tag == f"{{{_W}}}p")


def _compound_pms(doc):
    """Return all compound-split ParaModels in doc.body_items."""
    from tailor.compiler.models import TableBlock
    return [
        pm for pm in (doc.body_items or [])
        if not isinstance(pm, TableBlock) and hasattr(pm, "_compound_parts")
    ]


@pytest.fixture(scope="module")
def doc35():
    from tailor.compiler.docx_parser import parse_docx
    return parse_docx(_S35_DOCX)


# ---------------------------------------------------------------------------
# 1. Physical paragraph count invariant
# ---------------------------------------------------------------------------

def test_s35_layout_blocks_count_matches_source(doc35):
    """layout_blocks count must equal source body paragraph count (no inflation)."""
    src_count = _source_body_para_count(_S35_DOCX)
    lb_count = len(doc35.layout_blocks)
    assert lb_count == src_count, (
        f"layout_blocks count {lb_count} != source body para count {src_count}; "
        "compound-split expansion must not inflate physical paragraph count"
    )


# ---------------------------------------------------------------------------
# 2. Compound para carries title_pm's para_id
# ---------------------------------------------------------------------------

def test_s35_compound_paras_present_in_body_items(doc35):
    """Sample 35 must contain compound-split paragraphs (regression guard)."""
    assert _compound_pms(doc35), "Sample 35 must have at least one compound-split paragraph"


def test_s35_compound_paras_carry_title_pm_id(doc35):
    """Each compound para must carry title_pm's para_id, not an empty string."""
    for pm in _compound_pms(doc35):
        title_pm = pm._compound_parts[0]
        assert pm.para_id != "", (
            "Compound para must have a non-empty para_id after fix"
        )
        assert pm.para_id == title_pm.para_id, (
            f"Compound para para_id {pm.para_id!r} != title_pm.para_id {title_pm.para_id!r}"
        )


# ---------------------------------------------------------------------------
# 3. meta_pm and body_pm are unbound (not in layout_blocks)
# ---------------------------------------------------------------------------

def test_s35_meta_pm_is_unbound(doc35):
    """meta_pm para_ids must not appear in layout_blocks (semantic-only)."""
    lb_ids = {lb.para_id for lb in doc35.layout_blocks if hasattr(lb, "para_id")}
    for pm in _compound_pms(doc35):
        _, meta_pm, _ = pm._compound_parts
        assert meta_pm.para_id not in lb_ids, (
            f"meta_pm {meta_pm.para_id!r} must not have a layout block (unbound)"
        )


def test_s35_body_pm_is_unbound(doc35):
    """body_pm para_ids must not appear in layout_blocks (semantic-only)."""
    lb_ids = {lb.para_id for lb in doc35.layout_blocks if hasattr(lb, "para_id")}
    for pm in _compound_pms(doc35):
        _, _, body_pm = pm._compound_parts
        assert body_pm.para_id not in lb_ids, (
            f"body_pm {body_pm.para_id!r} must not have a layout block (unbound)"
        )


# ---------------------------------------------------------------------------
# 4. _build_para_lookup maps compound para_id → title_pm
# ---------------------------------------------------------------------------

def test_s35_para_lookup_maps_compound_id_to_title_pm(doc35):
    """para_lookup[compound.para_id] must return title_pm, not the compound para."""
    from tailor.compiler.docx_renderer import _build_para_lookup
    lookup = _build_para_lookup(doc35)
    for pm in _compound_pms(doc35):
        title_pm = pm._compound_parts[0]
        resolved = lookup.get(pm.para_id)
        assert resolved is not None, (
            f"para_lookup has no entry for compound para_id {pm.para_id!r}"
        )
        assert resolved is title_pm, (
            f"para_lookup[{pm.para_id!r}] returned {resolved!r}, expected title_pm"
        )


# ---------------------------------------------------------------------------
# 5. After apply_tailored, compound role header has LLM-updated text
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path(_S35_GEN).exists(), reason="Generation JSON not present")
def test_s35_compound_role_header_updated_after_tailoring(doc35):
    """After apply_tailored, the experience role for Stellar must carry
    LLM-updated header text, confirming the rendering target is correct."""
    with open(_S35_GEN, encoding="utf-8") as f:
        gen_data = json.load(f)
    llm_text = gen_data["llm_response"]["resume"]

    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.updater import apply_tailored

    llm_secs = parse_llm_output(llm_text)
    updated = apply_tailored(doc35, llm_secs)

    exp_secs = [s for s in updated.sections if s.semantic_type == "experience"]
    all_role_headers = [r.header.text for s in exp_secs for r in s.roles]
    stellar_headers = [h for h in all_role_headers if "Stellar" in h]
    assert stellar_headers, (
        "Expected a role header containing 'Stellar' after apply_tailored. "
        f"All experience role headers: {all_role_headers}"
    )


@pytest.mark.skipif(not Path(_S35_GEN).exists(), reason="Generation JSON not present")
def test_s35_compound_para_id_in_layout_blocks_after_tailoring(doc35):
    """After apply_tailored, compound para_ids must still be in layout_blocks,
    confirming the compound para has a valid rendering position."""
    with open(_S35_GEN, encoding="utf-8") as f:
        gen_data = json.load(f)
    llm_text = gen_data["llm_response"]["resume"]

    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.updater import apply_tailored

    llm_secs = parse_llm_output(llm_text)
    updated = apply_tailored(doc35, llm_secs)

    lb_ids = {lb.para_id for lb in (updated.layout_blocks or []) if hasattr(lb, "para_id")}
    for pm in _compound_pms(doc35):
        assert pm.para_id in lb_ids, (
            f"Compound para {pm.para_id!r} not in updated layout_blocks — "
            "rendering position lost after apply_tailored"
        )


# ---------------------------------------------------------------------------
# 6. Non-regression: samples 34 and 36
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not Path(_S34_DOCX).exists(), reason="Sample 34 not present")
def test_s34_no_compound_splits():
    """Sample 34 must have no compound-split paragraphs."""
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(_S34_DOCX)
    assert not _compound_pms(doc), "Sample 34 must not trigger compound splitting"


@pytest.mark.skipif(not Path(_S34_DOCX).exists(), reason="Sample 34 not present")
def test_s34_layout_blocks_matches_source():
    """Sample 34 layout_blocks count must equal its source paragraph count."""
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(_S34_DOCX)
    src_count = _source_body_para_count(_S34_DOCX)
    assert len(doc.layout_blocks) == src_count, (
        f"Sample 34: layout_blocks {len(doc.layout_blocks)} != source count {src_count}"
    )


@pytest.mark.skipif(not Path(_S36_DOCX).exists(), reason="Sample 36 not present")
def test_s36_no_compound_splits():
    """Sample 36 must have no compound-split paragraphs."""
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(_S36_DOCX)
    assert not _compound_pms(doc), "Sample 36 must not trigger compound splitting"


@pytest.mark.skipif(not Path(_S36_DOCX).exists(), reason="Sample 36 not present")
def test_s36_layout_blocks_matches_source():
    """Sample 36 layout_blocks count must equal its source paragraph count."""
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(_S36_DOCX)
    src_count = _source_body_para_count(_S36_DOCX)
    assert len(doc.layout_blocks) == src_count, (
        f"Sample 36: layout_blocks {len(doc.layout_blocks)} != source count {src_count}"
    )


# ---------------------------------------------------------------------------
# 7. Floating textbox text exclusion (Issue 7a)
# ---------------------------------------------------------------------------

def test_s35_no_doubled_name_in_header_paras(doc35):
    """Floating textbox content must not duplicate paragraph text.

    Sample 35 has a <w:drawing> anchor textbox containing 'Gleb Zernov'.
    The same paragraph also has contact info runs.  _get_para_text must
    exclude the textbox text so the name does not appear doubled.
    """
    contact_para = next(
        (pm for pm in doc35.header_paras if pm.text.strip()),
        None,
    )
    assert contact_para is not None, "Expected at least one non-empty header para"
    text = contact_para.text
    # The name 'Gleb Zernov' must appear at most once
    assert text.count("Gleb Zernov") <= 1, (
        f"Name duplicated in header para: {text!r}"
    )
    # The contact paragraph must not contain 'Gleb ZernovGleb Zernov' (doubled)
    assert "Gleb ZernovGleb Zernov" not in text, (
        f"Doubled name detected in header para: {text!r}"
    )


# ---------------------------------------------------------------------------
# 8. Narrow date-column metadata attachment (Issue 1)
# ---------------------------------------------------------------------------

def test_s35_experience_roles_have_date_col_text(doc35):
    """All experience roles in Sample 35 must carry _date_col_text metadata."""
    from tailor.compiler.docx_parser import parse_docx
    exp_secs = [s for s in doc35.sections if s.semantic_type == "experience"]
    assert exp_secs, "Sample 35 must have an experience section"
    exp_roles = [r for s in exp_secs for r in (s.roles or [])]
    assert exp_roles, "Experience section must have roles"

    roles_with_dates = [r for r in exp_roles if getattr(r, "_date_col_text", None)]
    assert roles_with_dates, (
        "No experience roles have _date_col_text; metadata attachment did not run"
    )
    # Majority of roles should have date text (allow one unmatched at end)
    assert len(roles_with_dates) >= len(exp_roles) - 1, (
        f"Only {len(roles_with_dates)}/{len(exp_roles)} roles have _date_col_text"
    )


def test_s35_date_col_text_contains_year(doc35):
    """_date_col_text for each matched role must contain a 4-digit year."""
    import re
    exp_secs = [s for s in doc35.sections if s.semantic_type == "experience"]
    exp_roles = [r for s in exp_secs for r in (s.roles or [])]
    for role in exp_roles:
        dct = getattr(role, "_date_col_text", None)
        if dct is not None:
            assert re.search(r"(19|20)\d{2}", dct), (
                f"_date_col_text for role {role.header.text[:30]!r} "
                f"contains no year: {dct!r}"
            )


def test_s35_date_col_text_not_in_meta_lines(doc35):
    """_date_col_text must be metadata-only: no date text injected into meta_lines."""
    exp_secs = [s for s in doc35.sections if s.semantic_type == "experience"]
    exp_roles = [r for s in exp_secs for r in (s.roles or [])]
    for role in exp_roles:
        dct = getattr(role, "_date_col_text", None)
        if dct:
            meta_texts = [m.text for m in (role.meta_lines or [])]
            for mt in meta_texts:
                assert mt not in dct, (
                    f"Date text from _date_col_text found in meta_lines for "
                    f"role {role.header.text[:30]!r}: {mt!r}"
                )


# ---------------------------------------------------------------------------
# 9. layout_binding — timeline row metadata
# ---------------------------------------------------------------------------

def _exp_roles_with_binding(doc):
    exp_secs = [s for s in doc.sections if s.semantic_type == "experience"]
    return [r for s in exp_secs for r in (s.roles or [])
            if r.layout_binding is not None]


def test_s35_all_experience_roles_have_layout_binding(doc35):
    """Every experience role in Sample 35 must carry layout_binding."""
    exp_secs = [s for s in doc35.sections if s.semantic_type == "experience"]
    exp_roles = [r for s in exp_secs for r in (s.roles or [])]
    assert exp_roles, "No experience roles found"
    roles_without = [r for r in exp_roles if r.layout_binding is None]
    assert not roles_without, (
        f"{len(roles_without)} role(s) missing layout_binding: "
        + str([r.header.text[:40] for r in roles_without])
    )


def test_s35_layout_binding_kind(doc35):
    """All layout_binding dicts must have kind='timeline_left_role_right'."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert lb["kind"] == "timeline_left_role_right", (
            f"Wrong kind {lb['kind']!r} for role {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_row_index_sequential(doc35):
    """row_index values must be 0, 1, 2, ... in role order."""
    bound = _exp_roles_with_binding(doc35)
    for i, role in enumerate(bound):
        assert role.layout_binding["row_index"] == i, (
            f"Role[{i}] row_index={role.layout_binding['row_index']}, expected {i}"
        )


def test_s35_layout_binding_right_anchor_matches_header(doc35):
    """right_anchor_para_id must equal role.header.para_id."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert lb["right_anchor_para_id"] == role.header.para_id, (
            f"right_anchor_para_id {lb['right_anchor_para_id']!r} != "
            f"header.para_id {role.header.para_id!r} for {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_left_para_ids_non_empty(doc35):
    """left_para_ids must be non-empty for every binding."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert lb["left_para_ids"], (
            f"Empty left_para_ids for role {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_left_para_ids_not_in_meta_lines(doc35):
    """left_para_ids must not overlap with meta_lines para_ids (metadata-only)."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        meta_pids = {m.para_id for m in (role.meta_lines or [])}
        overlap = set(lb["left_para_ids"]) & meta_pids
        assert not overlap, (
            f"left_para_ids overlap with meta_lines for {role.header.text[:30]!r}: "
            f"{overlap}"
        )


def test_s35_layout_binding_left_text_contains_year(doc35):
    """left_text in every binding must contain a 4-digit year."""
    import re
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert re.search(r"(19|20)\d{2}", lb["left_text"]), (
            f"binding.left_text has no year for {role.header.text[:30]!r}: "
            f"{lb['left_text']!r}"
        )


def test_s35_layout_binding_left_text_matches_date_col_text(doc35):
    """layout_binding.left_text must equal _date_col_text when both present."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        dct = getattr(role, "_date_col_text", None)
        if dct is not None:
            assert lb["left_text"] == dct, (
                f"binding.left_text {lb['left_text']!r} != _date_col_text {dct!r} "
                f"for {role.header.text[:30]!r}"
            )


def test_s35_layout_binding_column_width_valid(doc35):
    """column_width_twips must be a narrow value (< 2000 twips)."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        cw = lb["column_width_twips"]
        assert cw is not None and cw < 2000, (
            f"column_width_twips {cw!r} is not narrow (<2000) "
            f"for {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_does_not_alter_layout_blocks(doc35):
    """Adding layout_binding must not change layout_blocks count."""
    src_count = _source_body_para_count(_S35_DOCX)
    lb_count = len(doc35.layout_blocks)
    assert lb_count == src_count, (
        f"layout_blocks {lb_count} != source {src_count} after binding attachment"
    )


def test_s34_s36_no_layout_binding():
    """Samples 34 and 36 (no narrow date sidebar) must not get layout_binding."""
    from tailor.compiler.docx_parser import parse_docx
    for label, path in [("s34", _S34_DOCX), ("s36", _S36_DOCX)]:
        if not Path(path).exists():
            continue
        doc = parse_docx(path)
        exp_secs = [s for s in doc.sections if s.semantic_type == "experience"]
        exp_roles = [r for s in exp_secs for r in (s.roles or [])]
        bound = [r for r in exp_roles if r.layout_binding is not None]
        assert not bound, (
            f"{label}: {len(bound)} role(s) unexpectedly have layout_binding"
        )


# ---------------------------------------------------------------------------
# 10. layout_binding — right_para_ids, policy fields, disjointness, serialization
# ---------------------------------------------------------------------------

def test_s35_layout_binding_right_para_ids_starts_with_anchor(doc35):
    """right_para_ids[0] must equal right_anchor_para_id (role header is first)."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert lb["right_para_ids"], (
            f"Empty right_para_ids for role {role.header.text[:30]!r}"
        )
        assert lb["right_para_ids"][0] == lb["right_anchor_para_id"], (
            f"right_para_ids[0]={lb['right_para_ids'][0]!r} != "
            f"right_anchor_para_id={lb['right_anchor_para_id']!r} "
            f"for {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_right_para_ids_match_role_semantic(doc35):
    """right_para_ids must exactly cover the role's semantic paragraph IDs."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        expected = (
            [role.header.para_id]
            + [p.para_id for p in role.header_extra]
            + [p.para_id for p in role.meta_lines]
            + [p.para_id for p in role.bullets]
        )
        assert lb["right_para_ids"] == expected, (
            f"right_para_ids mismatch for {role.header.text[:30]!r}:\n"
            f"  binding: {lb['right_para_ids']}\n"
            f"  expected: {expected}"
        )


def test_s35_layout_binding_left_right_disjoint(doc35):
    """left_para_ids and right_para_ids must be disjoint for every role."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        overlap = set(lb["left_para_ids"]) & set(lb["right_para_ids"])
        assert not overlap, (
            f"left/right para_ids overlap for {role.header.text[:30]!r}: {overlap}"
        )


def test_s35_layout_binding_policy_fields(doc35):
    """preserve_left_verbatim must be True; right_rewrite_policy must be set."""
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        assert lb.get("preserve_left_verbatim") is True, (
            f"preserve_left_verbatim not True for {role.header.text[:30]!r}"
        )
        assert lb.get("right_rewrite_policy") == "rewrite_inner_content_only", (
            f"right_rewrite_policy wrong for {role.header.text[:30]!r}: "
            f"{lb.get('right_rewrite_policy')!r}"
        )


def test_s35_layout_binding_survives_roundtrip(doc35):
    """layout_binding must survive RoleEntry.to_dict() / from_dict() roundtrip."""
    from tailor.compiler.models import RoleEntry
    for role in _exp_roles_with_binding(doc35):
        d = role.to_dict()
        assert "layout_binding" in d, "layout_binding missing from to_dict()"
        assert d["layout_binding"] is not None
        restored = RoleEntry.from_dict(d)
        assert restored.layout_binding == role.layout_binding, (
            f"layout_binding changed after roundtrip for {role.header.text[:30]!r}"
        )


def test_s35_layout_binding_6_roles(doc35):
    """Sample 35 must produce exactly 6 layout bindings — one per experience role."""
    bound = _exp_roles_with_binding(doc35)
    assert len(bound) == 6, (
        f"Expected 6 layout bindings, got {len(bound)}"
    )


def test_s35_layout_binding_left_para_ids_in_header_paras(doc35):
    """All left_para_ids must be present in doc.header_paras."""
    hp_ids = {pm.para_id for pm in doc35.header_paras if pm.para_id}
    for role in _exp_roles_with_binding(doc35):
        lb = role.layout_binding
        missing = [pid for pid in lb["left_para_ids"] if pid not in hp_ids]
        assert not missing, (
            f"left_para_ids not in header_paras for {role.header.text[:30]!r}: "
            f"{missing}"
        )

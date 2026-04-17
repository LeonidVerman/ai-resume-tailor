"""
backend/tests/unit/test_classification_validator.py

Unit tests for the deterministic classification validator and downgrade logic.

Tests validate:
  - Per-section-type contract enforcement
  - Safe downgrade of invalid sections
  - apply_validation_and_downgrade() integration
"""
from __future__ import annotations

import pytest

from tailor.compiler.classification_validator import (
    # error codes under test
    E_DOC_MISSING_ID,
    E_SUMMARY_REWRITE_POLICY,
    E_SUMMARY_PRESERVE_HEADING,
    E_SUMMARY_PRESERVE_BODY,
    E_SUMMARY_ROLES_PRESENT,
    E_SUMMARY_BLOCKS_EMPTY,
    E_SUMMARY_BLOCK_TYPE,
    E_SUMMARY_BLOCK_POLICY,
    E_SUMMARY_BLOCK_NEW,
    E_SKILLS_REWRITE_POLICY,
    E_SKILLS_BLOCK_TYPE,
    E_SKILLS_BLOCK_NEW,
    E_EXP_REWRITE_POLICY,
    E_EXP_PRESERVE_HEADING,
    E_EXP_PRESERVE_BODY,
    E_EXP_BLOCKS_PRESENT,
    E_EXP_ROLES_EMPTY,
    E_EXP_ROLE_HEADER_TYPE,
    E_EXP_ROLE_HEADER_POLICY,
    E_EXP_ROLE_BODY_TYPE,
    E_EXP_ROLE_BODY_POLICY,
    E_EDU_REWRITE_POLICY,
    E_EDU_PRESERVE_HEADING,
    E_EDU_BLOCK_TYPE,
    E_OTHER_REWRITE_POLICY,
    E_OTHER_BLOCK_TYPE,
    # functions under test
    validate_classification,
    downgrade_invalid_sections,
    apply_validation_and_downgrade,
)


# ---------------------------------------------------------------------------
# Fixtures — minimal valid sections
# ---------------------------------------------------------------------------

def _summary_section(section_id="sec_summary"):
    return {
        "section_id": section_id,
        "raw_title": "SUMMARY",
        "display_title": "Professional Summary",
        "semantic_type": "summary",
        "rewrite_policy": "rewrite_body",
        "preserve_heading": False,
        "preserve_body_structure": False,
        "blocks": [
            {"block_id": "blk_001", "para_id": "p1", "semantic_type": "summary_paragraph",
             "rewrite_policy": "rewrite_text", "new": False},
        ],
        "roles": [],
    }


def _skills_section(section_id="sec_skills"):
    return {
        "section_id": section_id,
        "raw_title": "SKILLS",
        "display_title": "Technical Skills",
        "semantic_type": "skills",
        "rewrite_policy": "rewrite_body",
        "preserve_heading": False,
        "preserve_body_structure": False,
        "blocks": [
            {"block_id": "blk_001", "para_id": "p1", "semantic_type": "skills_paragraph",
             "rewrite_policy": "rewrite_text", "new": False},
        ],
        "roles": [],
    }


def _experience_section(section_id="sec_exp"):
    return {
        "section_id": section_id,
        "raw_title": "EXPERIENCE",
        "display_title": "Experience",
        "semantic_type": "experience",
        "rewrite_policy": "rewrite_bullets_only",
        "preserve_heading": True,
        "preserve_body_structure": True,
        "blocks": [],
        "roles": [
            {
                "role_id": "role_001",
                "header_blocks": [
                    {"block_id": "blk_h1", "para_id": "ph1",
                     "semantic_type": "role_header", "rewrite_policy": "preserve", "new": False},
                ],
                "meta_blocks": [
                    {"block_id": "blk_m1", "para_id": "pm1",
                     "semantic_type": "role_meta", "rewrite_policy": "preserve", "new": False},
                ],
                "body_blocks": [
                    {"block_id": "blk_b1", "para_id": "pb1",
                     "semantic_type": "bullet", "rewrite_policy": "rewrite_text", "new": False},
                ],
            }
        ],
    }


def _education_section(section_id="sec_edu"):
    return {
        "section_id": section_id,
        "raw_title": "EDUCATION",
        "display_title": "Education",
        "semantic_type": "education",
        "rewrite_policy": "preserve",
        "preserve_heading": True,
        "preserve_body_structure": True,
        "blocks": [
            {"block_id": "blk_001", "para_id": "p1", "semantic_type": "education_entry",
             "rewrite_policy": "preserve", "new": False},
        ],
        "roles": [],
    }


def _other_section(section_id="sec_other"):
    return {
        "section_id": section_id,
        "raw_title": "HOBBIES",
        "display_title": "Hobbies",
        "semantic_type": "other",
        "rewrite_policy": "preserve",
        "preserve_heading": True,
        "preserve_body_structure": False,
        "blocks": [
            {"block_id": "blk_001", "para_id": "p1", "semantic_type": "other_paragraph",
             "rewrite_policy": "preserve", "new": False},
        ],
        "roles": [],
    }


def _doc(sections):
    return {
        "document_id": "42",
        "classification_version": "1.0",
        "source_kind": "docx",
        "sections": sections,
    }


def _error_codes(result) -> list[str]:
    return [e["code"] for e in result["errors"]]


def _section_result(result, section_id):
    for sr in result["section_results"]:
        if sr["section_id"] == section_id:
            return sr
    return None


# ---------------------------------------------------------------------------
# Global validation
# ---------------------------------------------------------------------------

def test_valid_document_is_valid():
    doc = _doc([_summary_section(), _experience_section()])
    r = validate_classification(doc)
    assert r["is_valid"] is True
    assert r["errors"] == []


def test_missing_document_id():
    doc = _doc([_other_section()])
    doc["document_id"] = ""
    r = validate_classification(doc)
    assert not r["is_valid"]
    assert E_DOC_MISSING_ID in _error_codes(r)


# ---------------------------------------------------------------------------
# Summary contract
# ---------------------------------------------------------------------------

def test_summary_valid():
    r = validate_classification(_doc([_summary_section()]))
    assert r["is_valid"] is True


def test_summary_wrong_rewrite_policy():
    sec = _summary_section()
    sec["rewrite_policy"] = "preserve"
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_REWRITE_POLICY in _error_codes(r)


def test_summary_preserve_heading_must_be_false():
    sec = _summary_section()
    sec["preserve_heading"] = True
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_PRESERVE_HEADING in _error_codes(r)


def test_summary_preserve_body_must_be_false():
    sec = _summary_section()
    sec["preserve_body_structure"] = True
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_PRESERVE_BODY in _error_codes(r)


def test_summary_roles_present():
    sec = _summary_section()
    sec["roles"] = [{"role_id": "r1", "header_blocks": [], "meta_blocks": [], "body_blocks": []}]
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_ROLES_PRESENT in _error_codes(r)


def test_summary_blocks_empty():
    sec = _summary_section()
    sec["blocks"] = []
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_BLOCKS_EMPTY in _error_codes(r)


def test_summary_wrong_block_type():
    sec = _summary_section()
    sec["blocks"][0]["semantic_type"] = "other_paragraph"
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_BLOCK_TYPE in _error_codes(r)


def test_summary_wrong_block_policy():
    sec = _summary_section()
    sec["blocks"][0]["rewrite_policy"] = "preserve"
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_BLOCK_POLICY in _error_codes(r)


def test_summary_block_new_true():
    sec = _summary_section()
    sec["blocks"][0]["new"] = True
    r = validate_classification(_doc([sec]))
    assert E_SUMMARY_BLOCK_NEW in _error_codes(r)


# ---------------------------------------------------------------------------
# Skills contract
# ---------------------------------------------------------------------------

def test_skills_valid():
    r = validate_classification(_doc([_skills_section()]))
    assert r["is_valid"] is True


def test_skills_wrong_policy():
    sec = _skills_section()
    sec["rewrite_policy"] = "preserve"
    r = validate_classification(_doc([sec]))
    assert E_SKILLS_REWRITE_POLICY in _error_codes(r)


def test_skills_wrong_block_type():
    sec = _skills_section()
    sec["blocks"][0]["semantic_type"] = "other_paragraph"
    r = validate_classification(_doc([sec]))
    assert E_SKILLS_BLOCK_TYPE in _error_codes(r)


def test_skills_block_new_must_be_false():
    sec = _skills_section()
    sec["blocks"][0]["new"] = True
    r = validate_classification(_doc([sec]))
    assert E_SKILLS_BLOCK_NEW in _error_codes(r)


# ---------------------------------------------------------------------------
# Experience contract
# ---------------------------------------------------------------------------

def test_experience_valid():
    r = validate_classification(_doc([_experience_section()]))
    assert r["is_valid"] is True


def test_experience_wrong_rewrite_policy():
    sec = _experience_section()
    sec["rewrite_policy"] = "rewrite_body"
    r = validate_classification(_doc([sec]))
    assert E_EXP_REWRITE_POLICY in _error_codes(r)


def test_experience_preserve_heading_must_be_true():
    sec = _experience_section()
    sec["preserve_heading"] = False
    r = validate_classification(_doc([sec]))
    assert E_EXP_PRESERVE_HEADING in _error_codes(r)


def test_experience_preserve_body_must_be_true():
    sec = _experience_section()
    sec["preserve_body_structure"] = False
    r = validate_classification(_doc([sec]))
    assert E_EXP_PRESERVE_BODY in _error_codes(r)


def test_experience_blocks_present():
    sec = _experience_section()
    sec["blocks"] = [{"block_id": "b1", "para_id": "p1",
                      "semantic_type": "other_paragraph", "rewrite_policy": "preserve", "new": False}]
    r = validate_classification(_doc([sec]))
    assert E_EXP_BLOCKS_PRESENT in _error_codes(r)


def test_experience_roles_empty():
    sec = _experience_section()
    sec["roles"] = []
    r = validate_classification(_doc([sec]))
    assert E_EXP_ROLES_EMPTY in _error_codes(r)


def test_experience_role_header_wrong_type():
    sec = _experience_section()
    sec["roles"][0]["header_blocks"][0]["semantic_type"] = "other_paragraph"
    r = validate_classification(_doc([sec]))
    assert E_EXP_ROLE_HEADER_TYPE in _error_codes(r)


def test_experience_role_header_wrong_policy():
    sec = _experience_section()
    sec["roles"][0]["header_blocks"][0]["rewrite_policy"] = "rewrite_text"
    r = validate_classification(_doc([sec]))
    assert E_EXP_ROLE_HEADER_POLICY in _error_codes(r)


def test_experience_role_body_wrong_type():
    sec = _experience_section()
    sec["roles"][0]["body_blocks"][0]["semantic_type"] = "other_paragraph"
    r = validate_classification(_doc([sec]))
    assert E_EXP_ROLE_BODY_TYPE in _error_codes(r)


def test_experience_role_body_wrong_policy():
    sec = _experience_section()
    sec["roles"][0]["body_blocks"][0]["rewrite_policy"] = "preserve"
    r = validate_classification(_doc([sec]))
    assert E_EXP_ROLE_BODY_POLICY in _error_codes(r)


# ---------------------------------------------------------------------------
# Education contract
# ---------------------------------------------------------------------------

def test_education_valid():
    r = validate_classification(_doc([_education_section()]))
    assert r["is_valid"] is True


def test_education_wrong_rewrite_policy():
    sec = _education_section()
    sec["rewrite_policy"] = "rewrite_body"
    r = validate_classification(_doc([sec]))
    assert E_EDU_REWRITE_POLICY in _error_codes(r)


def test_education_preserve_heading_must_be_true():
    sec = _education_section()
    sec["preserve_heading"] = False
    r = validate_classification(_doc([sec]))
    assert E_EDU_PRESERVE_HEADING in _error_codes(r)


def test_education_wrong_block_type():
    sec = _education_section()
    sec["blocks"][0]["semantic_type"] = "bullet"
    r = validate_classification(_doc([sec]))
    assert E_EDU_BLOCK_TYPE in _error_codes(r)


def test_education_other_paragraph_allowed():
    sec = _education_section()
    sec["blocks"][0]["semantic_type"] = "other_paragraph"
    r = validate_classification(_doc([sec]))
    assert r["is_valid"] is True


# ---------------------------------------------------------------------------
# Other contract
# ---------------------------------------------------------------------------

def test_other_valid():
    r = validate_classification(_doc([_other_section()]))
    assert r["is_valid"] is True


def test_other_wrong_rewrite_policy():
    sec = _other_section()
    sec["rewrite_policy"] = "rewrite_body"
    r = validate_classification(_doc([sec]))
    assert E_OTHER_REWRITE_POLICY in _error_codes(r)


def test_other_wrong_block_type():
    sec = _other_section()
    sec["blocks"][0]["semantic_type"] = "bullet"
    r = validate_classification(_doc([sec]))
    assert E_OTHER_BLOCK_TYPE in _error_codes(r)


# ---------------------------------------------------------------------------
# section_results structure
# ---------------------------------------------------------------------------

def test_section_results_valid_section():
    r = validate_classification(_doc([_other_section("sec_x")]))
    sr = _section_result(r, "sec_x")
    assert sr is not None
    assert sr["is_valid"] is True
    assert sr["error_codes"] == []


def test_section_results_invalid_section():
    sec = _other_section("sec_bad")
    sec["rewrite_policy"] = "rewrite_body"
    r = validate_classification(_doc([sec]))
    sr = _section_result(r, "sec_bad")
    assert sr is not None
    assert sr["is_valid"] is False
    assert E_OTHER_REWRITE_POLICY in sr["error_codes"]


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

def test_downgrade_non_experience_section():
    sec = _summary_section("sec_sum")
    sec["rewrite_policy"] = "preserve"  # make it invalid
    doc = _doc([sec])
    result = downgrade_invalid_sections(doc, {"sec_sum"})

    dgraded = result["sections"][0]
    assert dgraded["semantic_type"] == "other"
    assert dgraded["rewrite_policy"] == "preserve"
    assert dgraded["preserve_heading"] is True
    assert dgraded["preserve_body_structure"] is True
    assert dgraded["roles"] == []
    assert dgraded["section_id"] == "sec_sum"
    assert dgraded["raw_title"] == sec["raw_title"]
    # original para_id preserved
    assert dgraded["blocks"][0]["para_id"] == "p1"
    assert dgraded["blocks"][0]["semantic_type"] == "other_paragraph"
    assert dgraded["blocks"][0]["rewrite_policy"] == "preserve"
    assert dgraded["blocks"][0]["new"] is False


def test_downgrade_experience_section_flattens_roles():
    sec = _experience_section("sec_exp")
    sec["roles"] = []  # make it invalid
    doc = _doc([_experience_section("sec_exp")])
    result = downgrade_invalid_sections(doc, {"sec_exp"})

    dgraded = result["sections"][0]
    assert dgraded["semantic_type"] == "other"
    assert dgraded["roles"] == []
    # para_ids from all role block groups should appear as blocks
    para_ids = {b["para_id"] for b in dgraded["blocks"]}
    assert "ph1" in para_ids  # header block
    assert "pm1" in para_ids  # meta block
    assert "pb1" in para_ids  # body block


def test_downgrade_preserves_block_id():
    sec = _summary_section("sec_sum")
    sec["rewrite_policy"] = "preserve"
    doc = _doc([sec])
    result = downgrade_invalid_sections(doc, {"sec_sum"})
    dgraded = result["sections"][0]
    assert dgraded["blocks"][0]["block_id"] == "blk_001"


def test_downgrade_no_invalid_ids_returns_original():
    doc = _doc([_summary_section()])
    result = downgrade_invalid_sections(doc, set())
    # must be same object (no copy made)
    assert result is doc


def test_downgrade_does_not_touch_valid_sections():
    valid_sec = _other_section("sec_ok")
    invalid_sec = _summary_section("sec_bad")
    invalid_sec["rewrite_policy"] = "preserve"
    doc = _doc([valid_sec, invalid_sec])
    result = downgrade_invalid_sections(doc, {"sec_bad"})
    # valid section unchanged
    assert result["sections"][0] is valid_sec
    # invalid section replaced
    assert result["sections"][1]["semantic_type"] == "other"


# ---------------------------------------------------------------------------
# apply_validation_and_downgrade
# ---------------------------------------------------------------------------

def test_apply_all_valid_status():
    doc = _doc([_other_section()])
    _, _, meta = apply_validation_and_downgrade(doc)
    assert meta["status"] == "valid"
    assert meta["recovery_applied"] is False
    assert meta["invalid_section_ids"] == []
    assert meta["validation_error_count"] == 0


def test_apply_invalid_section_status_downgraded():
    sec = _summary_section("sec_bad")
    sec["rewrite_policy"] = "preserve"
    doc = _doc([sec])
    validation, final, meta = apply_validation_and_downgrade(doc)
    assert meta["status"] == "downgraded"
    assert meta["recovery_applied"] is True
    assert "sec_bad" in meta["invalid_section_ids"]
    assert meta["validation_error_count"] > 0
    # final classification has downgraded section
    assert final["sections"][0]["semantic_type"] == "other"
    # raw_classification in validation result has original
    assert validation["is_valid"] is False


def test_apply_mixed_valid_and_invalid():
    valid_sec = _other_section("sec_ok")
    invalid_sec = _summary_section("sec_bad")
    invalid_sec["rewrite_policy"] = "preserve"
    doc = _doc([valid_sec, invalid_sec])
    validation, final, meta = apply_validation_and_downgrade(doc)
    assert meta["status"] == "downgraded"
    assert meta["invalid_section_ids"] == ["sec_bad"]
    # valid section untouched in final
    assert final["sections"][0] is valid_sec
    # invalid section downgraded in final
    assert final["sections"][1]["semantic_type"] == "other"
    # validation correctly marks only sec_bad as invalid
    sr_ok = _section_result(validation, "sec_ok")
    sr_bad = _section_result(validation, "sec_bad")
    assert sr_ok["is_valid"] is True
    assert sr_bad["is_valid"] is False


def test_apply_preserves_top_level_fields():
    doc = _doc([_other_section()])
    _, final, _ = apply_validation_and_downgrade(doc)
    assert final["document_id"] == "42"
    assert final["source_kind"] == "docx"
    assert final["classification_version"] == "1.0"

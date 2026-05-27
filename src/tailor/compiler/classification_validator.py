"""
src/tailor/compiler/classification_validator.py

Deterministic validator and safe-downgrade for LLM classification output.

Phase 2a — no LLM retry.

Two public functions:

  validate_classification(raw: dict) -> dict
      Validate a raw classification dict against the prompt contract.
      Returns {document_id, is_valid, errors, section_results}.

  apply_validation_and_downgrade(raw: dict) -> (validation, final, status_meta)
      Run validation; downgrade invalid sections; return all three artifacts.

All processing is deterministic and has no side effects.
UTF-8 strings only; no binary content.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Error codes — machine-readable, one per rule
# ---------------------------------------------------------------------------

# Global document checks
E_DOC_MISSING_ID       = "DOC_MISSING_DOCUMENT_ID"
E_DOC_MISSING_KIND     = "DOC_MISSING_SOURCE_KIND"
E_DOC_MISSING_SECTIONS = "DOC_MISSING_SECTIONS"

# Generic section checks
E_SEC_MISSING_ID            = "SEC_MISSING_SECTION_ID"
E_SEC_INVALID_SEMANTIC_TYPE = "SEC_INVALID_SEMANTIC_TYPE"

# Summary
E_SUMMARY_REWRITE_POLICY = "SUMMARY_REWRITE_POLICY"
E_SUMMARY_PRESERVE_HEADING = "SUMMARY_PRESERVE_HEADING"
E_SUMMARY_PRESERVE_BODY = "SUMMARY_PRESERVE_BODY_STRUCTURE"
E_SUMMARY_ROLES_PRESENT = "SUMMARY_ROLES_PRESENT"
E_SUMMARY_BLOCKS_EMPTY = "SUMMARY_BLOCKS_EMPTY"
E_SUMMARY_BLOCK_TYPE = "SUMMARY_BLOCK_TYPE"
E_SUMMARY_BLOCK_POLICY = "SUMMARY_BLOCK_POLICY"
E_SUMMARY_BLOCK_NEW = "SUMMARY_BLOCK_NEW_NOT_FALSE"

# Skills
E_SKILLS_REWRITE_POLICY = "SKILLS_REWRITE_POLICY"
E_SKILLS_PRESERVE_HEADING = "SKILLS_PRESERVE_HEADING"
E_SKILLS_PRESERVE_BODY = "SKILLS_PRESERVE_BODY_STRUCTURE"
E_SKILLS_ROLES_PRESENT = "SKILLS_ROLES_PRESENT"
E_SKILLS_BLOCKS_EMPTY = "SKILLS_BLOCKS_EMPTY"
E_SKILLS_BLOCK_TYPE = "SKILLS_BLOCK_TYPE"
E_SKILLS_BLOCK_POLICY = "SKILLS_BLOCK_POLICY"
E_SKILLS_BLOCK_NEW = "SKILLS_BLOCK_NEW_NOT_FALSE"

# Experience
E_EXP_REWRITE_POLICY = "EXPERIENCE_REWRITE_POLICY"
E_EXP_PRESERVE_HEADING = "EXPERIENCE_PRESERVE_HEADING"
E_EXP_PRESERVE_BODY = "EXPERIENCE_PRESERVE_BODY_STRUCTURE"
E_EXP_BLOCKS_PRESENT = "EXPERIENCE_BLOCKS_PRESENT"
E_EXP_ROLES_EMPTY = "EXPERIENCE_ROLES_EMPTY"
E_EXP_ROLE_HEADER_TYPE = "EXPERIENCE_ROLE_HEADER_BLOCK_TYPE"
E_EXP_ROLE_HEADER_POLICY = "EXPERIENCE_ROLE_HEADER_BLOCK_POLICY"
E_EXP_ROLE_META_TYPE = "EXPERIENCE_ROLE_META_BLOCK_TYPE"
E_EXP_ROLE_META_POLICY = "EXPERIENCE_ROLE_META_BLOCK_POLICY"
E_EXP_ROLE_BODY_TYPE = "EXPERIENCE_ROLE_BODY_BLOCK_TYPE"
E_EXP_ROLE_BODY_POLICY = "EXPERIENCE_ROLE_BODY_BLOCK_POLICY"

# Education
E_EDU_REWRITE_POLICY = "EDUCATION_REWRITE_POLICY"
E_EDU_PRESERVE_HEADING = "EDUCATION_PRESERVE_HEADING"
E_EDU_PRESERVE_BODY = "EDUCATION_PRESERVE_BODY_STRUCTURE"
E_EDU_ROLES_PRESENT = "EDUCATION_ROLES_PRESENT"
E_EDU_BLOCKS_EMPTY = "EDUCATION_BLOCKS_EMPTY"
E_EDU_BLOCK_TYPE = "EDUCATION_BLOCK_TYPE"
E_EDU_BLOCK_POLICY = "EDUCATION_BLOCK_POLICY"

# Projects
E_PROJ_REWRITE_POLICY = "PROJECTS_REWRITE_POLICY"
E_PROJ_ROLES_PRESENT = "PROJECTS_ROLES_PRESENT"
E_PROJ_BLOCKS_EMPTY = "PROJECTS_BLOCKS_EMPTY"
E_PROJ_BLOCK_TYPE = "PROJECTS_BLOCK_TYPE"
E_PROJ_BLOCK_POLICY = "PROJECTS_BLOCK_POLICY"

# Additional
E_ADD_REWRITE_POLICY = "ADDITIONAL_REWRITE_POLICY"
E_ADD_ROLES_PRESENT = "ADDITIONAL_ROLES_PRESENT"
E_ADD_BLOCKS_EMPTY = "ADDITIONAL_BLOCKS_EMPTY"
E_ADD_BLOCK_TYPE = "ADDITIONAL_BLOCK_TYPE"
E_ADD_BLOCK_POLICY = "ADDITIONAL_BLOCK_POLICY"

# Other
E_OTHER_REWRITE_POLICY = "OTHER_REWRITE_POLICY"
E_OTHER_ROLES_PRESENT = "OTHER_ROLES_PRESENT"
E_OTHER_BLOCKS_EMPTY = "OTHER_BLOCKS_EMPTY"
E_OTHER_BLOCK_TYPE = "OTHER_BLOCK_TYPE"
E_OTHER_BLOCK_POLICY = "OTHER_BLOCK_POLICY"


_VALID_SEMANTIC_TYPES: frozenset[str] = frozenset({
    "summary", "skills", "experience", "education",
    "projects", "additional", "other",
})

# Experience role body_block semantic types
_EXP_REWRITEABLE_BODY_TYPES: frozenset[str] = frozenset({
    "bullet", "role_achievement_bullet", "role_responsibility_bullet",
})
_EXP_PRESERVED_BODY_TYPES: frozenset[str] = frozenset({
    "role_intro", "role_highlight", "role_project_label", "role_project_context",
    "role_tech_stack", "role_key_technologies", "role_tools",
    "role_nested_detail", "role_freeform_note",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _err(code: str, section_id: str | None = None, detail: str = "") -> dict:
    return {"code": code, "section_id": section_id, "detail": detail}


def _check_roles_empty(roles: list, section_id: str, code: str) -> list[dict]:
    if roles:
        return [_err(code, section_id, "roles[] must be empty for this section type")]
    return []


def _check_blocks_non_empty(blocks: list, section_id: str, code: str) -> list[dict]:
    if not blocks:
        return [_err(code, section_id, "blocks[] must be non-empty")]
    return []


def _check_blocks_uniform(
    blocks: list,
    section_id: str,
    allowed_types: frozenset[str],
    required_policy: str,
    type_code: str,
    policy_code: str,
    new_must_be_false: bool = False,
    new_code: str = "",
) -> list[dict]:
    errors: list[dict] = []
    for b in blocks:
        bid = b.get("block_id", "?")
        if b.get("semantic_type", "") not in allowed_types:
            errors.append(_err(type_code, section_id,
                               f"block_id={bid} semantic_type={b.get('semantic_type')!r}"))
        if b.get("rewrite_policy", "") != required_policy:
            errors.append(_err(policy_code, section_id,
                               f"block_id={bid} rewrite_policy={b.get('rewrite_policy')!r}"))
        if new_must_be_false and b.get("new", False) is True:
            errors.append(_err(new_code, section_id, f"block_id={bid} new=true"))
    return errors


# ---------------------------------------------------------------------------
# Per-type section validators (each returns list[dict])
# ---------------------------------------------------------------------------

def _validate_summary(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "rewrite_body":
        e.append(_err(E_SUMMARY_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    if s.get("preserve_heading") is not False:
        e.append(_err(E_SUMMARY_PRESERVE_HEADING, sid, "must be false"))
    if s.get("preserve_body_structure") is not False:
        e.append(_err(E_SUMMARY_PRESERVE_BODY, sid, "must be false"))
    e += _check_roles_empty(s.get("roles", []), sid, E_SUMMARY_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_SUMMARY_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"summary_paragraph"}), "rewrite_text",
        E_SUMMARY_BLOCK_TYPE, E_SUMMARY_BLOCK_POLICY,
        new_must_be_false=True, new_code=E_SUMMARY_BLOCK_NEW,
    )
    return e


def _validate_skills(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "rewrite_body":
        e.append(_err(E_SKILLS_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    if s.get("preserve_heading") is not False:
        e.append(_err(E_SKILLS_PRESERVE_HEADING, sid, "must be false"))
    if s.get("preserve_body_structure") is not False:
        e.append(_err(E_SKILLS_PRESERVE_BODY, sid, "must be false"))
    e += _check_roles_empty(s.get("roles", []), sid, E_SKILLS_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_SKILLS_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"skills_paragraph"}), "rewrite_text",
        E_SKILLS_BLOCK_TYPE, E_SKILLS_BLOCK_POLICY,
        new_must_be_false=True, new_code=E_SKILLS_BLOCK_NEW,
    )
    return e


def _validate_experience(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "rewrite_bullets_only":
        e.append(_err(E_EXP_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    if s.get("preserve_heading") is not True:
        e.append(_err(E_EXP_PRESERVE_HEADING, sid, "must be true"))
    if s.get("preserve_body_structure") is not True:
        e.append(_err(E_EXP_PRESERVE_BODY, sid, "must be true"))
    if s.get("blocks"):
        e.append(_err(E_EXP_BLOCKS_PRESENT, sid, "blocks[] must be empty for experience"))
    roles = s.get("roles", [])
    if not roles:
        e.append(_err(E_EXP_ROLES_EMPTY, sid, "roles[] must be non-empty"))
    for role in roles:
        role_id = role.get("role_id", "?")
        for b in role.get("header_blocks", []):
            bid = b.get("block_id", "?")
            if b.get("semantic_type") != "role_header":
                e.append(_err(E_EXP_ROLE_HEADER_TYPE, sid,
                              f"role={role_id} block_id={bid} type={b.get('semantic_type')!r}"))
            if b.get("rewrite_policy") != "preserve":
                e.append(_err(E_EXP_ROLE_HEADER_POLICY, sid,
                              f"role={role_id} block_id={bid}"))
        for b in role.get("meta_blocks", []):
            bid = b.get("block_id", "?")
            if b.get("semantic_type") != "role_meta":
                e.append(_err(E_EXP_ROLE_META_TYPE, sid,
                              f"role={role_id} block_id={bid} type={b.get('semantic_type')!r}"))
            if b.get("rewrite_policy") != "preserve":
                e.append(_err(E_EXP_ROLE_META_POLICY, sid,
                              f"role={role_id} block_id={bid}"))
        for b in role.get("body_blocks", []):
            bid = b.get("block_id", "?")
            st = b.get("semantic_type", "")
            rp = b.get("rewrite_policy", "")
            if st in _EXP_REWRITEABLE_BODY_TYPES:
                if rp != "rewrite_text":
                    e.append(_err(E_EXP_ROLE_BODY_POLICY, sid,
                                  f"role={role_id} block_id={bid} rewriteable type={st!r} "
                                  f"requires rewrite_text, got {rp!r}"))
            elif st in _EXP_PRESERVED_BODY_TYPES:
                if rp != "preserve":
                    e.append(_err(E_EXP_ROLE_BODY_POLICY, sid,
                                  f"role={role_id} block_id={bid} preserved type={st!r} "
                                  f"requires preserve, got {rp!r}"))
            else:
                e.append(_err(E_EXP_ROLE_BODY_TYPE, sid,
                              f"role={role_id} block_id={bid} type={st!r}"))
    return e


def _validate_education(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "preserve":
        e.append(_err(E_EDU_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    if s.get("preserve_heading") is not True:
        e.append(_err(E_EDU_PRESERVE_HEADING, sid, "must be true"))
    if s.get("preserve_body_structure") is not True:
        e.append(_err(E_EDU_PRESERVE_BODY, sid, "must be true"))
    e += _check_roles_empty(s.get("roles", []), sid, E_EDU_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_EDU_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"education_entry"}), "preserve",
        E_EDU_BLOCK_TYPE, E_EDU_BLOCK_POLICY,
    )
    return e


def _validate_projects(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "preserve":
        e.append(_err(E_PROJ_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    e += _check_roles_empty(s.get("roles", []), sid, E_PROJ_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_PROJ_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"project_entry", "other_paragraph"}), "preserve",
        E_PROJ_BLOCK_TYPE, E_PROJ_BLOCK_POLICY,
    )
    return e


def _validate_additional(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "preserve":
        e.append(_err(E_ADD_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    e += _check_roles_empty(s.get("roles", []), sid, E_ADD_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_ADD_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"additional_item", "other_paragraph"}), "preserve",
        E_ADD_BLOCK_TYPE, E_ADD_BLOCK_POLICY,
    )
    return e


def _validate_other(s: dict, sid: str) -> list[dict]:
    e: list[dict] = []
    if s.get("rewrite_policy") != "preserve":
        e.append(_err(E_OTHER_REWRITE_POLICY, sid, f"got {s.get('rewrite_policy')!r}"))
    e += _check_roles_empty(s.get("roles", []), sid, E_OTHER_ROLES_PRESENT)
    e += _check_blocks_non_empty(s.get("blocks", []), sid, E_OTHER_BLOCKS_EMPTY)
    e += _check_blocks_uniform(
        s.get("blocks", []), sid,
        frozenset({"other_paragraph"}), "preserve",
        E_OTHER_BLOCK_TYPE, E_OTHER_BLOCK_POLICY,
    )
    return e


_SECTION_VALIDATORS = {
    "summary":    _validate_summary,
    "skills":     _validate_skills,
    "experience": _validate_experience,
    "education":  _validate_education,
    "projects":   _validate_projects,
    "additional": _validate_additional,
    "other":      _validate_other,
}


# ---------------------------------------------------------------------------
# Public: validate
# ---------------------------------------------------------------------------

def validate_classification(raw: dict) -> dict:
    """Validate a raw classification dict against the prompt contract.

    Parameters
    ----------
    raw:
        Dict parsed from the LLM JSON response.

    Returns
    -------
    dict:
        document_id  — str, forwarded from raw (may be empty)
        is_valid     — True only when zero errors found
        errors       — list of {code, section_id, detail}
        section_results — list of {section_id, is_valid, error_codes}
    """
    errors: list[dict] = []
    section_results: list[dict] = []

    document_id: str = raw.get("document_id") or ""
    if not document_id:
        errors.append(_err(E_DOC_MISSING_ID, detail="document_id is missing or empty"))
    if not raw.get("source_kind"):
        errors.append(_err(E_DOC_MISSING_KIND, detail="source_kind is missing or empty"))

    sections = raw.get("sections")
    if not isinstance(sections, list):
        errors.append(_err(E_DOC_MISSING_SECTIONS, detail="sections must be a list"))
        return {
            "document_id": document_id,
            "is_valid": False,
            "errors": errors,
            "section_results": section_results,
        }

    for sec in sections:
        if not isinstance(sec, dict):
            errors.append(_err(E_SEC_MISSING_ID, detail="section entry is not a dict"))
            continue

        sid: str = sec.get("section_id") or ""
        if not sid:
            errors.append(_err(E_SEC_MISSING_ID, detail="section_id is missing or empty"))

        stype: str = sec.get("semantic_type", "")
        if stype not in _VALID_SEMANTIC_TYPES:
            err = _err(E_SEC_INVALID_SEMANTIC_TYPE, sid, f"got {stype!r}")
            errors.append(err)
            section_results.append({
                "section_id": sid,
                "is_valid": False,
                "error_codes": [E_SEC_INVALID_SEMANTIC_TYPE],
            })
            continue

        sec_errors = _SECTION_VALIDATORS[stype](sec, sid)
        errors.extend(sec_errors)
        section_results.append({
            "section_id": sid,
            "is_valid": len(sec_errors) == 0,
            "error_codes": [e["code"] for e in sec_errors],
        })

    return {
        "document_id": document_id,
        "is_valid": len(errors) == 0,
        "errors": errors,
        "section_results": section_results,
    }


# ---------------------------------------------------------------------------
# Public: downgrade
# ---------------------------------------------------------------------------

def _downgrade_section(section: dict) -> dict:
    """Return a safe-downgraded copy of a single section dict.

    Downgrade target:
    - semantic_type = "other", rewrite_policy = "preserve"
    - preserve_heading = True, preserve_body_structure = True
    - roles = []
    - blocks: one other_paragraph per original para_id (order preserved)

    Preserved: section_id, raw_title, display_title, block_id, para_id.
    """
    seen_pids: set[str] = set()
    pairs: list[tuple[str, str]] = []  # (block_id, para_id)

    def _collect(block_id: str, para_id: str) -> None:
        if para_id and para_id not in seen_pids:
            seen_pids.add(para_id)
            pairs.append((block_id, para_id))

    for b in section.get("blocks", []):
        _collect(b.get("block_id", ""), b.get("para_id", ""))

    for role in section.get("roles", []):
        for group in ("header_blocks", "meta_blocks", "body_blocks"):
            for b in role.get(group, []):
                _collect(b.get("block_id", ""), b.get("para_id", ""))

    downgraded_blocks: list[dict] = [
        {
            "block_id": bid or f"blk_{idx:03d}",
            "para_id": pid,
            "semantic_type": "other_paragraph",
            "rewrite_policy": "preserve",
            "new": False,
        }
        for idx, (bid, pid) in enumerate(pairs, start=1)
    ]

    return {
        "section_id": section.get("section_id", ""),
        "raw_title": section.get("raw_title", ""),
        "display_title": section.get("display_title", ""),
        "semantic_type": "other",
        "rewrite_policy": "preserve",
        "preserve_heading": True,
        "preserve_body_structure": True,
        "blocks": downgraded_blocks,
        "roles": [],
    }


def downgrade_invalid_sections(classification: dict, invalid_section_ids: set[str]) -> dict:
    """Return a shallow copy of classification with invalid sections downgraded.

    Valid sections are returned as-is.  The top-level dict is a new object so
    the original is not mutated.
    """
    if not invalid_section_ids:
        return classification

    result = dict(classification)
    result["sections"] = [
        _downgrade_section(sec) if sec.get("section_id", "") in invalid_section_ids else sec
        for sec in classification.get("sections", [])
    ]
    return result


# ---------------------------------------------------------------------------
# Public: combined entry point
# ---------------------------------------------------------------------------

def get_errors_by_section(validation: dict) -> dict[str, list[dict]]:
    """Return a mapping of section_id → list of error dicts from a validation result.

    Useful for building a repair payload that scopes errors to each invalid section.
    """
    by_section: dict[str, list[dict]] = {}
    for err in validation.get("errors", []):
        sid = err.get("section_id") or ""
        if sid:
            by_section.setdefault(sid, []).append(err)
    return by_section


def apply_validation_and_downgrade(
    raw_classification: dict,
) -> tuple[dict, dict, dict]:
    """Validate and, if needed, downgrade a raw classification dict.

    Returns
    -------
    (validation_result, final_classification, status_meta)

    validation_result:
        {document_id, is_valid, errors, section_results}

    final_classification:
        Ready-to-persist classification.  Identical to raw_classification when
        all sections pass; invalid sections are replaced with downgraded copies.

    status_meta:
        {
          "status": "valid" | "downgraded",
          "validation_error_count": int,
          "invalid_section_ids": list[str],
          "recovery_applied": bool,
        }
    """
    validation = validate_classification(raw_classification)

    invalid_ids: list[str] = [
        sr["section_id"]
        for sr in validation["section_results"]
        if not sr["is_valid"]
    ]
    invalid_set = set(invalid_ids)
    recovery_applied = bool(invalid_set)

    final = downgrade_invalid_sections(raw_classification, invalid_set)

    status_meta: dict = {
        "status": "downgraded" if recovery_applied else "valid",
        "validation_error_count": len(validation["errors"]),
        "invalid_section_ids": invalid_ids,
        "recovery_applied": recovery_applied,
    }

    return validation, final, status_meta

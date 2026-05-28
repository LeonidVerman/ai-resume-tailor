"""
src/tailor/compiler/classification_normalizer.py

Pre-classification normalizations applied to ClassificationInput before LLM
classification, and post-classification resolution of synthetic para_ids.

Normalization pipeline (applied in order):
  1. Remove section_heading paragraphs — text already captured in raw_title.
  2. Clear roles[] for non-experience sections — prevents the LLM from treating
     education/skills role-like structures as experience roles.
  3. Split compound role_meta paragraphs — pipe-delimited meta lines are split
     into individual parts so the LLM classifies each part cleanly.

Synthetic para IDs use the format:  {source_para_id}__meta_{index}
e.g.  para_25__meta_0, para_25__meta_1

The sidecar_map (synthetic_id -> source_id) is returned alongside the
normalized input and used in post-processing to replace synthetic para_ids
in the LLM output back to source para_ids.

Post-classification:
  resolve_synthetic_para_ids(classification, sidecar) — replaces synthetic
  para_ids in preserved-type blocks with their source para_ids.
  Rewriteable blocks (role_achievement_bullet, role_responsibility_bullet)
  keep their synthetic para_ids; they have no matching IR paragraph until
  a future IR-split feature.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tailor.compiler.classification_models import (
        ClassificationInput,
        ClassificationSectionInput,
        ClassificationParaInput,
        ClassificationRoleInput,
    )

# ---------------------------------------------------------------------------
# Non-experience section title heuristics
# ---------------------------------------------------------------------------

_NON_EXP_KEYWORDS: frozenset[str] = frozenset({
    "summary", "professional summary", "executive summary", "career summary",
    "objective", "career objective", "professional objective",
    "profile", "about me", "about", "personal statement",
    "skills", "technical skills", "core skills", "key skills",
    "skills & expertise", "skills and expertise",
    "competencies", "core competencies", "key competencies",
    "education", "academic", "academics", "academic background",
    "academic qualifications", "educational background", "education & training",
    "certifications", "certificates", "professional certifications",
    "licenses", "awards", "honors", "honours", "achievements", "accomplishments",
    "languages", "language skills", "language proficiencies",
    "references", "volunteer", "volunteering", "volunteer experience",
    "interests", "hobbies", "activities", "extracurricular",
    "affiliations", "memberships",
    "publications", "research", "presentations", "conferences",
    "additional", "additional information", "additional skills",
    "other", "other information", "miscellaneous",
})


def _is_non_experience_title(raw_title: str) -> bool:
    t = raw_title.strip().lower()
    if t in _NON_EXP_KEYWORDS:
        return True
    for kw in _NON_EXP_KEYWORDS:
        if t.startswith(kw):
            return True
    return False


# ---------------------------------------------------------------------------
# Compound role_meta splitter
# ---------------------------------------------------------------------------

_PIPE_RE = re.compile(r"\s*\|\s*")


def _split_compound_meta(text: str) -> list[str] | None:
    """Split a pipe-delimited meta line into individual parts.

    Returns None when the text contains no ' | ' or yields fewer than 2 parts.
    """
    if " | " not in text:
        return None
    parts = [p.strip() for p in _PIPE_RE.split(text)]
    parts = [p for p in parts if p]
    return parts if len(parts) >= 2 else None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def normalize_classification_input(
    ci: "ClassificationInput",
) -> "tuple[ClassificationInput, dict[str, str]]":
    """Apply pre-classification normalizations to a ClassificationInput.

    Returns
    -------
    (normalized_input, sidecar_map)

    sidecar_map maps synthetic_para_id -> source_para_id.
    Empty dict when no synthetic paragraphs were created.
    """
    from tailor.compiler.classification_models import (
        ClassificationInput,
        ClassificationSectionInput,
        ClassificationParaInput,
        ClassificationRoleInput,
    )

    sidecar: dict[str, str] = {}
    new_sections: list[ClassificationSectionInput] = []

    for sec in ci.sections:
        # Para ids used as role headers — must survive step 1
        role_header_ids: set[str] = {
            pid for role in sec.roles for pid in role.header_para_ids
        }

        # Step 1: drop section_heading paras that are not role headers
        paras_s1: list[ClassificationParaInput] = [
            p for p in sec.paragraphs
            if p.parser_semantic != "section_heading" or p.para_id in role_header_ids
        ]

        # Step 2: clear roles for non-experience sections
        roles_s2 = (
            [] if _is_non_experience_title(sec.raw_title) and sec.roles
            else sec.roles
        )

        # Step 3: split compound role_meta paragraphs referenced in roles_s2
        meta_ids_in_roles: set[str] = {
            pid for role in roles_s2 for pid in role.meta_para_ids
        }
        paras_s3: list[ClassificationParaInput] = []
        split_map: dict[str, list[str]] = {}  # source_id -> [synth_id, ...]

        for p in paras_s1:
            if p.parser_semantic == "role_meta" and p.para_id in meta_ids_in_roles:
                parts = _split_compound_meta(p.text)
                if parts is not None:
                    synth_ids: list[str] = []
                    for idx, part_text in enumerate(parts):
                        synth_id = f"{p.para_id}__meta_{idx}"
                        sidecar[synth_id] = p.para_id
                        synth_ids.append(synth_id)
                        paras_s3.append(ClassificationParaInput(
                            para_id=synth_id,
                            text=part_text,
                            parser_semantic="role_meta",
                        ))
                    split_map[p.para_id] = synth_ids
                    continue
            paras_s3.append(p)

        roles_s3 = _remap_role_meta_ids(roles_s2, split_map)

        new_sections.append(ClassificationSectionInput(
            section_id=sec.section_id,
            raw_title=sec.raw_title,
            paragraphs=paras_s3,
            roles=roles_s3,
        ))

    return ClassificationInput(
        document_id=ci.document_id,
        source_kind=ci.source_kind,
        sections=new_sections,
    ), sidecar


def _remap_role_meta_ids(
    roles: "list[ClassificationRoleInput]",
    split_map: "dict[str, list[str]]",
) -> "list[ClassificationRoleInput]":
    """Return roles with meta_para_ids expanded per split_map."""
    if not split_map:
        return roles

    from tailor.compiler.classification_models import ClassificationRoleInput

    result: list[ClassificationRoleInput] = []
    for role in roles:
        new_meta: list[str] = []
        for pid in role.meta_para_ids:
            new_meta.extend(split_map.get(pid, [pid]))
        result.append(ClassificationRoleInput(
            role_id=role.role_id,
            header_para_ids=role.header_para_ids,
            meta_para_ids=new_meta,
            bullet_para_ids=role.bullet_para_ids,
        ))
    return result


# ---------------------------------------------------------------------------
# Post-classification: resolve synthetic para_ids
# ---------------------------------------------------------------------------

# Semantic types that are preserved in the IR — synthetic para_ids for these
# blocks should be resolved back to their source para_ids.
_PRESERVED_TYPES: frozenset[str] = frozenset({
    "role_meta",
    "role_header",
    "role_intro",
    "role_highlight",
    "role_project_label",
    "role_project_context",
    "role_tech_stack",
    "role_key_technologies",
    "role_tools",
    "role_nested_detail",
    "role_freeform_note",
    "education_entry",
    "skills_paragraph",
    "summary_paragraph",
    "other_paragraph",
    "project_entry",
    "additional_item",
    "section_heading",
})


def resolve_synthetic_para_ids(
    classification: dict,
    sidecar: dict[str, str],
) -> dict:
    """Replace synthetic para_ids in the classification output with source para_ids.

    Only preserved-type blocks are resolved.  When multiple synthetic children
    of the same source are all classified as preserved, duplicates are removed
    (first occurrence wins).

    Rewriteable synthetic blocks (role_achievement_bullet,
    role_responsibility_bullet) are left unchanged — they have no corresponding
    IR paragraph until a future feature adds IR-level para splitting.

    The input dict is not modified; a deep copy is returned.
    """
    if not sidecar:
        return classification

    import copy
    result = copy.deepcopy(classification)

    for sec in result.get("sections", []):
        if not isinstance(sec, dict):
            continue
        _resolve_block_list(sec.get("blocks", []), sidecar)
        for role in sec.get("roles", []):
            if not isinstance(role, dict):
                continue
            _resolve_block_list(role.get("header_blocks", []), sidecar)
            _resolve_block_list(role.get("meta_blocks", []), sidecar)
            _resolve_block_list(role.get("body_blocks", []), sidecar)

    return result


def _resolve_block_list(blocks: list, sidecar: dict[str, str]) -> None:
    """In-place: resolve synthetic para_ids and deduplicate same-source blocks."""
    emitted_source_ids: set[str] = set()
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if not isinstance(block, dict):
            i += 1
            continue
        pid = block.get("para_id", "")
        source_id = sidecar.get(pid)
        if source_id is None:
            i += 1
            continue
        if block.get("semantic_type", "") not in _PRESERVED_TYPES:
            i += 1
            continue
        if source_id in emitted_source_ids:
            blocks.pop(i)  # duplicate — remove
        else:
            block["para_id"] = source_id
            emitted_source_ids.add(source_id)
            i += 1

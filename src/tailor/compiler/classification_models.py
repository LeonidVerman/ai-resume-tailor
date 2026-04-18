"""
src/tailor/compiler/classification_models.py

Lightweight models for upload-time LLM resume template classification.

Two concerns:
  ClassificationInput  — compact structured view of the parsed IR sent to the
                         classifier LLM.  Built by build_classification_input().
  ClassificationOutput — typed representation of the JSON the LLM returns.

Neither model participates in the existing tailoring / rendering pipeline.
They exist solely to make the Phase 1 classification inspectable and to
provide a typed foundation for later integration phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tailor.compiler.models import ResumeDocument


# ── Input model ───────────────────────────────────────────────────────────

@dataclass
class ClassificationParaInput:
    para_id: str
    text: str
    parser_semantic: str  # section_heading | role_header | role_meta | bullet | paragraph | empty

    def to_dict(self) -> dict:
        return {"para_id": self.para_id, "text": self.text, "parser_semantic": self.parser_semantic}


@dataclass
class ClassificationRoleInput:
    role_id: str
    header_para_ids: list[str] = field(default_factory=list)
    meta_para_ids: list[str] = field(default_factory=list)
    bullet_para_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "role_id": self.role_id,
            "header_para_ids": self.header_para_ids,
            "meta_para_ids": self.meta_para_ids,
            "bullet_para_ids": self.bullet_para_ids,
        }


@dataclass
class ClassificationSectionInput:
    section_id: str
    raw_title: str
    paragraphs: list[ClassificationParaInput] = field(default_factory=list)
    roles: list[ClassificationRoleInput] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "section_id": self.section_id,
            "raw_title": self.raw_title,
            "paragraphs": [p.to_dict() for p in self.paragraphs],
            "roles": [r.to_dict() for r in self.roles],
        }


@dataclass
class ClassificationInput:
    document_id: str
    source_kind: str
    sections: list[ClassificationSectionInput] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "source_kind": self.source_kind,
            "sections": [s.to_dict() for s in self.sections],
        }


def build_classification_input(doc: ResumeDocument, document_id: str) -> ClassificationInput:
    """Derive a compact ClassificationInput from a parsed ResumeDocument.

    The document must have stable IDs assigned (assign_stable_ids() called).
    Paragraphs with empty para_ids are included with a fallback ID so the
    classifier always has something to reference, even for legacy IR dicts
    that pre-date the stable-ID feature.
    """
    _fallback_counter = 0

    def _pid(pm) -> str:
        nonlocal _fallback_counter
        if pm.para_id:
            return pm.para_id
        _fallback_counter += 1
        return f"fallback_{_fallback_counter}"

    sections: list[ClassificationSectionInput] = []

    for sec_idx, section in enumerate(doc.sections, start=1):
        sec_id = section.section_id or f"sec_{sec_idx}"

        # All paragraphs in this section (flat list for the classifier).
        paras: list[ClassificationParaInput] = [
            ClassificationParaInput(
                para_id=_pid(section.heading),
                text=section.heading.text,
                parser_semantic=section.heading.semantic,
            )
        ]

        roles: list[ClassificationRoleInput] = []

        if section.roles:
            for role in section.roles:
                rid = role.role_id_stable or role.role_id or f"role_in_{sec_id}_{len(roles)+1}"

                header_ids: list[str] = []
                meta_ids: list[str] = []
                bullet_ids: list[str] = []

                hp = ClassificationParaInput(_pid(role.header), role.header.text, role.header.semantic)
                paras.append(hp)
                header_ids.append(hp.para_id)

                for pm in role.header_extra:
                    p = ClassificationParaInput(_pid(pm), pm.text, pm.semantic)
                    paras.append(p)
                    header_ids.append(p.para_id)

                for pm in role.meta_lines:
                    p = ClassificationParaInput(_pid(pm), pm.text, pm.semantic)
                    paras.append(p)
                    meta_ids.append(p.para_id)

                for pm in role.bullets:
                    p = ClassificationParaInput(_pid(pm), pm.text, pm.semantic)
                    paras.append(p)
                    bullet_ids.append(p.para_id)

                roles.append(ClassificationRoleInput(
                    role_id=rid,
                    header_para_ids=header_ids,
                    meta_para_ids=meta_ids,
                    bullet_para_ids=bullet_ids,
                ))
        elif (
            section.semantic_type not in ("experience", "summary", "skills", "education")
            and any(p.semantic == "role_meta" for p in section.body_paras)
        ):
            # Pattern C: job-title-as-section (PDF table/column layouts where
            # each job title is parsed as a section heading followed by
            # company + date + bullets in the body).  The section heading itself
            # is the role header; use its existing para_id so the entry is
            # consistent with what the section heading shows in paras[0].
            meta_ids = []
            bullet_ids = []
            for pm in section.body_paras:
                p = ClassificationParaInput(_pid(pm), pm.text, pm.semantic)
                paras.append(p)
                if pm.semantic == "role_meta":
                    meta_ids.append(p.para_id)
                elif pm.semantic == "bullet":
                    bullet_ids.append(p.para_id)
            roles.append(ClassificationRoleInput(
                role_id=f"{sec_id}_role_1",
                header_para_ids=[_pid(section.heading)],
                meta_para_ids=meta_ids,
                bullet_para_ids=bullet_ids,
            ))
        else:
            for pm in section.body_paras:
                paras.append(ClassificationParaInput(_pid(pm), pm.text, pm.semantic))

        sections.append(ClassificationSectionInput(
            section_id=sec_id,
            raw_title=section.title,
            paragraphs=paras,
            roles=roles,
        ))

    return ClassificationInput(
        document_id=document_id,
        source_kind=doc.source_kind,
        sections=sections,
    )


# ── Output model ──────────────────────────────────────────────────────────

@dataclass
class ClassificationBlock:
    block_id: str
    para_id: str
    semantic_type: str   # summary_paragraph | skills_paragraph | role_header | …
    rewrite_policy: str  # rewrite_text | preserve
    new: bool = False

    @classmethod
    def from_dict(cls, d: dict) -> "ClassificationBlock":
        return cls(
            block_id=d["block_id"],
            para_id=d["para_id"],
            semantic_type=d["semantic_type"],
            rewrite_policy=d["rewrite_policy"],
            new=bool(d.get("new", False)),
        )


@dataclass
class ClassificationRole:
    role_id: str
    header_blocks: list[ClassificationBlock] = field(default_factory=list)
    meta_blocks: list[ClassificationBlock] = field(default_factory=list)
    body_blocks: list[ClassificationBlock] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ClassificationRole":
        return cls(
            role_id=d["role_id"],
            header_blocks=[ClassificationBlock.from_dict(b) for b in d.get("header_blocks", [])],
            meta_blocks=[ClassificationBlock.from_dict(b) for b in d.get("meta_blocks", [])],
            body_blocks=[ClassificationBlock.from_dict(b) for b in d.get("body_blocks", [])],
        )


@dataclass
class ClassificationSection:
    section_id: str
    raw_title: str
    display_title: str
    semantic_type: str    # summary | skills | experience | education | projects | additional | other
    rewrite_policy: str   # rewrite_body | rewrite_bullets_only | preserve
    preserve_heading: bool = True
    preserve_body_structure: bool = False
    blocks: list[ClassificationBlock] = field(default_factory=list)
    roles: list[ClassificationRole] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ClassificationSection":
        return cls(
            section_id=d["section_id"],
            raw_title=d["raw_title"],
            display_title=d["display_title"],
            semantic_type=d["semantic_type"],
            rewrite_policy=d["rewrite_policy"],
            preserve_heading=bool(d.get("preserve_heading", True)),
            preserve_body_structure=bool(d.get("preserve_body_structure", False)),
            blocks=[ClassificationBlock.from_dict(b) for b in d.get("blocks", [])],
            roles=[ClassificationRole.from_dict(r) for r in d.get("roles", [])],
        )


@dataclass
class ClassificationOutput:
    document_id: str
    classification_version: str
    source_kind: str
    sections: list[ClassificationSection] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "ClassificationOutput":
        return cls(
            document_id=d["document_id"],
            classification_version=d.get("classification_version", "1.0"),
            source_kind=d["source_kind"],
            sections=[ClassificationSection.from_dict(s) for s in d.get("sections", [])],
        )

    def to_dict(self) -> dict:
        """Return a plain JSON-serializable dict (for DB storage)."""
        import dataclasses
        return dataclasses.asdict(self)

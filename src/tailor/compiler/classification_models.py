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

import re
from dataclasses import dataclass, field
from typing import Any

from tailor.compiler.models import ResumeDocument


# ── Role-header normalisation helpers ────────────────────────────────────

_MONTH_NAME = (
    r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?'
    r'|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
)
_YEAR_TOKEN = r'(?:\d{4}|20[Xx]{2}|19[Xx]{2})'
_DATE_END_TOKEN = (
    fr'(?:{_MONTH_NAME}[\s,]*{_YEAR_TOKEN}?|{_YEAR_TOKEN}|present|current|now|ongoing)'
)
_DATE_START_TOKEN = fr'(?:{_MONTH_NAME}[\s,]*{_YEAR_TOKEN}?|{_YEAR_TOKEN})'
_DATE_RANGE_RE = re.compile(
    fr'^[\(\[\s]*{_DATE_START_TOKEN}[\s\S]{{0,25}}{_DATE_END_TOKEN}[\)\]\s]*$',
    re.IGNORECASE,
)


def _is_date_line(text: str) -> bool:
    t = text.strip()
    return len(t) <= 80 and bool(_DATE_RANGE_RE.match(t))


def _normalize_role_header_items(
    header_cps: list[tuple[str, str, str]],  # list of (para_id, text, semantic)
    uid_prefix: str,
    synth_counter: list[int],              # mutable int box
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Deterministically move/split role header paragraphs into header vs meta.

    Returns (final_header_list, extra_meta_list) where each item is (para_id, text, semantic).

    Rules applied in order per item:
      Norm D — item text > 120 chars and there is at least one other header → meta
      Norm A — item 0 contains ' | ' and one side is a date range → split into title + synth meta date
      Norm B — item 0 is a pure date line AND there are more items → move to meta, promote item 1
      Norm C — item index >= 1, len <= 80, no pipe, no date → meta (employer/location line)

    Guarantee: final header list is never empty (restores first promoted item if needed).
    """
    if not header_cps:
        return [], []

    final_headers: list[tuple[str, str, str]] = []
    extra_meta: list[tuple[str, str, str]] = []

    # Work on a copy so we can mutate freely
    items = list(header_cps)

    # Norm D: very long multi-word lines that look like descriptions, but only when
    # there is at least one shorter sibling to remain as header.
    has_short = any(len(pid_txt_sem[1].strip()) <= 120 for pid_txt_sem in items)
    if has_short:
        kept: list[tuple[str, str, str]] = []
        for item in items:
            if len(item[1].strip()) > 120:
                extra_meta.append(item)
            else:
                kept.append(item)
        items = kept if kept else items  # never empty

    # Norm A: item 0 contains ' | ' — try to split date from title
    if items and ' | ' in items[0][1]:
        pid0, txt0, sem0 = items[0]
        parts = [p.strip() for p in txt0.split(' | ')]
        date_idx = next((i for i, p in enumerate(parts) if _is_date_line(p)), None)
        if date_idx is not None:
            title_parts = [p for i, p in enumerate(parts) if i != date_idx]
            date_text = parts[date_idx]
            new_title = ' | '.join(title_parts)
            synth_counter[0] += 1
            synth_id = f"{uid_prefix}_normA_{synth_counter[0]}"
            items[0] = (pid0, new_title, sem0)
            extra_meta.insert(0, (synth_id, date_text, "role_meta"))

    # Norm B: item 0 is a pure date line and there are more items → move to meta
    if len(items) > 1 and _is_date_line(items[0][1]):
        extra_meta.insert(0, items[0])
        items = items[1:]

    # Norm C: items at index >= 1 that look like employer/location or body text
    # (no pipe, no date, and not a very long proper title).  The threshold is
    # 120 chars to also catch medium-length sentence-like descriptions (e.g.
    # "This is the place for a summary of your key responsibilities…", 89 chars)
    # that templates insert as header_extra lines.
    if len(items) > 1:
        new_items: list[tuple[str, str, str]] = [items[0]]
        for item in items[1:]:
            pid, txt, sem = item
            t = txt.strip()
            if len(t) <= 120 and ' | ' not in t and not _is_date_line(t):
                extra_meta.append(item)
            else:
                new_items.append(item)
        items = new_items if new_items else items

    final_headers = items

    # Guarantee: never return empty header list
    if not final_headers and extra_meta:
        final_headers = [extra_meta.pop(0)]

    return final_headers, extra_meta


# ── Input model ───────────────────────────────────────────────────────────

@dataclass
class ClassificationParaInput:
    para_id: str
    text: str
    parser_semantic: str  # section_heading | role_header | role_meta | bullet | paragraph | empty
    source_para_id: str = ""   # original para_id when this is a synthetic split product
    synthetic: bool = False    # True when created by pre-classification normalization
    split_kind: str = ""       # "compound_meta_nl" | "compound_meta_pipe" | "overmerged_role"

    def to_dict(self) -> dict:
        d: dict = {"para_id": self.para_id, "text": self.text, "parser_semantic": self.parser_semantic}
        if self.synthetic:
            d["source_para_id"] = self.source_para_id
            d["synthetic"] = True
            d["split_kind"] = self.split_kind
        return d


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
    _synth_counter = [0]  # mutable counter shared across all roles for unique synth IDs

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

                meta_ids: list[str] = []
                bullet_ids: list[str] = []

                # Collect raw header candidates as (para_id, text, semantic) tuples
                raw_header_cps: list[tuple[str, str, str]] = [
                    (_pid(role.header), role.header.text, role.header.semantic)
                ]
                for pm in role.header_extra:
                    raw_header_cps.append((_pid(pm), pm.text, pm.semantic))

                # Apply normalization: may move some header items to extra meta
                final_hdr_cps, extra_meta_cps = _normalize_role_header_items(
                    raw_header_cps, rid, _synth_counter
                )

                # Emit header paragraphs (may include synth items that lack a real pm)
                header_ids: list[str] = []
                for pid, txt, sem in final_hdr_cps:
                    if not any(cp.para_id == pid for cp in paras):
                        paras.append(ClassificationParaInput(pid, txt, sem))
                    header_ids.append(pid)

                # Emit extra meta from normalization (before parser-assigned meta_lines)
                for pid, txt, sem in extra_meta_cps:
                    if not any(cp.para_id == pid for cp in paras):
                        paras.append(ClassificationParaInput(pid, txt, sem))
                    meta_ids.append(pid)

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

"""Tests for Phase 2 injection fixes: classification trust guard, lenient
section mapping, similarity-based role pairing, LLM-header mega-role split.

Root causes covered (golden-set samples in parentheses):
- Bogus 'preserve' policies on real target sections are bypassed (24/26/33/35).
- One unmatchable section no longer reverts the whole document to the
  template — matched pairs are still applied (28).
- Classified role pairing is by company similarity, not list position (29/33).
- A classification mega-role is split on bullets matching LLM role headers
  even without role_meta boundary paras (35).
"""
from __future__ import annotations

from tailor.compiler.classification_models import (
    ClassificationBlock,
    ClassificationOutput,
    ClassificationRole,
    ClassificationSection,
)
from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
    assign_stable_ids,
)
from tailor.compiler.text_parser import LlmRole, LlmSection
from tailor.compiler.updater import (
    _match_sections,
    _split_cls_mega_role,
    apply_tailored,
)


def _para(text: str, semantic: str, para_id: str = "") -> ParaModel:
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _layout() -> LayoutProfile:
    return LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )


def _body_section(title: str, semantic_type: str, texts: list[str]) -> ResumeSection:
    sec = ResumeSection(
        title=title,
        heading=_para(title, "section_heading"),
        semantic_type=semantic_type,
        body_paras=[_para(t, "paragraph") for t in texts],
    )
    return sec


def _doc(sections: list[ResumeSection]) -> ResumeDocument:
    doc = ResumeDocument(header_paras=[], sections=sections, layout=_layout(), all_paras=[])
    assign_stable_ids(doc)
    return doc


def _cls(sections: list[ClassificationSection]) -> ClassificationOutput:
    return ClassificationOutput(
        document_id="doc_1", classification_version="v1",
        source_kind="docx", sections=sections,
    )


def _cls_sec(
    section_id: str, title: str, semantic_type: str, rewrite_policy: str,
) -> ClassificationSection:
    return ClassificationSection(
        section_id=section_id, raw_title=title, display_title=title,
        semantic_type=semantic_type, rewrite_policy=rewrite_policy,
    )


class TestClsPreserveTrustGuard:
    def test_mismatched_preserve_on_skills_section_bypassed(self):
        # Sample 24/33: IR says skills, classification says other/preserve.
        doc = _doc([_body_section("SKILL", "skills", ["Old skill line"])])
        cls = _cls([_cls_sec(doc.sections[0].section_id, "SKILL", "other", "preserve")])
        llm = [LlmSection(heading="Technical Skills", semantic_type="skills",
                          body_lines=["Rust", "Go"])]
        result = apply_tailored(doc, llm, classification=cls)
        texts = [p.text for p in result.sections[0].body_paras]
        assert "Old skill line" not in texts
        assert "Rust" in texts

    def test_matching_typed_preserve_honored(self):
        # Explicit skills/preserve is a deliberate classifier statement.
        doc = _doc([_body_section("Skills", "skills", ["Old skill line"])])
        cls = _cls([_cls_sec(doc.sections[0].section_id, "Skills", "skills", "preserve")])
        llm = [LlmSection(heading="Skills", semantic_type="skills", body_lines=["Rust"])]
        result = apply_tailored(doc, llm, classification=cls)
        assert [p.text for p in result.sections[0].body_paras] == ["Old skill line"]

    def test_other_preserve_empty_blocks_bypassed_for_llm_skills(self):
        # Sample 35: IR 'other' + cls other/preserve with no blocks, matched to
        # an LLM skills section via the skills-title keyword pass.
        doc = _doc([_body_section("Skills (used in projects)", "other", ["Old line"])])
        cls = _cls([_cls_sec(doc.sections[0].section_id,
                             "Skills (used in projects)", "other", "preserve")])
        llm = [LlmSection(heading="Technical Skills", semantic_type="skills",
                          body_lines=["Rust", "Go"])]
        result = apply_tailored(doc, llm, classification=cls)
        texts = [p.text for p in result.sections[0].body_paras]
        assert "Rust" in texts


class TestLenientSectionMapping:
    def _mismatch_doc_and_llm(self):
        # Template: summary + experience-like body + a content section the LLM
        # never reproduces; LLM: matching summary + an unmatchable extra.
        doc = _doc([
            _body_section("Summary", "summary", ["Old summary."]),
            _body_section("Highlights", "skills", ["Old highlight"]),
        ])
        llm = [
            LlmSection(heading="Summary", semantic_type="summary",
                       body_lines=["New tailored summary."]),
            LlmSection(heading="Invented Section", semantic_type="other",
                       body_lines=["Invented content"]),
        ]
        return doc, llm

    def test_strict_match_raises(self):
        doc, llm = self._mismatch_doc_and_llm()
        try:
            _match_sections(doc.sections, llm)
            raised = False
        except ValueError:
            raised = True
        assert raised

    def test_matched_pairs_still_applied(self):
        # Sample 28: previously the whole template was returned verbatim.
        doc, llm = self._mismatch_doc_and_llm()
        result = apply_tailored(doc, llm)
        assert result.sections[0].body_paras[0].text == "New tailored summary."
        # unmatched original kept verbatim
        assert result.sections[1].body_paras[0].text == "Old highlight"

    def test_zero_pairs_returns_template_verbatim(self):
        doc = _doc([_body_section("Highlights", "skills", ["Old highlight"])])
        llm = [LlmSection(heading="Unrelated", semantic_type="summary",
                          body_lines=["Lone summary."])]
        result = apply_tailored(doc, llm)
        assert result.sections[0].body_paras[0].text == "Old highlight"
        assert all(s.semantic_type != "summary" for s in result.sections)


class TestSimilarityRolePairing:
    def test_bullets_land_on_matching_company(self):
        # LLM emits roles in a different order than the template.
        header_a = _para("Engineer | Acme Corp", "role_header")
        header_b = _para("Engineer | Globex Inc", "role_header")
        bullets_a = [_para("old acme bullet", "bullet")]
        bullets_b = [_para("old globex bullet", "bullet")]
        sec = ResumeSection(
            title="Experience",
            heading=_para("Experience", "section_heading"),
            semantic_type="experience",
            roles=[
                RoleEntry(header=header_a, bullets=bullets_a, role_id="a"),
                RoleEntry(header=header_b, bullets=bullets_b, role_id="b"),
            ],
        )
        doc = _doc([sec])
        cls = _cls([_cls_sec(doc.sections[0].section_id, "Experience",
                             "experience", "rewrite_bullets_only")])
        llm = [LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[
                LlmRole(header="Engineer | Globex Inc", bullets=["Globex work."]),
                LlmRole(header="Engineer | Acme Corp", bullets=["Acme work."]),
            ],
        )]
        result = apply_tailored(doc, llm, classification=cls)
        roles = result.sections[0].roles
        assert roles[0].bullets[0].text == "Acme work."
        assert roles[1].bullets[0].text == "Globex work."


class TestMegaRoleSplitOnLlmHeaders:
    def test_split_on_bullet_matching_llm_header(self):
        # Sample 35: mega-role bullets contain the next role's header line but
        # no role_meta paras (dates live in another column).
        bullets = [
            _para("Built the exchange backend.", "bullet", "para_1"),
            _para("Senior Backend Engineer, Enclave Markets, Remote", "bullet", "para_2"),
            _para("Added websocket market data streaming.", "bullet", "para_3"),
        ]
        role = RoleEntry(
            header=_para("Senior Backend Engineer, Ondo Perps, Remote",
                         "role_header", "para_0"),
            bullets=bullets,
            role_id="Senior Backend Engineer, Ondo Perps, Remote",
        )
        llm_roles = [
            LlmRole(header="Senior Backend Engineer, Ondo Perps, Remote", bullets=["x"]),
            LlmRole(header="Senior Backend Engineer, Enclave Markets, Remote", bullets=["y"]),
        ]
        split = _split_cls_mega_role(role, llm_roles)
        assert len(split) == 2
        assert split[1].header.text == "Senior Backend Engineer, Enclave Markets, Remote"
        assert [b.text for b in split[0].bullets] == ["Built the exchange backend."]
        assert [b.text for b in split[1].bullets] == ["Added websocket market data streaming."]

    def test_no_split_without_llm_roles(self):
        bullets = [
            _para("Did work.", "bullet", "para_1"),
            _para("Senior Backend Engineer, Enclave Markets, Remote", "bullet", "para_2"),
        ]
        role = RoleEntry(header=_para("H", "role_header", "para_0"),
                         bullets=bullets, role_id="H")
        assert len(_split_cls_mega_role(role)) == 1

"""Regression tests for date-first experience layout support in apply_tailored.

Template layout being tested:
  (2010-2013)
  Ginyard International Co.
  RESPONSIBLE FOR NETWORK AND SOFTWARE
  Develop features...
  Contribute to code reviews...

  (2014-Now)
  Wardiere Inc.
  SOFTWARE ENGINEERING
  Develop features...
  Use Infrastructure As Code...

The parser collapses this into one malformed RoleEntry because it expects
role_header → meta → bullets but the template has meta → header → bullets.
The fix rebuilds correct role groups from body_paras and matches LLM roles
to them by company name similarity (handling reverse-chronological LLM output).
"""
from __future__ import annotations

import pytest

from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)
from tailor.compiler.text_parser import LlmRole, LlmSection
from tailor.compiler.updater import (
    _extract_company_tokens,
    _has_date_first_layout,
    _match_llm_to_ir_roles,
    _rebuild_date_first_roles,
    apply_tailored,
)


# ── IR construction helpers ───────────────────────────────────────────────

def _para(text: str, semantic: str = "paragraph", para_id: str = "") -> ParaModel:
    return ParaModel(text=text, style=ParaStyle(), semantic=semantic, para_id=para_id)


def _empty(para_id: str = "") -> ParaModel:
    return _para("", "empty", para_id)


_LAYOUT = LayoutProfile(
    page_width_pt=612, page_height_pt=792,
    margin_top_pt=72, margin_bottom_pt=72,
    margin_left_pt=72, margin_right_pt=72,
    default_font_name="Calibri", default_font_size_pt=11.0,
)


def _make_date_first_section() -> ResumeSection:
    """Replicate the exact body_paras structure from the Lydia_Mary debug file."""
    body_paras = [
        _empty("para_66"),
        _empty("para_67"),
        _empty("para_68"),
        _para("(2010-2013)",              "role_meta",  "para_69"),
        _empty("para_70"),
        _para("Ginyard International Co.", "paragraph", "para_71"),
        _empty("para_72"),
        _para("RESPONSIBLE FOR NETWORK AND SOFTWARE", "paragraph", "para_73"),
        _empty("para_74"),
        _para("Develop features that improve the team's ability to deliver reliability.", "paragraph", "para_75"),
        _empty("para_76"),
        _para("Contribute to code reviews along with design and architecture reviews.", "paragraph", "para_77"),
        _empty("para_78"),
        _empty("para_79"),
        _para("(2014-Now)",               "role_meta",  "para_80"),
        _empty("para_81"),
        _para("Wardiere Inc.",            "paragraph", "para_82"),
        _empty("para_83"),
        _para("SOFTWARE ENGINEERING",     "paragraph", "para_84"),
        _empty("para_85"),
        _para("Develop features that improve the team's ability to deliver reliability.", "paragraph", "para_86"),
        _empty("para_87"),
        _para("Use Infrastructure As Code tools and techniques.", "paragraph", "para_88"),
    ]
    # Simulate the malformed role the parser produces
    malformed_role = RoleEntry(
        header=body_paras[3],   # "(2010-2013)" — role_meta used as header
        meta_lines=[body_paras[14]],  # "(2014-Now)"
        bullets=[body_paras[9], body_paras[11]],
        role_id="(2010-2013)",
    )
    return ResumeSection(
        title="Experience",
        heading=_para("Experience", "section_heading"),
        semantic_type="experience",
        body_paras=body_paras,
        roles=[malformed_role],
        section_id="sec_3",
    )


def _make_doc(sections: list[ResumeSection]) -> ResumeDocument:
    all_paras = []
    for sec in sections:
        all_paras.append(sec.heading)
        all_paras.extend(sec.body_paras)
    return ResumeDocument(
        header_paras=[],
        sections=sections,
        layout=_LAYOUT,
        all_paras=all_paras,
    )


def _llm_dash_experience(roles: list[tuple[str, list[str]]]) -> LlmSection:
    """Build an LlmSection with dash-format body_lines (no pipe-separated roles)."""
    lines = []
    for header, bullets in roles:
        lines.append(header)
        for b in bullets:
            lines.append(f"- {b}")
        lines.append("")
    return LlmSection(
        heading="Experience",
        semantic_type="experience",
        roles=[],
        body_lines=[l for l in lines if l or lines.index(l) < len(lines)],
    )


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests for helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractCompanyTokens:
    def test_simple_company(self):
        tokens = _extract_company_tokens("Wardiere Inc.")
        assert "wardiere" in tokens
        assert "inc" not in tokens

    def test_company_with_dash_role(self):
        tokens = _extract_company_tokens("Wardiere Inc. – Software Engineering (2014–Present)")
        assert "wardiere" in tokens
        assert "software" not in tokens  # title part stripped

    def test_company_international(self):
        tokens = _extract_company_tokens("Ginyard International Co.")
        assert "ginyard" in tokens
        assert "international" not in tokens
        assert "co" not in tokens

    def test_company_in_dash_llm_header(self):
        tokens = _extract_company_tokens(
            "Ginyard International Co. – Responsible for Network and Software (2010–2013)"
        )
        assert "ginyard" in tokens
        assert "network" not in tokens


class TestDetectDateFirstLayout:
    def test_detects_malformed_section(self):
        sec = _make_date_first_section()
        assert _has_date_first_layout(sec) is True

    def test_ignores_normal_section(self):
        role = RoleEntry(
            header=_para("Engineer | Acme", "role_header"),
            meta_lines=[_para("2020–2022", "role_meta")],
            bullets=[_para("did things", "bullet")],
            role_id="Engineer | Acme",
        )
        sec = ResumeSection(
            title="Experience",
            heading=_para("Experience", "section_heading"),
            semantic_type="experience",
            roles=[role],
            body_paras=[],
        )
        assert _has_date_first_layout(sec) is False

    def test_ignores_single_meta(self):
        body = [_para("(2020-2022)", "role_meta"), _para("Company", "paragraph")]
        sec = ResumeSection(
            title="Experience",
            heading=_para("Experience", "section_heading"),
            semantic_type="experience",
            roles=[],
            body_paras=body,
        )
        assert _has_date_first_layout(sec) is False

    def test_detects_empty_roles_two_meta(self):
        body = [
            _para("(2010-2013)", "role_meta"),
            _para("Company A", "paragraph"),
            _para("(2014-Now)", "role_meta"),
            _para("Company B", "paragraph"),
        ]
        sec = ResumeSection(
            title="Experience",
            heading=_para("Experience", "section_heading"),
            semantic_type="experience",
            roles=[],
            body_paras=body,
        )
        assert _has_date_first_layout(sec) is True


class TestRebuildDateFirstRoles:
    def test_rebuilds_two_roles(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert len(roles) == 2

    def test_role0_meta_is_date(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[0].meta_lines[0].text == "(2010-2013)"

    def test_role0_header_is_company(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[0].header.text == "Ginyard International Co."
        assert roles[0].header.semantic != "role_meta"

    def test_role0_header_extra_is_title(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[0].header_extra[0].text == "RESPONSIBLE FOR NETWORK AND SOFTWARE"

    def test_role0_bullets_are_content(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert len(roles[0].bullets) == 2
        assert "Develop" in roles[0].bullets[0].text

    def test_role1_meta_is_second_date(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[1].meta_lines[0].text == "(2014-Now)"

    def test_role1_header_is_company(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[1].header.text == "Wardiere Inc."

    def test_role1_header_extra_is_title(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert roles[1].header_extra[0].text == "SOFTWARE ENGINEERING"

    def test_role1_bullets_are_content(self):
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        assert len(roles[1].bullets) == 2
        assert "Infrastructure" in roles[1].bullets[1].text

    def test_reuses_existing_para_model_instances(self):
        """Rebuilt roles hold references to the SAME ParaModel objects."""
        sec = _make_date_first_section()
        roles = _rebuild_date_first_roles(sec)
        # Wardiere Inc. paragraph is para_82 in body_paras
        wardiere_para = sec.body_paras[16]  # index 16 = para_82
        assert wardiere_para.text == "Wardiere Inc."
        assert roles[1].header is wardiere_para


class TestCompanyMatching:
    def _ginyard_role(self) -> RoleEntry:
        return RoleEntry(
            header=_para("Ginyard International Co."),
            header_extra=[_para("RESPONSIBLE FOR NETWORK AND SOFTWARE")],
            meta_lines=[_para("(2010-2013)", "role_meta")],
            bullets=[_para("old bullet 1"), _para("old bullet 2")],
            role_id="Ginyard International Co.",
        )

    def _wardiere_role(self) -> RoleEntry:
        return RoleEntry(
            header=_para("Wardiere Inc."),
            header_extra=[_para("SOFTWARE ENGINEERING")],
            meta_lines=[_para("(2014-Now)", "role_meta")],
            bullets=[_para("old w1"), _para("old w2")],
            role_id="Wardiere Inc.",
        )

    def test_wardiere_matches_wardiere(self):
        ir = [self._ginyard_role(), self._wardiere_role()]
        llm = [
            LlmRole(header="Wardiere Inc. – Software Engineering (2014–Present)", meta_lines=[], bullets=["new w1"]),
            LlmRole(header="Ginyard International Co. – Responsible for Network (2010–2013)", meta_lines=[], bullets=["new g1"]),
        ]
        result = _match_llm_to_ir_roles(llm, ir)
        # IR[0]=Ginyard should match LLM[1]=Ginyard
        # IR[1]=Wardiere should match LLM[0]=Wardiere
        assert result[0] == 1  # Ginyard IR → Ginyard LLM (index 1)
        assert result[1] == 0  # Wardiere IR → Wardiere LLM (index 0)

    def test_no_double_assignment(self):
        ir = [self._ginyard_role(), self._wardiere_role()]
        llm = [
            LlmRole(header="Wardiere Inc. – Software Engineering", meta_lines=[], bullets=["w"]),
            LlmRole(header="Ginyard International Co. – Responsible", meta_lines=[], bullets=["g"]),
        ]
        result = _match_llm_to_ir_roles(llm, ir)
        assigned = [r for r in result if r is not None]
        assert len(assigned) == len(set(assigned)), "each LLM role assigned at most once"


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests: apply_tailored with date-first experience
# ═══════════════════════════════════════════════════════════════════════════

class TestApplyTailoredDateFirst:
    """End-to-end tests using the Lydia_Mary / Wardiere+Ginyard structure."""

    def _make_llm_sections(
        self,
        wardiere_bullets: list[str] | None = None,
        ginyard_bullets: list[str] | None = None,
    ) -> list[LlmSection]:
        """LLM output with dash-format roles (reverse chronological)."""
        w_bullets = wardiere_bullets or ["Developed W1", "Developed W2", "Developed W3"]
        g_bullets = ginyard_bullets or ["Developed G1", "Contributed G2"]
        lines = [
            # Wardiere first (LLM reverse-chronological)
            "Wardiere Inc. – Software Engineering (2014–Present)",
        ] + [f"- {b}" for b in w_bullets] + [
            "",
            "Ginyard International Co. – Responsible for Network and Software (2010–2013)",
        ] + [f"- {b}" for b in g_bullets]
        return [LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[],
            body_lines=lines,
        )]

    def test_result_has_two_roles_in_all_paras(self):
        """Updated IR must reflect both roles in body_paras (via roles=[])."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections())

        exp = result.sections[0]
        # roles=[] means body_paras path is used
        assert exp.roles == []
        # body_paras still has both roles' content
        texts = [p.text for p in exp.body_paras if p.text.strip()]
        assert any("(2010-2013)" in t for t in texts)
        assert any("(2014-Now)" in t for t in texts)
        assert any("Ginyard" in t for t in texts)
        assert any("Wardiere" in t for t in texts)

    def test_wardiere_bullets_applied_to_wardiere(self):
        """Wardiere LLM bullets update only Wardiere template paragraphs."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections(
            wardiere_bullets=["WARDIERE SPECIFIC BULLET"],
        ))
        texts = [p.text for p in result.sections[0].body_paras if p.text.strip()]
        assert any("WARDIERE SPECIFIC BULLET" in t for t in texts)

    def test_ginyard_bullets_applied_to_ginyard(self):
        """Ginyard LLM bullets update only Ginyard template paragraphs."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections(
            ginyard_bullets=["GINYARD SPECIFIC BULLET"],
        ))
        texts = [p.text for p in result.sections[0].body_paras if p.text.strip()]
        assert any("GINYARD SPECIFIC BULLET" in t for t in texts)

    def test_wardiere_bullets_not_in_ginyard_position(self):
        """Bullets after Ginyard date/company must not contain Wardiere content."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections(
            wardiere_bullets=["WARDIERE UNIQUE XYZ"],
            ginyard_bullets=["GINYARD UNIQUE ABC"],
        ))
        bp = result.sections[0].body_paras
        # Find positions of the two date markers
        ginyard_date_idx = next(i for i, p in enumerate(bp) if "(2010-2013)" in p.text)
        wardiere_date_idx = next(i for i, p in enumerate(bp) if "(2014-Now)" in p.text)

        # Content between Ginyard date and Wardiere date should not contain Wardiere bullets
        ginyard_block = [p.text for p in bp[ginyard_date_idx:wardiere_date_idx] if p.text.strip()]
        assert not any("WARDIERE UNIQUE XYZ" in t for t in ginyard_block), \
            "Wardiere bullets leaked into Ginyard block"

        # Content after Wardiere date should not contain Ginyard bullets
        wardiere_block = [p.text for p in bp[wardiere_date_idx:] if p.text.strip()]
        assert not any("GINYARD UNIQUE ABC" in t for t in wardiere_block), \
            "Ginyard bullets leaked into Wardiere block"

    def test_dates_preserved(self):
        """Original date strings are never modified."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections())
        texts = {p.text for p in result.sections[0].body_paras}
        assert "(2010-2013)" in texts
        assert "(2014-Now)" in texts

    def test_company_names_preserved(self):
        """Company and title lines from template are never modified."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        result = apply_tailored(doc, self._make_llm_sections())
        texts = {p.text for p in result.sections[0].body_paras}
        assert "Ginyard International Co." in texts
        assert "RESPONSIBLE FOR NETWORK AND SOFTWARE" in texts
        assert "Wardiere Inc." in texts
        assert "SOFTWARE ENGINEERING" in texts

    def test_extra_llm_roles_ignored(self):
        """If LLM writes 3 roles but template has 2, extra is ignored."""
        sec = _make_date_first_section()
        doc = _make_doc([sec])
        # Add a third LLM role
        lines = [
            "Wardiere Inc. – Software Engineering (2014–Present)",
            "- W bullet",
            "",
            "Ginyard International Co. – Responsible (2010–2013)",
            "- G bullet",
            "",
            "Extra Corp – Fake Role (2008–2010)",
            "- Extra bullet",
        ]
        llm = [LlmSection(heading="Experience", semantic_type="experience", roles=[], body_lines=lines)]
        result = apply_tailored(doc, llm)
        texts = {p.text for p in result.sections[0].body_paras if p.text.strip()}
        # Extra Corp should not appear
        assert not any("Extra Corp" in t for t in texts)
        assert not any("Extra bullet" in t for t in texts)

    def test_normal_template_unchanged(self):
        """A normal header-first template must continue to work exactly as before."""
        role = RoleEntry(
            header=_para("Engineer | Corp", "role_header"),
            meta_lines=[_para("2020–2022", "role_meta")],
            bullets=[_para("Old bullet 1"), _para("Old bullet 2")],
            role_id="Engineer | Corp",
        )
        sec = ResumeSection(
            title="Experience",
            heading=_para("Experience", "section_heading"),
            semantic_type="experience",
            roles=[role],
            body_paras=[],
            section_id="sec_1",
        )
        doc = _make_doc([sec])
        llm = [LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[LlmRole(header="Engineer | Corp", meta_lines=["2020–2022"], bullets=["New bullet"])],
            body_lines=[],
        )]
        result = apply_tailored(doc, llm)
        # Normal path: roles are used (not body_paras path)
        assert len(result.sections[0].roles) == 1
        assert result.sections[0].roles[0].bullets[0].text == "New bullet"
        assert result.sections[0].roles[0].header.text == "Engineer | Corp"

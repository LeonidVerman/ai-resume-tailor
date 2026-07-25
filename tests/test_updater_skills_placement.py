"""Tests for Phase 5 skills placement fixes.

Covers:
- _heading_like_style: Heading*/Title/Subtitle detection from style_name AND
  from the xml_proto's pStyle (sample 12: style_name empty, proto Subtitle).
- _make_extra_section: heading-like pStyle stripped from cloned body paras so
  injected doc-end skills don't re-parse as section headings.
- _update_body_section skills preserve-exception narrowing: leftover template
  skill items covered by the injected LLM lines are cleared; name/summary
  paras not covered stay preserved (sample 17).
"""
from __future__ import annotations

from lxml import etree

from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
from tailor.compiler.text_parser import LlmSection
from tailor.compiler.updater import (
    _heading_like_style,
    _make_extra_section,
    _update_body_section,
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _proto(pstyle: str | None) -> "etree._Element":
    ps = f'<w:pPr><w:pStyle w:val="{pstyle}"/></w:pPr>' if pstyle else "<w:pPr/>"
    return etree.fromstring(
        f'<w:p xmlns:w="{_W}">{ps}<w:r><w:t>x</w:t></w:r></w:p>'
    )


def _pm(text: str, pstyle: str | None = None, style_name: str | None = None,
        semantic: str = "paragraph", para_id: str = "") -> ParaModel:
    pm = ParaModel(
        text=text,
        style=ParaStyle(style_name=style_name, xml_proto=_proto(pstyle)),
        semantic=semantic,
    )
    pm.para_id = para_id
    return pm


def _proto_pstyle(pm: ParaModel) -> str | None:
    pPr = pm.style.xml_proto.find(f"{{{_W}}}pPr")
    if pPr is None:
        return None
    ps = pPr.find(f"{{{_W}}}pStyle")
    return ps.get(f"{{{_W}}}val") if ps is not None else None


class TestHeadingLikeStyle:
    def test_subtitle_in_proto_only(self):
        assert _heading_like_style(_pm("x", pstyle="Subtitle"))

    def test_title_in_style_name(self):
        pm = ParaModel(text="x", style=ParaStyle(style_name="Title"), semantic="paragraph")
        assert _heading_like_style(pm)

    def test_heading_with_space(self):
        pm = ParaModel(text="x", style=ParaStyle(style_name="Heading 3"), semantic="paragraph")
        assert _heading_like_style(pm)

    def test_body_text_is_not_heading_like(self):
        assert not _heading_like_style(_pm("x", pstyle="BodyText"))

    def test_no_style_at_all(self):
        pm = ParaModel(text="x", style=ParaStyle(), semantic="paragraph")
        assert not _heading_like_style(pm)


class TestMakeExtraSectionStyleNormalization:
    def test_subtitle_pstyle_stripped_from_body(self):
        arch = _pm("proto", pstyle="Subtitle")
        sec = _make_extra_section(
            LlmSection(heading="Technical Skills", semantic_type="skills",
                       body_lines=["A: b, c", "D: e"]),
            arch, arch,
        )
        assert len(sec.body_paras) == 2
        for bp in sec.body_paras:
            assert _proto_pstyle(bp) is None

    def test_plain_body_proto_untouched(self):
        arch = _pm("proto", pstyle=None)
        sec = _make_extra_section(
            LlmSection(heading="Technical Skills", semantic_type="skills",
                       body_lines=["A: b"]),
            arch, arch,
        )
        assert sec.body_paras[0].text == "A: b"


class TestSkillsPreserveExceptionNarrowed:
    def _skills_section(self) -> ResumeSection:
        body = [
            _pm("Design and Validation", para_id="para_1"),
            _pm("Statistical Analysis", para_id="para_2"),
            _pm("Product Development", para_id="para_3"),
            _pm("Jacob Howard", para_id="para_4"),
        ]
        sec = ResumeSection(
            title="CORE COMPETENCIES",
            heading=_pm("CORE COMPETENCIES", semantic="section_heading",
                        para_id="para_0"),
            semantic_type="skills",
            body_paras=body,
        )
        sec.section_id = "sec_sk"
        return sec

    def test_covered_leftovers_cleared_name_preserved(self):
        # LLM joins all skills into one line → the single slot gets it; the
        # leftover template skill items (covered by the line) are cleared,
        # while the name para (not covered) is preserved.
        sec = self._skills_section()
        llm = LlmSection(
            heading="CORE COMPETENCIES", semantic_type="skills",
            body_lines=[
                "Design and Validation, Statistical Analysis, Product Development",
            ],
        )
        result = _update_body_section(sec, llm, layout_bound=True)
        texts = {p.para_id: p.text for p in result.body_paras}
        assert "Statistical Analysis" in texts["para_1"]  # slot got LLM line
        assert texts["para_2"] == ""
        assert texts["para_3"] == ""
        assert texts["para_4"] == "Jacob Howard"

    def test_uncovered_paragraph_still_preserved(self):
        sec = self._skills_section()
        llm = LlmSection(
            heading="CORE COMPETENCIES", semantic_type="skills",
            body_lines=["Completely different skills line"],
        )
        result = _update_body_section(sec, llm, layout_bound=True)
        texts = {p.para_id: p.text for p in result.body_paras}
        # Not covered by the LLM content → old behaviour (preserve verbatim).
        assert texts["para_2"] == "Statistical Analysis"
        assert texts["para_4"] == "Jacob Howard"

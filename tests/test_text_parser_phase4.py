"""Tests for Phase 4 LLM-text parsing fixes.

Covers:
- Heading vocabulary: "EMPLOYMENT SUMMARY" (experience) and
  "EDUCATIONAL HISTORY" (education) recognised as section boundaries
  (sample 17).
- Glued-heading split: "EDUCATIONReally Great University" becomes a section
  boundary plus a content line; summary-type prefixes are never split
  (sample 28).
- Title-case fallback suppression right after a fresh pipe role-header so a
  2-word company line ("Lamna Healthcare") is not misdetected as a new
  section heading (sample 15).
"""
from __future__ import annotations

from tailor.compiler.text_parser import parse_llm_output, preprocess_resume_text


class TestHeadingVocabulary:
    def test_employment_summary_is_experience(self):
        text = (
            "JACOB HOWARD\n"
            "MECHANICAL ENGINEER\n\n"
            "EMPLOYMENT SUMMARY\n"
            "Lead Mechanical Engineer\n"
            "VALENTI AND ASSOCIATES | AUG 2016 - PRESENT\n"
            "- Spearheaded development of mechanical products.\n"
        )
        sections = parse_llm_output(text)
        exp = [s for s in sections if s.semantic_type == "experience"]
        assert len(exp) == 1
        assert exp[0].heading == "EMPLOYMENT SUMMARY"
        assert len(exp[0].roles) == 1
        assert exp[0].roles[0].bullets == [
            "Spearheaded development of mechanical products."
        ]

    def test_educational_history_is_education(self):
        text = (
            "EDUCATIONAL HISTORY\n"
            "Enjeti-Maynard Institute\n"
            "POSTGRADUATE CERTIFICATE | JAN 2015 - DEC 2016\n"
        )
        sections = parse_llm_output(text)
        assert sections[0].semantic_type == "education"


class TestGluedHeadingSplit:
    def test_education_prefix_split(self):
        out = preprocess_resume_text("EDUCATIONReally Great University (2012-2014)")
        assert out.splitlines() == [
            "EDUCATION",
            "Really Great University (2012-2014)",
        ]

    def test_summary_prefix_never_split(self):
        line = "SUMMARYMaster Degree of Project Engineering"
        assert preprocess_resume_text(line) == line

    def test_unknown_prefix_not_split(self):
        line = "NAVALENGINEERING"
        assert preprocess_resume_text(line) == line

    def test_plain_heading_untouched(self):
        assert preprocess_resume_text("EDUCATION") == "EDUCATION"

    def test_glued_education_becomes_section_boundary(self):
        # Sample 28 shape: glued education heading inside experience flow.
        text = (
            "WORK HISTORY\n"
            "2014-2016\n"
            "Mechanical Engineering Apprentice at Thynk Unlimited\n"
            "- Researched new materials-generation techniques.\n"
            "EDUCATIONReally Great University (2012-2014)\n"
            "BS in Mechanical Engineering\n"
        )
        sections = parse_llm_output(text)
        types = [s.semantic_type for s in sections]
        assert "education" in types
        edu = sections[types.index("education")]
        assert "Really Great University (2012-2014)" in edu.body_lines
        # The education lines must not leak into the experience section.
        exp = sections[types.index("experience")]
        exp_bullets = [b for r in exp.roles for b in r.bullets] + exp.body_lines
        assert not any("Really Great University" in b for b in exp_bullets)


class TestFreshPipeHeaderSuppression:
    _TEXT = (
        "EXPERIENCE\n"
        "Jan 20XX — present | Phlebotomist\n"
        "Lamna Healthcare\n"
        "- Performed venipuncture and capillary puncture procedures.\n"
        "- Processed blood samples according to established protocols.\n"
        "Feb 20XX — Jan 20XX | Phlebotomist\n"
        "Wholeness Healthcare\n"
        "- Maintained patient privacy and adhered to medical regulations.\n"
    )

    def test_company_line_not_a_section_heading(self):
        sections = parse_llm_output(self._TEXT)
        assert [s.heading for s in sections] == ["EXPERIENCE"]

    def test_company_line_becomes_role_meta(self):
        (sec,) = parse_llm_output(self._TEXT)
        assert len(sec.roles) == 2
        assert sec.roles[0].meta_lines == ["Lamna Healthcare"]
        assert len(sec.roles[0].bullets) == 2
        assert sec.roles[1].meta_lines == ["Wholeness Healthcare"]
        assert len(sec.roles[1].bullets) == 1

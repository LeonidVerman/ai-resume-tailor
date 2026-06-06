"""Tests for the band-aware newspaper-column multi-column parser fix.

Covers the full acceptance criteria for sample 31, non-regression for existing
templates, and unit tests for the conservative detection guard.
"""
from __future__ import annotations

import pytest
from pathlib import Path

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")

_NON_TRIGGER_TEMPLATES = [
    "1-Leonid_Verman_Resume_Template.docx",
    "6-Template1.docx",
    "9-Template4.docx",
    "20-Software-Engineer-Editable-Resume-Template-Download-in-docx-5.docx",
]


def _get_section(doc, title_lower: str):
    for sec in doc.sections:
        if sec.title.strip().lower() == title_lower:
            return sec
    return None


def _section_all_text(sec) -> str:
    parts = [p.text for p in sec.body_paras]
    for role in sec.roles:
        parts.append(role.header.text)
        parts.extend(m.text for m in role.meta_lines)
        parts.extend(b.text for b in role.bullets)
    return "\n".join(parts)


@pytest.fixture(scope="module")
def doc31():
    from tailor.compiler.docx_parser import parse_docx
    return parse_docx(_SAMPLE_31)


# ---------------------------------------------------------------------------
# Fix activation
# ---------------------------------------------------------------------------

def test_sample31_fix_applied(doc31):
    assert doc31.table_column_layout_fixed is True


def test_sample31_label_column_not_triggered(doc31):
    assert doc31.label_column_fixed is False


# ---------------------------------------------------------------------------
# EDUCATION section
# ---------------------------------------------------------------------------

def test_sample31_education_section_exists(doc31):
    assert _get_section(doc31, "education") is not None


def test_sample31_education_has_university(doc31):
    edu = _get_section(doc31, "education")
    assert edu is not None
    txt = _section_all_text(edu)
    assert "Fauget" in txt, "Fauget University should be in EDUCATION"


def test_sample31_education_has_cs_date(doc31):
    edu = _get_section(doc31, "education")
    assert edu is not None
    txt = _section_all_text(edu)
    assert "2010-2014" in txt, "Computer Science 2010-2014 should be in EDUCATION"


def test_sample31_education_has_address(doc31):
    """123 Anywhere St., Any City 2008-2011 is education column content."""
    edu = _get_section(doc31, "education")
    assert edu is not None
    txt = _section_all_text(edu)
    assert "2008-2011" in txt, "Education date '2008-2011' should be in EDUCATION section"


# ---------------------------------------------------------------------------
# SKILLS section
# ---------------------------------------------------------------------------

def test_sample31_skills_section_exists(doc31):
    assert _get_section(doc31, "skills") is not None, "SKILLS section must exist"


def test_sample31_skills_has_databases(doc31):
    skills = _get_section(doc31, "skills")
    assert skills is not None
    assert "Databases" in _section_all_text(skills)


def test_sample31_skills_has_networking(doc31):
    skills = _get_section(doc31, "skills")
    assert skills is not None
    assert "Networking" in _section_all_text(skills)


def test_sample31_skills_has_testing(doc31):
    skills = _get_section(doc31, "skills")
    assert skills is not None
    assert "testing" in _section_all_text(skills).lower()


# ---------------------------------------------------------------------------
# CERTIFICATION section
# ---------------------------------------------------------------------------

def test_sample31_certification_exists(doc31):
    assert _get_section(doc31, "certification") is not None


def test_sample31_certification_has_liceria(doc31):
    cert = _get_section(doc31, "certification")
    assert "Liceria" in _section_all_text(cert)


def test_sample31_certification_has_2019(doc31):
    cert = _get_section(doc31, "certification")
    assert "2019" in _section_all_text(cert)


def test_sample31_certification_has_fauget_company(doc31):
    """Fauget Company is a cert entry that must appear inside CERTIFICATION (not a fake section)."""
    cert = _get_section(doc31, "certification")
    assert cert is not None
    assert "Fauget Company" in _section_all_text(cert), (
        "Fauget Company (second cert entry) must be in CERTIFICATION body"
    )


def test_sample31_certification_has_2021(doc31):
    cert = _get_section(doc31, "certification")
    assert "2021" in _section_all_text(cert)


def test_sample31_certification_no_skills(doc31):
    cert = _get_section(doc31, "certification")
    cert_text = _section_all_text(cert)
    for snippet in ["Networking basics", "Operating Systems", "Unit testing",
                    "Integration testing", "Critical Thinking"]:
        assert snippet not in cert_text, f"{snippet!r} must not appear in CERTIFICATION"


def test_sample31_certification_no_contact(doc31):
    cert = _get_section(doc31, "certification")
    cert_text = _section_all_text(cert)
    assert "+123" not in cert_text
    assert "@techguruplus" not in cert_text


# ---------------------------------------------------------------------------
# WORK EXPERIENCE: 3 roles, no contact contamination
# ---------------------------------------------------------------------------

def test_sample31_work_experience_three_roles(doc31):
    we = _get_section(doc31, "work experience")
    assert we is not None
    assert len(we.roles) == 3, f"Expected 3 WE roles, got {[r.role_id for r in we.roles]}"


def test_sample31_work_experience_role_names(doc31):
    we = _get_section(doc31, "work experience")
    role_ids = [r.role_id for r in we.roles]
    assert any("Web Developer" in rid for rid in role_ids)
    assert any("Web Designer" in rid for rid in role_ids)
    assert any("Web Development Intern" in rid for rid in role_ids)


def test_sample31_we_roles_no_contact(doc31):
    """Phone and email must not appear inside WE role bullets."""
    we = _get_section(doc31, "work experience")
    assert we is not None
    bullet_text = "\n".join(
        b.text for role in we.roles for b in role.bullets
    )
    assert "+123" not in bullet_text, "Phone number must not be in WE role bullets"
    assert "@techguruplus" not in bullet_text, "Email must not be in WE role bullets"


# ---------------------------------------------------------------------------
# COURSE section
# ---------------------------------------------------------------------------

def test_sample31_course_section_exists(doc31):
    course = _get_section(doc31, "course")
    assert course is not None, "COURSE section must be present"


def test_sample31_course_has_borcelle_tech(doc31):
    course = _get_section(doc31, "course")
    assert "Borcelle Tech" in _section_all_text(course), (
        "Borcelle Tech must be in COURSE body (not a fake section)"
    )


def test_sample31_course_has_fauget_corp(doc31):
    course = _get_section(doc31, "course")
    assert "Fauget Corp" in _section_all_text(course), (
        "Fauget Corp must be in COURSE body (not a fake section)"
    )


def test_sample31_course_has_2019_and_2020(doc31):
    course = _get_section(doc31, "course")
    txt = _section_all_text(course)
    assert "2019" in txt
    assert "2020" in txt


# ---------------------------------------------------------------------------
# AWARDS section
# ---------------------------------------------------------------------------

def test_sample31_awards_section_exists(doc31):
    awards = _get_section(doc31, "awards")
    assert awards is not None, "AWARDS section must be present"


def test_sample31_awards_has_liceria(doc31):
    awards = _get_section(doc31, "awards")
    assert "Liceria" in _section_all_text(awards), (
        "Liceria & Co. must be in AWARDS body (not a fake section)"
    )


def test_sample31_awards_has_best_web_designer(doc31):
    awards = _get_section(doc31, "awards")
    assert "Best Web Designer" in _section_all_text(awards)


def test_sample31_no_fake_org_sections(doc31):
    """Fauget Company, Borcelle Tech, Fauget Corp, Liceria (inside AWARDS) must
    not become top-level sections — they must be absorbed as body paragraphs."""
    section_titles = {s.title.strip() for s in doc31.sections}
    for name in ("Fauget Company", "Borcelle Tech", "Fauget Corp"):
        assert name not in section_titles, f"{name!r} must not be a top-level section"


# ---------------------------------------------------------------------------
# Non-regression: templates that must NOT trigger the fix
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", _NON_TRIGGER_TEMPLATES)
def test_non_trigger_templates(fname):
    from tailor.compiler.docx_parser import parse_docx
    path = _DOCX_DIR / fname
    if not path.exists():
        pytest.skip(f"Sample not found: {fname}")
    doc = parse_docx(str(path))
    assert doc.table_column_layout_fixed is False, (
        f"{fname}: must not trigger table_column_layout_fixed"
    )


# ---------------------------------------------------------------------------
# Conservative detection guard unit tests
# ---------------------------------------------------------------------------

def test_skills_column_guard_no_trigger_without_skills():
    from tailor.compiler.docx_parser import _KNOWN_SECTION_NAMES_LOWER, _SKILLS_COLUMN_NAMES
    left, right = "Experience", "Education"
    assert left.lower() in _KNOWN_SECTION_NAMES_LOWER
    assert right.lower() in _KNOWN_SECTION_NAMES_LOWER
    has_skills = left.lower() in _SKILLS_COLUMN_NAMES or right.lower() in _SKILLS_COLUMN_NAMES
    assert not has_skills, "Experience|Education must not trigger skills-column guard"


def test_skills_column_guard_triggers_with_skills():
    from tailor.compiler.docx_parser import _KNOWN_SECTION_NAMES_LOWER, _SKILLS_COLUMN_NAMES
    left, right = "EDUCATION", "SKILLS"
    assert left.lower() in _KNOWN_SECTION_NAMES_LOWER
    assert right.lower() in _KNOWN_SECTION_NAMES_LOWER
    has_skills = left.lower() in _SKILLS_COLUMN_NAMES or right.lower() in _SKILLS_COLUMN_NAMES
    assert has_skills, "EDUCATION|SKILLS must satisfy the skills-column guard"


# ---------------------------------------------------------------------------
# Diagnostics: _is_skills_list_para — true positives
# ---------------------------------------------------------------------------

def test_skills_list_comma_separated():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert _is_skills_list_para("Java, Python, AWS, Docker, PostgreSQL")


def test_skills_list_space_separated_long():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert _is_skills_list_para(
        "Networking basics Operating Systems Cross-platform software Encryption"
    )


def test_skills_list_testing_keywords():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert _is_skills_list_para(
        "Unit testing Integration testing System testing Critical Thinking Time management"
    )


def test_skills_list_tech_comma():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert _is_skills_list_para("React, TypeScript, Node.js, PostgreSQL, Redis")


# ---------------------------------------------------------------------------
# Diagnostics: _is_skills_list_para — false positives that must return False
# ---------------------------------------------------------------------------

def test_skills_fp_section_heading_work_experience():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("WORK EXPERIENCE")


def test_skills_fp_section_heading_professional_experience():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Professional Experience")


def test_skills_fp_section_heading_educational():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("EDUCATIONAL HISTORY")


def test_skills_fp_section_heading_software_engineer():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("SOFTWARE ENGINEER")


def test_skills_fp_contact_phone():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Landline: (123) 456 7890")


def test_skills_fp_contact_email():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Email: hello@techguruplus.com")


def test_skills_fp_contact_website():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Website: www.techguruplus.com")


def test_skills_fp_institution_name():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("University of Lovelstyne")


def test_skills_fp_person_name():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Philippe Stolvan")


def test_skills_fp_role_title_senior_engineer():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Senior Engineer")


def test_skills_fp_role_title_project_coordinator():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Project Coordinator")


def test_skills_fp_date_line():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Jan 2015 - present")


def test_skills_fp_action_verb_sentence():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para(
        "Developed trading systems for the Japanese financial market"
    )


def test_skills_fp_long_action_sentence():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para(
        "Implemented unit and integration testing practices to validate web application behavior"
    )


def test_skills_fp_connector_word_sentence():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Implemented CI/CD pipelines using Jenkins and Docker")


# ---------------------------------------------------------------------------
# Diagnostics: _is_contact_para
# ---------------------------------------------------------------------------

def test_contact_phone():
    from scripts.parser_diagnostics import _is_contact_para
    assert _is_contact_para("+123-456-7890")


def test_contact_email():
    from scripts.parser_diagnostics import _is_contact_para
    assert _is_contact_para("hello@techguruplus.com")


def test_contact_email_mixed_not_flagged():
    """Email embedded in mixed address line is NOT standalone → should not flag."""
    from scripts.parser_diagnostics import _is_contact_para
    assert not _is_contact_para("hello@techguruplus.com 123 Anywhere St.")


def test_contact_labelled():
    from scripts.parser_diagnostics import _is_contact_para
    assert _is_contact_para("Landline: (123) 456 7890")


def test_contact_false_positive_bullet():
    from scripts.parser_diagnostics import _is_contact_para
    assert not _is_contact_para("Improved system performance by 40% through query optimization")


def test_contact_false_positive_experience():
    from scripts.parser_diagnostics import _is_contact_para
    assert not _is_contact_para("Led cross-functional teams of 5 engineers")


# ---------------------------------------------------------------------------
# Diagnostics: context-aware detector behaviour
# ---------------------------------------------------------------------------

def test_skills_detector_skips_without_multicolumn():
    """Skills diagnostics must not fire for non-multicolumn files."""
    from scripts.parser_diagnostics import detect_table_skills_inside_non_skills
    sections = [
        {
            "semantic_type": "certifications",
            "raw_title": "CERTIFICATION",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "Java, Python, AWS, Docker, PostgreSQL", "parser_semantic": "paragraph",
                 "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    # Without multicolumn context → no issues
    assert detect_table_skills_inside_non_skills(sections, has_multicolumn=False) == []
    # With multicolumn context → should flag
    issues = detect_table_skills_inside_non_skills(sections, has_multicolumn=True)
    assert len(issues) == 1
    assert issues[0].code == "TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION"


def test_skills_detector_skips_section_heading_para():
    """section_heading paragraphs must never be flagged."""
    from scripts.parser_diagnostics import detect_table_skills_inside_non_skills
    sections = [
        {
            "semantic_type": "certifications",
            "raw_title": "CERTIFICATION",
            "section_id": "sec_1",
            "paragraphs": [
                # This would look skills-like without the semantic guard
                {"text": "Java Python AWS Docker Kubernetes Terraform", "parser_semantic": "section_heading",
                 "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    issues = detect_table_skills_inside_non_skills(sections, has_multicolumn=True)
    assert issues == [], "section_heading paragraphs must not be flagged"


def test_contamination_no_duplicate_when_specific_fires():
    """TABLE_COLUMN_CONTAMINATION_SUSPECTED must not fire for a section
    already covered by TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION."""
    from scripts.parser_diagnostics import (
        detect_table_skills_inside_non_skills,
        detect_table_column_contamination_suspected,
    )
    sections = [
        {
            "semantic_type": "certifications",
            "raw_title": "CERTIFICATION",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "Java, Python, AWS, Docker, PostgreSQL", "parser_semantic": "paragraph",
                 "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    skills_issues = detect_table_skills_inside_non_skills(sections, has_multicolumn=True)
    assert len(skills_issues) == 1

    sections_covered = {i.section_id for i in skills_issues if i.section_id}
    contamination = detect_table_column_contamination_suspected(sections, has_multicolumn=True)
    # Simulate deduplication as done in analyse_file
    deduped = [i for i in contamination if i.section_id not in sections_covered]
    assert deduped == [], "Contamination should not fire when specific diagnostic already covers it"


def test_contact_in_experience_section():
    """Contact text inside experience must be flagged."""
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    sections = [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "+123-456-7890", "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    issues = detect_table_contact_inside_experience(sections)
    assert len(issues) == 1
    assert issues[0].code == "TABLE_CONTACT_INSIDE_EXPERIENCE"


def test_contact_in_contact_section_not_flagged():
    """Contact info inside a Contact section must NOT trigger experience diagnostic."""
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    sections = [
        {
            "semantic_type": "other",
            "raw_title": "Contact",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "+123-456-7890", "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    issues = detect_table_contact_inside_experience(sections)
    assert issues == [], "Contact info inside Contact section must not be flagged"


def test_normal_tech_bullet_in_experience_not_flagged():
    """Normal technical achievement bullets in experience must not be flagged."""
    from scripts.parser_diagnostics import detect_table_skills_inside_non_skills
    sections = [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "Developed microservices using Python, Go and AWS Lambda",
                 "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    # Experience is in the skip set, so never flagged
    issues = detect_table_skills_inside_non_skills(sections, has_multicolumn=True)
    assert issues == []


# ---------------------------------------------------------------------------
# TABLE_EDUCATION_INSIDE_EXPERIENCE — true positive (real education in XP)
# ---------------------------------------------------------------------------

def test_education_true_positive_degree_abbreviation():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    sections = [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "Harvard University, Bachelor of Science, 2015",
                 "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    issues = detect_table_education_inside_experience(sections)
    assert len(issues) == 1
    assert issues[0].code == "TABLE_EDUCATION_INSIDE_EXPERIENCE"


def test_education_true_positive_gpa():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    sections = [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": "B.S. in Computer Science, GPA 3.8",
                 "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [],
        }
    ]
    issues = detect_table_education_inside_experience(sections)
    assert len(issues) == 1


# ---------------------------------------------------------------------------
# TABLE_EDUCATION_INSIDE_EXPERIENCE — false positives that MUST be silent
# ---------------------------------------------------------------------------

def _edu_sections_with(text: str, semantic: str = "paragraph") -> list[dict]:
    return [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": text, "parser_semantic": semantic, "para_id": "p1"},
            ],
            "roles": [],
        }
    ]


def test_education_fp_action_verb_bullet():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Built React/TypeScript interfaces for internal tooling")
    ) == []


def test_education_fp_supported_bullet():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Supported the launch of the mobile payments feature")
    ) == []


def test_education_fp_architected_bullet():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Architected backend services for the trading platform")
    ) == []


def test_education_fp_led_bullet():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Led a backend engineering team of 6 across two time zones")
    ) == []


def test_education_fp_role_meta_date():
    """Role meta lines are excluded by parser_semantic filter."""
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("May 2023 - August 2023", semantic="role_meta")
    ) == []


def test_education_fp_role_header():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Senior Software Engineer", semantic="role_header")
    ) == []


def test_education_fp_role_header_pipe():
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("Acme Corp | Backend Developer", semantic="role_header")
    ) == []


def test_education_fp_company_address():
    """Company+address line (role meta) excluded by semantic filter."""
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    assert detect_table_education_inside_experience(
        _edu_sections_with("TIMMERMAN INDUSTRIES - 123 Anywhere St.", semantic="role_meta")
    ) == []


def test_education_fp_company_pipe():
    """Company | title line excluded by | guard."""
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    # Even if semantic is wrongly paragraph, the | guard catches it
    assert detect_table_education_inside_experience(
        _edu_sections_with("Office manager | The Phone Company")
    ) == []


def test_education_fp_role_assigned_to_role():
    """Paragraphs whose para_id appears in role lists are excluded."""
    from scripts.parser_diagnostics import detect_table_education_inside_experience
    sections = [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                # Would trigger _is_education_line, but it's role-assigned
                {"text": "Bachelor of Science in Engineering, GPA 4.0",
                 "parser_semantic": "paragraph", "para_id": "p1"},
            ],
            "roles": [
                {"role_id": "r1", "bullet_para_ids": ["p1"],
                 "header_para_ids": [], "meta_para_ids": []},
            ],
        }
    ]
    assert detect_table_education_inside_experience(sections) == []


# ---------------------------------------------------------------------------
# TABLE_CONTACT_INSIDE_EXPERIENCE — true positives
# ---------------------------------------------------------------------------

def _contact_xp_sections_with(text: str, semantic: str = "paragraph") -> list[dict]:
    return [
        {
            "semantic_type": "experience",
            "raw_title": "WORK EXPERIENCE",
            "section_id": "sec_1",
            "paragraphs": [
                {"text": text, "parser_semantic": semantic, "para_id": "p1"},
            ],
            "roles": [],
        }
    ]


def test_contact_xp_true_positive_standalone_email():
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    issues = detect_table_contact_inside_experience(
        _contact_xp_sections_with("hello@example.com")
    )
    assert len(issues) == 1
    assert issues[0].code == "TABLE_CONTACT_INSIDE_EXPERIENCE"


def test_contact_xp_true_positive_labelled_phone():
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    issues = detect_table_contact_inside_experience(
        _contact_xp_sections_with("Phone: +1 123-456-7890")
    )
    assert len(issues) == 1


# ---------------------------------------------------------------------------
# TABLE_CONTACT_INSIDE_EXPERIENCE — false positives that MUST be silent
# ---------------------------------------------------------------------------

def test_contact_xp_fp_role_meta_date():
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    assert detect_table_contact_inside_experience(
        _contact_xp_sections_with("May 2023 - August 2023", semantic="role_meta")
    ) == []


def test_contact_xp_fp_company_address_role_meta():
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    assert detect_table_contact_inside_experience(
        _contact_xp_sections_with("TIMMERMAN INDUSTRIES - 123 Anywhere St.", semantic="role_meta")
    ) == []


def test_contact_xp_fp_mixed_email_sentence():
    """Email embedded inside a sentence is NOT a standalone contact line."""
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    assert detect_table_contact_inside_experience(
        _contact_xp_sections_with("Worked on the project at hello@example.com repository")
    ) == []


def test_contact_xp_fp_role_header():
    from scripts.parser_diagnostics import detect_table_contact_inside_experience
    assert detect_table_contact_inside_experience(
        _contact_xp_sections_with("Senior Software Engineer", semantic="role_header")
    ) == []


# ---------------------------------------------------------------------------
# _is_education_line unit tests
# ---------------------------------------------------------------------------

def test_education_line_degree_abbreviation():
    from scripts.parser_diagnostics import _is_education_line
    assert _is_education_line("B.S. in Computer Science, GPA 3.8")


def test_education_line_bachelor_phrase():
    from scripts.parser_diagnostics import _is_education_line
    assert _is_education_line("Harvard University, Bachelor of Science, 2015")


def test_education_line_masters():
    from scripts.parser_diagnostics import _is_education_line
    assert _is_education_line("M.S. Information Systems")


def test_education_line_phd():
    from scripts.parser_diagnostics import _is_education_line
    assert _is_education_line("Ph.D. in Computer Science, Stanford University")


def test_education_line_fp_action_verb():
    from scripts.parser_diagnostics import _is_education_line
    assert not _is_education_line("Built React/TypeScript interfaces for clients")


def test_education_line_fp_pipe_role_header():
    from scripts.parser_diagnostics import _is_education_line
    assert not _is_education_line("Senior Engineer | Acme Corp")


def test_education_line_fp_date():
    from scripts.parser_diagnostics import _is_education_line
    assert not _is_education_line("January 2020 – Present")


def test_education_line_fp_company_name_with_school():
    """Company name containing 'school' alone should NOT trigger."""
    from scripts.parser_diagnostics import _is_education_line
    assert not _is_education_line("Old School Media Company")


def test_education_line_fp_institute_in_company():
    from scripts.parser_diagnostics import _is_education_line
    assert not _is_education_line("Chartered Institute of Marketing")


# ---------------------------------------------------------------------------
# Sample 35: trailing single-column section ordering fix
#
# Sample 35 is a PDF-converted DOCX with three Word sections:
#   - Section 1 (header, single-col): name + contact
#   - Section 2 (2-col 66pt+440pt): date sidebar + Experience content
#   - Section 3 (2-col 66pt+440pt): date sidebar + additional jobs + Education + Skills
#   - Section 4 (single-col):        Skills content overflow + Other skills + Projects
#
# Before the fix, Section 4 was assigned to col=0 (left stream), placing
# "Projects" before "Experience" as the first detected section.
# After the fix, Section 4 is routed to tail_stream, correctly appended after
# left+right so Experience appears first.
# ---------------------------------------------------------------------------

_SAMPLE_35 = str(_DOCX_DIR / "35-Gleb_Zernov_Resume.docx")


@pytest.fixture(scope="module")
def doc35():
    from tailor.compiler.docx_parser import parse_docx
    return parse_docx(_SAMPLE_35)


def test_sample35_newspaper_fix_applied(doc35):
    assert doc35.table_column_layout_fixed is True


def test_sample35_experience_is_first_section(doc35):
    """Experience must be the first detected section — not Projects."""
    assert len(doc35.sections) >= 1
    first = doc35.sections[0].title.strip().lower()
    assert first == "experience", f"Expected 'experience' first, got {first!r}"


def test_sample35_projects_not_before_experience(doc35):
    """Projects must not appear before Experience in section order."""
    titles = [s.title.strip().lower() for s in doc35.sections]
    if "projects" in titles and "experience" in titles:
        assert titles.index("experience") < titles.index("projects"), (
            f"Experience must precede Projects. Got: {titles}"
        )


def test_sample35_experience_section_has_roles(doc35):
    """Experience section must contain at least 4 parsed roles."""
    exp = next((s for s in doc35.sections if s.title.strip().lower() == "experience"), None)
    assert exp is not None, "Experience section must exist"
    assert len(exp.roles) >= 4, f"Expected >= 4 roles, got {len(exp.roles)}"


def test_sample35_experience_has_ondo_perps(doc35):
    exp = next((s for s in doc35.sections if s.title.strip().lower() == "experience"), None)
    assert exp is not None
    role_headers = [r.header.text for r in exp.roles if r.header]
    assert any("Ondo" in h or "Enclave" in h or "Stellar" in h for h in role_headers), (
        f"Expected known employer in roles. Got: {role_headers}"
    )


def test_sample35_education_section_exists(doc35):
    assert any(s.title.strip().lower() == "education" for s in doc35.sections)


def test_sample35_projects_section_exists(doc35):
    assert any("projects" in s.title.strip().lower() for s in doc35.sections), (
        "Projects section must exist (in tail_stream after Experience)"
    )


def test_sample35_header_paras_reduced(doc35):
    """header_paras should be significantly less than 63 (the pre-fix value)."""
    assert len(doc35.header_paras) < 63, (
        f"header_paras={len(doc35.header_paras)} — should be < 63 after tail_stream fix"
    )

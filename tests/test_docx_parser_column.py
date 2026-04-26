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
    "32-Software-Engineer-Editable-Resume-Template-Download-in-docx-1-1.docx",
    "29-Programmer-Editable-Resume-Template-Download-in-docx.docx",
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
# Diagnostics: _is_skills_list_para unit tests
# ---------------------------------------------------------------------------

def test_skills_list_para_comma_separated():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert _is_skills_list_para("Python, JavaScript, SQL, Docker, Kubernetes")


def test_skills_list_para_not_action_verb_sentence():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Developed a microservices architecture for high-traffic APIs")


def test_skills_list_para_not_full_sentence():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para(
        "Led a team of 8 engineers to deliver a major product redesign ahead of schedule."
    )


def test_skills_list_para_connector_words():
    from scripts.parser_diagnostics import _is_skills_list_para
    assert not _is_skills_list_para("Implemented CI/CD pipelines using Jenkins and Docker")


# ---------------------------------------------------------------------------
# Diagnostics: contact-in-experience detection
# ---------------------------------------------------------------------------

def test_contact_detection_phone():
    from scripts.parser_diagnostics import _is_contact_para
    assert _is_contact_para("+123-456-7890")


def test_contact_detection_email():
    from scripts.parser_diagnostics import _is_contact_para
    assert _is_contact_para("hello@techguruplus.com 123 Anywhere St.")


def test_contact_detection_normal_bullet():
    from scripts.parser_diagnostics import _is_contact_para
    assert not _is_contact_para("Improved system performance by 40% through query optimization")

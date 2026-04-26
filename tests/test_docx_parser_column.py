"""Tests for the newspaper/table multi-column parser fix.

Covers:
- Sample 31: table_column_layout_fixed=True, clean CERTIFICATION, WE with 3 roles,
  skills not in CERTIFICATION.
- Non-regression: other templates do not trigger the fix.
- Conservative detection: templates without a skills-column split do not trigger.
"""
from __future__ import annotations

import pytest
from pathlib import Path

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")

# Templates that must NOT trigger the fix (non-regression set)
_NON_TRIGGER_TEMPLATES = [
    "1-Leonid_Verman_Resume_Template.docx",
    "6-Template1.docx",
    "9-Template4.docx",
    "32-Software-Engineer-Editable-Resume-Template-Download-in-docx-1-1.docx",
    "29-Programmer-Editable-Resume-Template-Download-in-docx.docx",
    "20-Software-Engineer-Editable-Resume-Template-Download-in-docx-5.docx",
]

_SKILLS_CONTAMINATION_SNIPPETS = [
    "Networking basics",
    "Operating Systems",
    "Cross-platform software",
    "Encryption",
    "Unit testing",
    "Integration testing",
    "System testing",
    "Critical Thinking",
    "Time management",
]


def _get_section(doc, title_lower: str):
    for sec in doc.sections:
        if sec.title.strip().lower() == title_lower:
            return sec
    return None


def _section_all_text(sec) -> str:
    parts = []
    for p in sec.body_paras:
        parts.append(p.text)
    for role in sec.roles:
        parts.append(role.header.text)
        for m in role.meta_lines:
            parts.append(m.text)
        for b in role.bullets:
            parts.append(b.text)
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
    """Newspaper-col fix and label-col fix must not both apply."""
    assert doc31.label_column_fixed is False


# ---------------------------------------------------------------------------
# Work Experience: 3 roles preserved
# ---------------------------------------------------------------------------

def test_sample31_work_experience_has_section(doc31):
    we = _get_section(doc31, "work experience")
    assert we is not None, "WORK EXPERIENCE section must be present"


def test_sample31_work_experience_three_roles(doc31):
    we = _get_section(doc31, "work experience")
    assert we is not None
    assert len(we.roles) == 3, f"Expected 3 WE roles, got {len(we.roles)}: {[r.role_id for r in we.roles]}"


def test_sample31_work_experience_role_names(doc31):
    we = _get_section(doc31, "work experience")
    assert we is not None
    role_ids = [r.role_id for r in we.roles]
    assert any("Web Developer" in rid for rid in role_ids), f"Missing Web Developer in {role_ids}"
    assert any("Web Designer" in rid for rid in role_ids), f"Missing Web Designer in {role_ids}"
    assert any("Web Development Intern" in rid for rid in role_ids), f"Missing Web Development Intern in {role_ids}"


# ---------------------------------------------------------------------------
# CERTIFICATION: no skills contamination
# ---------------------------------------------------------------------------

def test_sample31_certification_exists(doc31):
    cert = _get_section(doc31, "certification")
    assert cert is not None, "CERTIFICATION section must be present"


def test_sample31_certification_no_skills_content(doc31):
    cert = _get_section(doc31, "certification")
    assert cert is not None
    cert_text = _section_all_text(cert)
    for snippet in _SKILLS_CONTAMINATION_SNIPPETS:
        assert snippet not in cert_text, (
            f"Skill snippet {snippet!r} found in CERTIFICATION section "
            f"(cross-column contamination not fixed)"
        )


def test_sample31_certification_no_education_date_contamination(doc31):
    """'123 Anywhere St., Any City 2008-2011' must not appear in CERTIFICATION."""
    cert = _get_section(doc31, "certification")
    assert cert is not None
    cert_text = _section_all_text(cert)
    assert "2008-2011" not in cert_text, (
        "Education date line '2008-2011' found in CERTIFICATION (cross-column contamination)"
    )


# ---------------------------------------------------------------------------
# CERTIFICATION: correct content present
# ---------------------------------------------------------------------------

def test_sample31_certification_has_liceria(doc31):
    cert = _get_section(doc31, "certification")
    assert cert is not None
    cert_text = _section_all_text(cert)
    assert "Liceria" in cert_text, "Liceria & Co. should be in CERTIFICATION"


# ---------------------------------------------------------------------------
# Skills content separated from CERTIFICATION
# ---------------------------------------------------------------------------

def test_sample31_skills_not_in_certification(doc31):
    """Skills-like text must appear in a section other than CERTIFICATION."""
    cert = _get_section(doc31, "certification")
    assert cert is not None
    cert_text = _section_all_text(cert)
    # The key contamination keywords must not be in CERTIFICATION
    contaminated = [s for s in _SKILLS_CONTAMINATION_SNIPPETS if s in cert_text]
    assert not contaminated, (
        f"Skills contamination still present in CERTIFICATION: {contaminated}"
    )


def test_sample31_skills_section_exists(doc31):
    """After the fix a SKILLS section should be recognised from the tab-split heading."""
    skills = _get_section(doc31, "skills")
    assert skills is not None, "SKILLS section should be present after tab-split fix"


# ---------------------------------------------------------------------------
# EDUCATION section recognised
# ---------------------------------------------------------------------------

def test_sample31_education_section_exists(doc31):
    """The EDUCATION heading should be split from the merged EDUCATIONSKILLS para."""
    edu = _get_section(doc31, "education")
    assert edu is not None, "EDUCATION section should be present after tab-split fix"


# ---------------------------------------------------------------------------
# Non-regression: templates that must NOT trigger
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fname", _NON_TRIGGER_TEMPLATES)
def test_non_trigger_templates(fname):
    from tailor.compiler.docx_parser import parse_docx
    path = _DOCX_DIR / fname
    if not path.exists():
        pytest.skip(f"Sample not found: {fname}")
    doc = parse_docx(str(path))
    assert doc.table_column_layout_fixed is False, (
        f"{fname}: table_column_layout_fixed should be False (no skills-column split present)"
    )


# ---------------------------------------------------------------------------
# Conservative detection: no skills-column split → no trigger
# ---------------------------------------------------------------------------

def test_skills_column_guard_no_trigger_without_skills():
    """A document with Experience|Education tab split but NO SKILLS column must not trigger."""
    from tailor.compiler.docx_parser import _tab_split_texts, _KNOWN_SECTION_NAMES_LOWER, _SKILLS_COLUMN_NAMES
    # Simulate template 32's tab split: ("Experience", "Education")
    # Neither side is in _SKILLS_COLUMN_NAMES → should not trigger
    left, right = "Experience", "Education"
    assert left.lower() in _KNOWN_SECTION_NAMES_LOWER
    assert right.lower() in _KNOWN_SECTION_NAMES_LOWER
    has_skills = (
        left.lower() in _SKILLS_COLUMN_NAMES
        or right.lower() in _SKILLS_COLUMN_NAMES
    )
    assert not has_skills, "Experience|Education must not be treated as a skills-column split"


def test_skills_column_guard_triggers_with_skills():
    """EDUCATION|SKILLS split must satisfy the skills-column guard."""
    from tailor.compiler.docx_parser import _KNOWN_SECTION_NAMES_LOWER, _SKILLS_COLUMN_NAMES
    left, right = "EDUCATION", "SKILLS"
    assert left.lower() in _KNOWN_SECTION_NAMES_LOWER
    assert right.lower() in _KNOWN_SECTION_NAMES_LOWER
    has_skills = (
        left.lower() in _SKILLS_COLUMN_NAMES
        or right.lower() in _SKILLS_COLUMN_NAMES
    )
    assert has_skills, "EDUCATION|SKILLS must satisfy the skills-column guard"

"""Tests for Phase 6 role-header detection in the DOCX parser (sample 33).

Covers _relabel_implicit_role_headers:
- Undated ALL-CAPS job-title lines are promoted to role_header when the
  section shows role structure elsewhere.
- No promotion without a role-structure signal (avoids false positives in
  bullet-only sections).
- A role_meta whose text minus the date is an ALL-CAPS job title
  ("PROGRAMMER 2019") is promoted to role_header.
- Mixed-case dated titles ("Project Manager (2023 - Present)") stay
  role_meta — the date-first path depends on them.
"""
from __future__ import annotations

from tailor.compiler.docx_parser import _relabel_implicit_role_headers
from tailor.compiler.models import ParaModel, ParaStyle


def _para(text: str, semantic: str, para_id: str = "") -> ParaModel:
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


class TestUndatedAllCapsRoleHeader:
    def test_promoted_when_section_has_role_signal(self):
        paras = [
            _para("SOFTWARE ENGINEER", "paragraph", "p1"),
            _para("Collaborated with a dynamic team.", "paragraph", "p2"),
            _para("JUNIOR SOFTWARE ENGINEER", "paragraph", "p3"),
            _para("Assisted senior engineers.", "paragraph", "p4"),
            _para("PROGRAMMER 2019", "role_meta", "p5"),
            _para("Assisted in the development of unit tests.", "paragraph", "p6"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "role_header"
        assert paras[2].semantic == "role_header"

    def test_not_promoted_without_role_signal(self):
        paras = [
            _para("SOFTWARE ENGINEER", "paragraph", "p1"),
            _para("Collaborated with a dynamic team.", "paragraph", "p2"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "paragraph"

    def test_mixed_case_title_not_promoted_by_undated_variant(self):
        # The role_meta signal sits beyond the two-para lookahead, so only
        # the undated variant could fire — and it requires ALL-CAPS text.
        paras = [
            _para("Software Engineer", "paragraph", "p1"),
            _para("Collaborated with a dynamic team.", "paragraph", "p2"),
            _para("Did more things.", "paragraph", "p3"),
            _para("PROGRAMMER 2019", "role_meta", "p4"),
            _para("Did things.", "paragraph", "p5"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "paragraph"


class TestDatedTitleRoleMeta:
    def test_all_caps_dated_title_promoted(self):
        paras = [
            _para("PROGRAMMER 2019", "role_meta", "p1"),
            _para("Assisted in the development of unit tests.", "paragraph", "p2"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "role_header"

    def test_plain_date_range_stays_role_meta(self):
        paras = [
            _para("June 20XX - Present", "role_meta", "p1"),
            _para("Assistant Manager", "role_header", "p2"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "role_meta"

    def test_mixed_case_paren_date_title_stays_role_meta(self):
        # Sample 24 depends on "Project Manager (2023 - Present)" remaining
        # role_meta (its date-first flow keys on the role_meta semantic).
        paras = [
            _para("Project Manager (2023 - Present)", "role_meta", "p1"),
            _para("Prepare project worksheets.", "paragraph", "p2"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "role_meta"

    def test_all_caps_company_year_not_promoted(self):
        paras = [
            _para("ACME CORP 2019", "role_meta", "p1"),
            _para("Did things.", "paragraph", "p2"),
        ]
        _relabel_implicit_role_headers(paras)
        assert paras[0].semantic == "role_meta"

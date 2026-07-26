"""Tests for Phase 8 _group_roles refinements.

Covers:
- Combined Pattern-B headers ("2023 Ginyard International Co. Junior software
  developer"): following long/sentence paragraphs are bullets, and the next
  combined year+company line is a role boundary (sample 16).
- Company lines are never bullet slots: corporate suffixes right after a
  role_header stay header lines (samples 23/30), and a company/city-state
  line directly after the date meta goes to header_extra (sample 39).
- A bare job-title line inside the bullet stream goes to header_extra
  instead of becoming a slot (sample 17 'Mechanical Engineer').
"""
from __future__ import annotations

from tailor.compiler.docx_parser import _group_roles, _is_company_like_line
from tailor.compiler.models import ParaModel, ParaStyle


def _para(text: str, semantic: str, para_id: str = "") -> ParaModel:
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


class TestCompanyLikeLine:
    def test_corporate_suffixes(self):
        assert _is_company_like_line("Lopelski Builders, Inc.")
        assert _is_company_like_line("Giggling Platypus Co.")
        assert _is_company_like_line("2024 Larana. Inc.")

    def test_city_state(self):
        assert _is_company_like_line("Pineapple Enterprises Santa Monica, CA")

    def test_sentences_are_not_company_like(self):
        assert not _is_company_like_line(
            "Ensures compliance to safety and performance standards."
        )
        assert not _is_company_like_line("Oversees engineering projects")


class TestCombinedPatternBHeaders:
    def test_sample16_shape_two_roles(self):
        paras = [
            _para("2023 Ginyard International Co. Junior software developer",
                  "role_meta", "p74"),
            _para("set-up, maintenance and ongoing development of continuous "
                  "build/ integration infrastructure developing pipeline",
                  "paragraph", "p76"),
            _para("participate in technical discussions to aid system design, "
                  "analysis, and troubleshooting", "paragraph", "p78"),
            _para("2024 Larana. Inc.", "role_meta", "p80"),
            _para("Senior Devops engineer", "paragraph", "p82"),
            _para("Deliver the project from design to testing, including new "
                  "programs, enhancements and modifications", "paragraph", "p84"),
        ]
        roles = _group_roles(paras)
        assert len(roles) == 2
        assert [b.para_id for b in roles[0].bullets] == ["p76", "p78"]
        assert roles[1].header.para_id == "p80"
        # Title line stays header_extra; the long description is the bullet.
        assert [x.para_id for x in roles[1].header_extra] == ["p82"]
        assert [b.para_id for b in roles[1].bullets] == ["p84"]


class TestCompanyLinesNeverSlots:
    def test_corporate_suffix_after_header_stays_header_line(self):
        # Sample 23: 'Lopelski Builders, Inc.' ends with '.' but must not be
        # treated as sentence content.
        paras = [
            _para("Senior Engineer", "role_header", "p71"),
            _para("Lopelski Builders, Inc.", "paragraph", "p73"),
            _para("2013-2017", "role_meta", "p75"),
            _para("Oversees engineering projects", "paragraph", "p77"),
        ]
        (role,) = _group_roles(paras)
        assert [x.para_id for x in role.header_extra] == ["p73"]
        assert [b.para_id for b in role.bullets] == ["p77"]

    def test_company_after_date_meta_goes_to_header_extra(self):
        # Sample 39: date meta then company line then bullets.
        paras = [
            _para("Backend Developer", "role_header", "p33"),
            _para("Jan / 2021-Ongoing", "role_meta", "p34"),
            _para("Pineapple Enterprises Santa Monica, CA", "paragraph", "p36"),
            _para("Designed and implemented a scalable database architecture "
                  "for web applications.", "paragraph", "p37"),
        ]
        (role,) = _group_roles(paras)
        assert [x.para_id for x in role.header_extra] == ["p36"]
        assert [b.para_id for b in role.bullets] == ["p37"]


class TestBareTitleInBulletStream:
    def test_next_role_title_not_a_slot(self):
        # Sample 17: 'Mechanical Engineer' (next role's title) sits between
        # role 1's description paras and role 2's pipe header.
        paras = [
            _para("VALENTI AND ASSOCIATES | AUG 2016 - PRESENT",
                  "role_header", "p24"),
            _para("Works collaboratively with operations technicians, "
                  "supervisors, and vendors to build things", "paragraph", "p28"),
            _para("Mechanical Engineer", "paragraph", "p30"),
            _para("DURAFAME INC. | OCT 2013 - JUL 2016", "role_header", "p32"),
            _para("Created robust mechanical designs and translated these "
                  "into design specifications.", "paragraph", "p34"),
        ]
        roles = _group_roles(paras)
        assert len(roles) == 2
        assert [b.para_id for b in roles[0].bullets] == ["p28"]
        assert [x.para_id for x in roles[0].header_extra] == ["p30"]
        assert [b.para_id for b in roles[1].bullets] == ["p34"]

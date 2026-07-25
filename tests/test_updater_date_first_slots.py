"""Tests for date-first experience slot handling (Phase 1 injection fixes).

Covers three behaviors of the date-first update path:
1. _rebuild_roles_from_classification claims unclaimed span paragraphs as
   bullet slots when a classification role has empty body_blocks (samples 6/7:
   template description/placeholder paragraphs survived and all LLM bullets
   were silently dropped).
2. _update_experience_date_first anchors all LLM bullets as extra paragraphs
   when a matched role has no bullet slots at all.
3. Surplus template slots (more slots than LLM bullets) are blanked so stale
   template content does not render alongside injected bullets (sample 36),
   EXCEPT paras protected as another role's header/meta or company lines.
"""
from __future__ import annotations

import pytest

from tailor.compiler.classification_models import (
    ClassificationBlock,
    ClassificationRole,
    ClassificationSection,
)
from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection, RoleEntry
from tailor.compiler.text_parser import LlmRole, LlmSection
from tailor.compiler.updater import (
    _extract_company_tokens,
    _rebuild_roles_from_classification,
    _update_experience_date_first,
)


def _para(text: str, semantic: str, para_id: str) -> ParaModel:
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _block(para_id: str, semantic: str = "role_header") -> ClassificationBlock:
    return ClassificationBlock(
        block_id=f"blk_{para_id}",
        para_id=para_id,
        semantic_type=semantic,
        rewrite_policy="rewrite_text",
    )


def _date_first_section() -> ResumeSection:
    """Experience section shaped like sample 7: date, company, description ×3."""
    body = [
        _para("January 20xx - Current", "role_meta", "para_21"),
        _para("Office manager, The Phone Company", "role_header", "para_22"),
        _para("Summarize your key responsibilities.", "paragraph", "para_23"),
        _para("March 20xx - December 20xx", "role_meta", "para_25"),
        _para("Office manager, Nod Publishing", "role_header", "para_26"),
        _para("Summarize your key responsibilities again.", "paragraph", "para_27"),
    ]
    heading = _para("EXPERIENCE", "section_heading", "para_20")
    sec = ResumeSection(
        title="EXPERIENCE", heading=heading, semantic_type="experience",
        body_paras=body,
    )
    sec.section_id = "sec_2"
    return sec


def _cls_sec_empty_body_blocks() -> ClassificationSection:
    return ClassificationSection(
        section_id="sec_2",
        raw_title="EXPERIENCE",
        display_title="EXPERIENCE",
        semantic_type="experience",
        rewrite_policy="rewrite_bullets_only",
        roles=[
            ClassificationRole(
                role_id="role_1",
                header_blocks=[_block("para_22")],
                meta_blocks=[_block("para_21", "role_meta")],
                body_blocks=[],
            ),
            ClassificationRole(
                role_id="role_2",
                header_blocks=[_block("para_26")],
                meta_blocks=[_block("para_25", "role_meta")],
                body_blocks=[],
            ),
        ],
    )


class TestSpanClaimFallback:
    def test_empty_body_blocks_claim_span_paras(self):
        sec = _date_first_section()
        roles = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        assert len(roles) == 2
        assert [b.para_id for b in roles[0].bullets] == ["para_23"]
        assert [b.para_id for b in roles[1].bullets] == ["para_27"]

    def test_resolved_body_blocks_unchanged(self):
        sec = _date_first_section()
        cls_sec = _cls_sec_empty_body_blocks()
        cls_sec.roles[0].body_blocks = [_block("para_23", "bullet")]
        roles = _rebuild_roles_from_classification(sec, cls_sec)
        assert [b.para_id for b in roles[0].bullets] == ["para_23"]
        # role 2 still claims its own span; role 1's para is not re-claimed
        assert [b.para_id for b in roles[1].bullets] == ["para_27"]

    def test_span_does_not_cross_next_role(self):
        sec = _date_first_section()
        roles = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        claimed_role1 = {b.para_id for b in roles[0].bullets}
        # role 1's span must not swallow role 2's paras
        assert "para_25" not in claimed_role1
        assert "para_26" not in claimed_role1
        assert "para_27" not in claimed_role1

    def test_span_never_claims_pipe_header_segment_line(self):
        # Sample 10: role 3's company line ("Safewest Banking") sits right
        # after its header; the LLM role is pipe-format so the company lives
        # in a header SEGMENT, not meta_lines.  The span fallback must not
        # claim it as a bullet slot (it would be overwritten with bullet text).
        sec = _date_first_section()
        company3 = _para("Safewest Banking", "paragraph", "para_47")
        sec.body_paras = sec.body_paras + [
            _para("Aug 20XX - Jan 20XX", "role_meta", "para_45"),
            _para("Sales Associate", "role_header", "para_46"),
            company3,
        ]
        cls_sec = _cls_sec_empty_body_blocks()
        cls_sec.roles.append(ClassificationRole(
            role_id="role_3",
            header_blocks=[_block("para_46")],
            meta_blocks=[_block("para_45", "role_meta")],
            body_blocks=[],
        ))
        llm_roles = [
            LlmRole(header="Office manager | The Phone Company | Jan 20XX - Current",
                    bullets=["a"]),
            LlmRole(header="Office manager | Nod Publishing | Mar 20XX - Dec 20XX",
                    bullets=["b"]),
            LlmRole(header="Sales Associate | Safewest Banking | Aug 20XX - Jan 20XX",
                    bullets=["c"]),
        ]
        roles = _rebuild_roles_from_classification(sec, cls_sec, llm_roles)
        assert roles[2].bullets == []
        assert company3.text == "Safewest Banking"

    def test_span_stops_at_unclaimed_llm_header_line(self):
        # Sample 6: a third role's header para ("Office manager, Southridge
        # Video", semantic "paragraph") is unclaimed by classification and
        # sits inside role 2's span.  It must not become a bullet slot, and
        # paras after it belong to the next role, not the span.
        from tailor.compiler.text_parser import LlmRole

        sec = _date_first_section()
        header3 = _para("Office manager, Southridge Video ", "paragraph", "para_33")
        placeholder3 = _para("Summarize your key responsibilities third.",
                             "paragraph", "para_35")
        sec.body_paras = sec.body_paras + [header3, placeholder3]
        llm_roles = [
            LlmRole(header="Office manager, The Phone Company", bullets=["a"]),
            LlmRole(header="Office manager, Nod Publishing", bullets=["b"]),
            LlmRole(header="Office manager, Southridge Video", bullets=["c"]),
        ]
        roles = _rebuild_roles_from_classification(
            sec, _cls_sec_empty_body_blocks(), llm_roles
        )
        claimed_role2 = {b.para_id for b in roles[1].bullets}
        assert claimed_role2 == {"para_27"}
        assert header3.text == "Office manager, Southridge Video "


class TestCompanyTokenExtraction:
    def test_three_part_pipe_header_uses_company_segment(self):
        # "Title | Company | Date" — the company is the segment before the
        # date, not the first segment (sample 17 pairing swap).
        toks = _extract_company_tokens(
            "Lead Mechanical Engineer | VALENTI AND ASSOCIATES | AUG 2016 - PRESENT"
        )
        assert toks == {"valenti", "associates"}

    def test_two_part_company_date_header(self):
        toks = _extract_company_tokens("VALENTI AND ASSOCIATES | AUG 2016 - PRESENT")
        assert toks == {"valenti", "associates"}

    def test_title_dash_company_without_date_unchanged(self):
        toks = _extract_company_tokens("Senior Engineer — Acme Corp")
        assert toks == {"senior", "engineer"}


class TestClsTrustGuards:
    """Sample 24-class classification faults: the only cls role pairs the
    Project Manager header with the Project Engineer's bullets, while the
    manager's own slots are misfiled as meta_blocks/preserve."""

    def _section(self) -> ResumeSection:
        body = [
            _para("TIMMERMAN INDUSTRIES - Any City", "paragraph", "para_31"),
            _para("Project Manager (2023 - Present)", "role_meta", "para_33"),
            _para("Prepare project worksheets.", "paragraph", "para_35"),
            _para("Prepare administrative documents.", "paragraph", "para_38"),
            _para("Coordinate with other divisions.", "paragraph", "para_41"),
            _para("TIMMERMAN INDUSTRIES - Any City", "paragraph", "para_44"),
            _para("Project Engineer(2021 - 2023)", "role_meta", "para_46"),
            _para("Control the project's progress.", "paragraph", "para_48"),
            _para("Responsible for the engineering department.", "paragraph", "para_51"),
        ]
        heading = _para("WORK EXPERIENCE", "section_heading", "para_28")
        sec = ResumeSection(
            title="WORK EXPERIENCE", heading=heading,
            semantic_type="experience", body_paras=body,
        )
        sec.section_id = "sec_1"
        return sec

    def _cls_sec(self) -> ClassificationSection:
        return ClassificationSection(
            section_id="sec_1",
            raw_title="WORK EXPERIENCE",
            display_title="WORK EXPERIENCE",
            semantic_type="experience",
            rewrite_policy="rewrite_bullets_only",
            roles=[
                ClassificationRole(
                    role_id="role_1",
                    header_blocks=[_block("para_33")],
                    meta_blocks=[
                        _block("para_35", "role_meta"),
                        _block("para_38", "role_meta"),
                        _block("para_41", "role_meta"),
                        _block("para_44", "role_meta"),
                    ],
                    body_blocks=[
                        _block("para_48", "bullet"),
                        _block("para_51", "bullet"),
                    ],
                ),
            ],
        )

    def _llm_roles(self) -> list[LlmRole]:
        return [
            LlmRole(
                header="Project Manager",
                meta_lines=["TIMMERMAN INDUSTRIES - Any City", "2023 - Present"],
                bullets=["a", "b", "c"],
            ),
            LlmRole(
                header="Project Engineer",
                meta_lines=["TIMMERMAN INDUSTRIES - Any City", "2021 - 2023"],
                bullets=["d", "e"],
            ),
        ]

    def test_misassigned_body_blocks_dropped_and_meta_promoted(self):
        sec = self._section()
        roles = _rebuild_roles_from_classification(
            sec, self._cls_sec(), self._llm_roles()
        )
        # The next role's bullets (beyond the unclaimed "Project Engineer"
        # header line) are dropped from the manager role; the manager's
        # description-like meta paras are promoted to its bullet slots.
        assert [b.para_id for b in roles[0].bullets] == [
            "para_35", "para_38", "para_41",
        ]
        # The company line stays meta (no sentence-ending punctuation).
        assert [m.para_id for m in roles[0].meta_lines] == ["para_44"]
        # A second role is SYNTHESIZED at the unclaimed boundary and hands
        # the dropped paras to the Project Engineer's LLM role as slots.
        assert len(roles) == 2
        assert roles[1].header.para_id == "para_46"
        assert [b.para_id for b in roles[1].bullets] == ["para_48", "para_51"]

    def test_drop_requires_fallback_content(self):
        # Word-wrap fragment headers (sample 25): when there is NOTHING of the
        # role's own between header and boundary, the drop must not fire —
        # dropping would only orphan the LLM bullets.
        body = [
            _para("Mechanical Engineering", "role_header", "para_10"),
            _para("Mechanical Engineer at", "paragraph", "para_11"),
            _para("Warner & Spencer", "paragraph", "para_12"),
            _para("Researched new materials-generation", "paragraph", "para_13"),
        ]
        sec = ResumeSection(
            title="WORK HISTORY",
            heading=_para("WORK HISTORY", "section_heading", "para_9"),
            semantic_type="experience", body_paras=body,
        )
        sec.section_id = "sec_1"
        cls_sec = ClassificationSection(
            section_id="sec_1", raw_title="WORK HISTORY",
            display_title="WORK HISTORY", semantic_type="experience",
            rewrite_policy="rewrite_bullets_only",
            roles=[ClassificationRole(
                role_id="role_1",
                header_blocks=[_block("para_10")],
                meta_blocks=[],
                body_blocks=[
                    _block("para_12", "bullet"),
                    _block("para_13", "bullet"),
                ],
            )],
        )
        llm_roles = [
            LlmRole(header="Mechanical Engineering", bullets=["x"]),
            LlmRole(header="Mechanical Engineer at", bullets=["y"]),
        ]
        roles = _rebuild_roles_from_classification(sec, cls_sec, llm_roles)
        # para_11 matches an LLM "header" but there is no fallback content
        # between para_10 and para_11 — body blocks must be kept.
        assert [b.para_id for b in roles[0].bullets] == ["para_12", "para_13"]

    def test_meta_outside_territory_not_promoted(self):
        # Sample 6: another role's placeholder (BEFORE this role's header) is
        # misfiled into this role's meta — it must not become a bullet slot.
        body = [
            _para("Summarize your key responsibilities.", "paragraph", "para_25"),
            _para("Office manager, Southridge Video", "role_header", "para_33"),
            _para("Summarize your responsibilities third.", "paragraph", "para_35"),
        ]
        sec = ResumeSection(
            title="EXPERIENCE",
            heading=_para("EXPERIENCE", "section_heading", "para_20"),
            semantic_type="experience", body_paras=body,
        )
        sec.section_id = "sec_2"
        cls_sec = ClassificationSection(
            section_id="sec_2", raw_title="EXPERIENCE",
            display_title="EXPERIENCE", semantic_type="experience",
            rewrite_policy="rewrite_bullets_only",
            roles=[ClassificationRole(
                role_id="role_1",
                header_blocks=[_block("para_33")],
                meta_blocks=[_block("para_25", "role_meta")],
                body_blocks=[],
            )],
        )
        llm_roles = [LlmRole(header="Office manager, Southridge Video", bullets=["z"])]
        roles = _rebuild_roles_from_classification(sec, cls_sec, llm_roles)
        # para_25 (before the header) must NOT be promoted; the span fallback
        # claims para_35, the role's own placeholder, instead.
        assert [b.para_id for b in roles[0].bullets] == ["para_35"]
        assert [m.para_id for m in roles[0].meta_lines] == ["para_25"]


class TestDateFirstInjection:
    def _llm(self, bullets_per_role: list[list[str]]) -> LlmSection:
        return LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[
                LlmRole(
                    header=h,
                    bullets=b,
                )
                for h, b in zip(
                    ["Office Manager, The Phone Company", "Office Manager, Nod Publishing"],
                    bullets_per_role,
                )
            ],
        )

    def test_placeholder_slots_receive_llm_bullets(self):
        sec = _date_first_section()
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        llm = self._llm([["Managed operations.", "Coordinated tasks."], ["Supported projects."]])
        result = _update_experience_date_first(sec, llm, rebuilt)
        texts = [p.text for p in result.body_paras]
        assert "Managed operations." in texts
        assert "Supported projects." in texts
        assert not any("Summarize your key" in t for t in texts)
        # second bullet of role 1 exceeds the single slot → extra injection
        extras = getattr(result, "_extra_injections", {})
        assert [e.text for e in extras.get("para_23", [])] == ["Coordinated tasks."]

    def test_no_slots_at_all_anchors_extras_at_role_header(self):
        sec = _date_first_section()
        # remove the description paras entirely: roles have zero possible slots
        sec.body_paras = [p for p in sec.body_paras if p.semantic != "paragraph"]
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        assert all(not r.bullets for r in rebuilt)
        llm = self._llm([["Managed operations."], ["Supported projects."]])
        result = _update_experience_date_first(sec, llm, rebuilt)
        extras = getattr(result, "_extra_injections", {})
        # anchored at each role's last para in document order (the header)
        assert [e.text for e in extras.get("para_22", [])] == ["Managed operations."]
        assert [e.text for e in extras.get("para_26", [])] == ["Supported projects."]

    def test_surplus_slots_blanked(self):
        sec = _date_first_section()
        # give role 1 two extra stale template lines after its description
        stale1 = _para("Application level: Java 11, Spring Boot", "paragraph", "para_24a")
        stale2 = _para("DevOps tools: GitLab, Jenkins", "paragraph", "para_24b")
        sec.body_paras = sec.body_paras[:3] + [stale1, stale2] + sec.body_paras[3:]
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        assert len(rebuilt[0].bullets) == 3
        llm = self._llm([["Managed operations."], ["Supported projects."]])
        result = _update_experience_date_first(sec, llm, rebuilt)
        assert stale1.text == ""
        assert stale2.text == ""

    def test_surplus_company_line_never_blanked(self):
        sec = _date_first_section()
        # simulate sample 36: next role's company line misassigned as a slot
        company = _para("Nod Publishing - Krakow, Poland", "paragraph", "para_24c")
        sec.body_paras = sec.body_paras[:3] + [company] + sec.body_paras[3:]
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        llm = self._llm([["Managed operations."], ["Supported projects."]])
        _update_experience_date_first(sec, llm, rebuilt)
        assert company.text == "Nod Publishing - Krakow, Poland"

    def test_surplus_contact_line_never_blanked(self):
        sec = _date_first_section()
        contact = _para("+123-456-7890  hello@example.com", "paragraph", "para_24d")
        sec.body_paras = sec.body_paras[:3] + [contact] + sec.body_paras[3:]
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        llm = self._llm([["Managed operations."], ["Supported projects."]])
        _update_experience_date_first(sec, llm, rebuilt)
        assert contact.text == "+123-456-7890  hello@example.com"

    def test_llm_role_without_bullets_keeps_original(self):
        sec = _date_first_section()
        rebuilt = _rebuild_roles_from_classification(sec, _cls_sec_empty_body_blocks())
        llm = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[
                LlmRole(header="Office Manager, The Phone Company", bullets=[]),
                LlmRole(header="Office Manager, Nod Publishing", bullets=[]),
            ],
        )
        result = _update_experience_date_first(sec, llm, rebuilt)
        texts = [p.text for p in result.body_paras]
        # no LLM content → original preserved, nothing blanked
        assert "Summarize your key responsibilities." in texts
        assert "Summarize your key responsibilities again." in texts

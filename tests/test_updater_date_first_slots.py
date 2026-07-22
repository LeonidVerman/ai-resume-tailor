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

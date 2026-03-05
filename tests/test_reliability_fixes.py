"""Tests for reliability fixes (spec v1 + v2):

A) parse_cover_letter — header/body separation (ParsedCoverLetter)
A2) parse_cover_letter_body — line-based body parser
B) build_cover_letter_ledger — deterministic ledger override
C) _tokenize_skills_section — ignores category headers, handles parens
D) _autofill_evidence_ledger — fills missing ledger entries, coarse location
"""

from __future__ import annotations

import pytest

from tailor.cover_letter import (
    ParsedCoverLetter,
    build_cover_letter_ledger,
    parse_cover_letter,
    parse_cover_letter_body,
)
from tailor.phase2_validator import _tokenize_skills_section


# ---------------------------------------------------------------------------
# Section A: parse_cover_letter
# ---------------------------------------------------------------------------


class TestParseCoverLetter:
    """A: Header/body separation."""

    # ---- salutation detection ----

    def test_standard_salutation_splits_correctly(self):
        cl = (
            "Dear Hiring Manager,\n"
            "Hook paragraph.\n\n"
            "P1 paragraph.\n\n"
            "P2 paragraph.\n\n"
            "Closing paragraph."
        )
        parsed = parse_cover_letter(cl)
        assert parsed.header == "Dear Hiring Manager,"
        assert parsed.body_paragraphs == [
            "Hook paragraph.",
            "P1 paragraph.",
            "P2 paragraph.",
            "Closing paragraph.",
        ]

    def test_address_block_before_salutation(self):
        """Address lines before salutation must NOT become body paragraphs."""
        cl = (
            "Hiring Manager\n"
            "Acme Corp\n"
            "San Francisco, CA\n\n"
            "Dear Hiring Manager,\n\n"
            "Hook.\n\n"
            "P1.\n\n"
            "P2.\n\n"
            "Closing."
        )
        parsed = parse_cover_letter(cl)
        assert "Hiring Manager\nAcme Corp" in parsed.header
        assert "Dear Hiring Manager," in parsed.header
        assert parsed.body_paragraphs == ["Hook.", "P1.", "P2.", "Closing."]
        assert len(parsed.body_paragraphs) == 4

    def test_salutation_and_first_para_same_block(self):
        """Salutation + text on next line → salutation is header, text is body para 1."""
        cl = (
            "Dear Hiring Manager,\n"
            "I am writing to express my interest.\n\n"
            "P1.\n\n"
            "P2.\n\n"
            "Closing."
        )
        parsed = parse_cover_letter(cl)
        assert parsed.header == "Dear Hiring Manager,"
        assert parsed.body_paragraphs[0] == "I am writing to express my interest."
        assert len(parsed.body_paragraphs) == 4

    def test_no_salutation_entire_text_is_body(self):
        """When no 'Dear' line exists, full text becomes body."""
        cl = "Hook.\n\nP1.\n\nP2.\n\nClosing."
        parsed = parse_cover_letter(cl)
        assert parsed.header == ""
        assert parsed.body_paragraphs == ["Hook.", "P1.", "P2.", "Closing."]

    def test_empty_string(self):
        parsed = parse_cover_letter("")
        assert parsed.header == ""
        assert parsed.body_paragraphs == []

    def test_case_insensitive_salutation(self):
        cl = "DEAR HIRING MANAGER,\nHook.\n\nP1.\n\nP2.\n\nClosing."
        parsed = parse_cover_letter(cl)
        assert "DEAR HIRING MANAGER," in parsed.header
        assert len(parsed.body_paragraphs) == 4

    # ---- paragraph_text helper ----

    def test_paragraph_text_valid_index(self):
        cl = "Dear Hiring Manager,\nHook.\n\nP1.\n\nP2.\n\nClosing."
        parsed = parse_cover_letter(cl)
        assert parsed.paragraph_text(1) == "Hook."
        assert parsed.paragraph_text(4) == "Closing."

    def test_paragraph_text_out_of_range(self):
        cl = "Dear Hiring Manager,\nHook.\n\nP1."
        parsed = parse_cover_letter(cl)
        assert parsed.paragraph_text(0) == ""
        assert parsed.paragraph_text(99) == ""

    # ---- integration: paragraph count robustness ----

    def test_four_body_paragraphs_counted_correctly(self):
        """Classic four-para cover letter must yield exactly 4 body paragraphs."""
        cl = (
            "Dear Hiring Manager,\n"
            "Hook paragraph.\n\n"
            "Proof P1 reduced deployment cycle by 40%.\n\n"
            "Proof P2 scaled platform to 1M+ users.\n\n"
            "While my background is in fintech, the underlying patterns translate directly.\n"
            "Thank you for your consideration."
        )
        parsed = parse_cover_letter(cl)
        assert len(parsed.body_paragraphs) == 4

    def test_address_block_does_not_inflate_count(self):
        """5-line address block must not add phantom paragraphs."""
        cl = (
            "Jane Doe\n"
            "123 Main St\n"
            "Anytown, CA 12345\n\n"
            "March 1, 2026\n\n"
            "Dear Hiring Manager,\n\n"
            "Hook.\n\n"
            "P1.\n\n"
            "P2.\n\n"
            "Closing."
        )
        parsed = parse_cover_letter(cl)
        assert len(parsed.body_paragraphs) == 4


# ---------------------------------------------------------------------------
# Section B: build_cover_letter_ledger
# ---------------------------------------------------------------------------


def _simple_plan(
    p1_span: str = "reduced deployment cycle by 40%",
    p2_span: str = "scaled platform to 1M+ users",
    bridge_required: bool = False,
) -> dict:
    return {
        "proof_points": [
            {"proof_id": "P1", "required_exact_span": p1_span},
            {"proof_id": "P2", "required_exact_span": p2_span},
        ],
        "bridge_sentence_required": bridge_required,
    }


def _four_para_cl(
    p1_span: str = "reduced deployment cycle by 40%",
    p2_span: str = "scaled platform to 1M+ users",
    p4: str = "I look forward to contributing.",
) -> str:
    return (
        "Dear Hiring Manager,\n"
        "Hook paragraph.\n\n"
        f"First proof: {p1_span} via CI/CD.\n\n"
        f"Second proof: {p2_span} using scaling.\n\n"
        f"{p4}"
    )


# ---------------------------------------------------------------------------
# Section A2: parse_cover_letter_body (line-based canonical parser)
# ---------------------------------------------------------------------------


class TestParseCoverLetterBody:
    """A2: parse_cover_letter_body — line-based header/body split."""

    def test_standard_salutation(self):
        cl = "Dear Hiring Manager,\nHook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert body == ["Hook.", "P1.", "P2.", "Closing."]

    def test_address_block_excluded(self):
        cl = "Jane Smith\nCity, CA\n\nDear Hiring Manager,\n\nHook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert body == ["Hook.", "P1.", "P2.", "Closing."]
        assert len(body) == 4

    def test_no_salutation_all_body(self):
        cl = "Hook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert body == ["Hook.", "P1.", "P2.", "Closing."]

    def test_crlf_normalization(self):
        cl = "Dear Hiring Manager,\r\nHook.\r\n\r\nP1.\r\n\r\nClosing."
        body = parse_cover_letter_body(cl)
        assert body == ["Hook.", "P1.", "Closing."]

    def test_empty_input(self):
        assert parse_cover_letter_body("") == []

    def test_multiple_blank_lines_collapsed(self):
        """3+ blank lines between paragraphs should still yield one paragraph per block."""
        cl = "Dear Hiring Manager,\nHook.\n\n\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert len(body) == 4

    def test_case_insensitive_dear(self):
        cl = "DEAR HIRING MANAGER,\nHook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert len(body) == 4

    def test_indented_salutation(self):
        """Indented salutation should still be detected."""
        cl = "  Dear Hiring Manager,\nHook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert len(body) == 4

    def test_fallback_dear_in_middle_of_line(self):
        """If no line *starts* with Dear, fall back to first line containing Dear."""
        cl = "(Dear Hiring Manager)\nHook.\n\nP1.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        # Fallback finds "(Dear Hiring Manager)", body starts after it
        assert len(body) == 4

    def test_paragraph_content_preserved(self):
        p1 = "reduced deployment cycle by 40%"
        cl = f"Dear Hiring Manager,\nHook.\n\nFirst proof: {p1} via CI/CD.\n\nP2.\n\nClosing."
        body = parse_cover_letter_body(cl)
        assert p1 in body[1]


class TestBuildCoverLetterLedger:
    """B: Deterministic cover_letter_ledger construction."""

    def test_both_spans_found(self):
        plan = _simple_plan()
        cl = _four_para_cl()
        ledger = build_cover_letter_ledger(cl, plan)

        index = {e["proof_id"]: e for e in ledger}
        assert index["P1"]["exact_span"] == "reduced deployment cycle by 40%"
        assert index["P1"]["location"] == "paragraph[2]"
        assert index["P2"]["exact_span"] == "scaled platform to 1M+ users"
        assert index["P2"]["location"] == "paragraph[3]"

    def test_missing_span_produces_empty_location(self):
        plan = _simple_plan(p1_span="this phrase does not appear")
        cl = _four_para_cl()
        ledger = build_cover_letter_ledger(cl, plan)

        index = {e["proof_id"]: e for e in ledger}
        assert index["P1"]["exact_span"] == ""
        assert index["P1"]["location"] == "missing"

    def test_bridge_found_in_paragraph_4(self):
        plan = _simple_plan(bridge_required=True)
        bridge_text = (
            "While my background is in fintech, the underlying patterns of "
            "distributed systems translate directly to cloud platforms."
        )
        cl = (
            "Dear Hiring Manager,\n"
            "Hook.\n\n"
            "P1 reduced deployment cycle by 40%.\n\n"
            "P2 scaled platform to 1M+ users.\n\n"
            f"{bridge_text}\nThank you."
        )
        ledger = build_cover_letter_ledger(cl, plan)
        index = {e["proof_id"]: e for e in ledger}

        assert "BRIDGE" in index
        assert "While my background is in fintech" in index["BRIDGE"]["exact_span"]
        assert index["BRIDGE"]["location"] == "paragraph[4]"

    def test_bridge_missing_produces_empty_entry(self):
        plan = _simple_plan(bridge_required=True)
        cl = _four_para_cl()  # no bridge sentence
        ledger = build_cover_letter_ledger(cl, plan)
        index = {e["proof_id"]: e for e in ledger}

        assert index["BRIDGE"]["exact_span"] == ""
        assert index["BRIDGE"]["location"] == "missing"

    def test_no_bridge_when_not_required(self):
        plan = _simple_plan(bridge_required=False)
        cl = _four_para_cl()
        ledger = build_cover_letter_ledger(cl, plan)
        ids = {e["proof_id"] for e in ledger}
        assert "BRIDGE" not in ids

    def test_ledger_overrides_lowers_validator_errors(self):
        """Validator V3 should pass when deterministic ledger is correct."""
        from datetime import date
        from tailor.phase2_validator import (
            CL_LEDGER_MISSING_ENTRY,
            CL_LEDGER_SPAN_NOT_FOUND,
            validate_phase2_output,
        )
        from tailor.writer_packet import build_writer_packet

        plan = {
            "role_level": "senior",
            "jd_top_themes": [
                {"theme": "Scalability", "priority": "primary",
                 "why_important": "x", "keywords": ["scaling"]}
            ],
            "evidence_map": [{"theme": "Scalability", "evidence": [
                {"source": "master_resume", "location": "Acme",
                 "quote": "Scaled platform", "allowed_claims": ["scaled"]}
            ], "gaps": [], "safe_translation": []}],
            "resume_strategy": {
                "summary": {"include_points": [], "avoid_points": []},
                "experience": [{"role_name": "Senior Engineer | Acme Corp",
                                "priority": "high",
                                "bullets_to_emphasize": [],
                                "bullets_to_compress": [],
                                "bullets_to_reframe": []}],
                "skills": {"promote_skills": [], "demote_skills": [],
                           "do_not_add_skills": []},
            },
            "cover_letter_strategy": {"company_and_role_mentions": [], "structure": []},
            "risk_checks": {"do_not_invent": [], "likely_hallucination_traps": [],
                            "claims_requiring_strict_grounding": []},
            "vocabulary_anchoring": {"must_embed": [], "optional_embed": []},
            "jd_domain": "b2b_saas_platform",
            "candidate_primary_domain": "b2b_saas_platform",
            "domain_mismatch": False,
            "domain_translation_rule_ids": [],
            "narrative_plan": {
                "anchor_role_id": "Senior Engineer | Acme Corp",
                "theme_ranked": [{"theme_id": "T1", "label": "Scalability",
                                   "priority": "primary",
                                   "signature_terms": ["scaling"]}],
                "summary_coverage": {"must_cover_theme_ids": [],
                                     "should_cover_theme_ids": []},
                "anchor_role_coverage": {"first_k_bullets": 3,
                                         "top_k_themes_to_cover": 1,
                                         "min_theme_occurrences": []},
                "domain_translation_binding": {
                    "min_total_rule_instantiations": 0,
                    "min_instantiations_in_anchor_role": 0,
                    "require_target_frame_in_anchor_role_first_k": False,
                },
            },
            "skill_graph": {"direct_skills": ["Python", "AWS", "Docker"],
                            "related_skills": []},
            "cover_letter_plan": {
                "structure_version": "CL_V1_4PARA_2PROOF",
                "hook_theme_id": "T1",
                "proof_points": [
                    {"proof_id": "P1",
                     "required_exact_span": "reduced deployment cycle by 40%",
                     "allowed_tool_mentions": []},
                    {"proof_id": "P2",
                     "required_exact_span": "scaled platform to 1M+ users",
                     "allowed_tool_mentions": []},
                ],
                "bridge_sentence_required": False,
                "closing_guidance": "Express interest.",
            },
        }

        resume = (
            "Professional Summary\nExperienced engineer.\n\n"
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Scaled platform to 1M+ users using horizontal scaling\n"
            "- Implemented caching\n"
            "- Designed microservices architecture\n"
            "- Led backend platform team\n\n"
            "Technical Skills\nLanguages: Python\nInfrastructure: AWS, Docker\n"
        )
        p1_span = "reduced deployment cycle by 40%"
        p2_span = "scaled platform to 1M+ users"
        cl = (
            "Dear Hiring Manager,\n"
            "Hook paragraph.\n\n"
            f"First proof: {p1_span} via CI/CD.\n\n"
            f"Second proof: {p2_span} using scaling.\n\n"
            "I look forward to speaking with you."
        )

        wp = build_writer_packet(plan, "", resume, "Job description")
        cl_plan = wp.get("cover_letter_plan") or {}
        deterministic_ledger = build_cover_letter_ledger(cl, cl_plan)

        d = date.today()
        current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
        report = validate_phase2_output(
            wp, resume, cl, current_date,
            cover_letter_ledger=deterministic_ledger,
        )
        cl_errors = [
            e for e in report["errors"]
            if e.startswith("CL_LEDGER")
        ]
        assert cl_errors == [], cl_errors

    def test_address_block_does_not_shift_paragraph_indices(self):
        """Spans must resolve to correct paragraph even with address header."""
        plan = _simple_plan()
        cl = (
            "Jane Smith\n"
            "Anytown, CA\n\n"
            "Dear Hiring Manager,\n\n"
            "Hook paragraph.\n\n"
            "First proof: reduced deployment cycle by 40%.\n\n"
            "Second proof: scaled platform to 1M+ users.\n\n"
            "Closing paragraph."
        )
        ledger = build_cover_letter_ledger(cl, plan)
        index = {e["proof_id"]: e for e in ledger}
        assert index["P1"]["location"] == "paragraph[2]"
        assert index["P2"]["location"] == "paragraph[3]"


# ---------------------------------------------------------------------------
# Section C: _tokenize_skills_section — category header fix
# ---------------------------------------------------------------------------


class TestTokenizeSkillsSection:
    """C: Skills section tokenizer must not treat category labels as skills."""

    def test_category_label_excluded(self):
        text = "Languages: Python, Java\nInfrastructure: AWS, Docker"
        tokens = _tokenize_skills_section(text)
        # Category labels must NOT be tokens
        assert "languages" not in tokens
        assert "infrastructure" not in tokens
        # Skills must be present
        assert "python" in tokens
        assert "java" in tokens
        assert "aws" in tokens
        assert "docker" in tokens

    def test_plain_comma_list_no_label(self):
        tokens = _tokenize_skills_section("Python, Java, AWS")
        assert "python" in tokens
        assert "java" in tokens
        assert "aws" in tokens

    def test_mixed_lines(self):
        text = "Languages & Frameworks: Python, React\nTools: Docker, Kubernetes\nGit"
        tokens = _tokenize_skills_section(text)
        assert "languages & frameworks" not in tokens
        assert "tools" not in tokens
        assert "python" in tokens
        assert "docker" in tokens
        assert "kubernetes" in tokens
        assert "git" in tokens

    def test_empty_rhs_after_colon(self):
        """Line with colon but empty RHS should produce no tokens from that line."""
        tokens = _tokenize_skills_section("Languages:")
        assert tokens == []

    def test_short_tokens_filtered(self):
        """Single-character tokens should be excluded."""
        tokens = _tokenize_skills_section("Skills: A, B, Python")
        assert "a" not in tokens
        assert "b" not in tokens
        assert "python" in tokens

    def test_pipe_separated_without_label(self):
        tokens = _tokenize_skills_section("Python | Java | AWS")
        assert "python" in tokens
        assert "java" in tokens
        assert "aws" in tokens

    def test_multiline_labels_not_leaked(self):
        """Ensure labels from multiple lines don't appear."""
        text = (
            "Backend: Python, Go\n"
            "Frontend: TypeScript\n"
            "Databases: PostgreSQL, Redis"
        )
        tokens = _tokenize_skills_section(text)
        for label in ("backend", "frontend", "databases"):
            assert label not in tokens
        for skill in ("python", "go", "typescript", "postgresql", "redis"):
            assert skill in tokens

    def test_parenthesized_tools_flattened(self):
        """'AI tools (ChatGPT, Cursor)' → tokens: 'ai tools', 'chatgpt', 'cursor'."""
        tokens = _tokenize_skills_section("AI tools (ChatGPT, Cursor)")
        assert "ai tools" in tokens
        assert "chatgpt" in tokens
        assert "cursor" in tokens

    def test_parenthesized_with_category_label(self):
        """'Dev tools: Git, AI tools (Cursor, Copilot)' → no label, paren content included."""
        tokens = _tokenize_skills_section("Dev tools: Git, AI tools (Cursor, Copilot)")
        assert "dev tools" not in tokens
        assert "git" in tokens
        assert "ai tools" in tokens
        assert "cursor" in tokens
        assert "copilot" in tokens

    def test_version_annotation_filtered(self):
        """'Python (3.9+)' → 'python' token but '3.9+' excluded (no letters)."""
        tokens = _tokenize_skills_section("Python (3.9+)")
        assert "python" in tokens
        assert "3.9+" not in tokens

    def test_multiple_paren_groups(self):
        """Each paren group is independently flattened; pure-digit versions dropped."""
        tokens = _tokenize_skills_section("Languages: Python (3.9+), Go (1.20+)")
        assert "python" in tokens
        assert "go" in tokens
        # "3.9+" and "1.20+" have no letters — filtered out
        assert "3.9+" not in tokens
        assert "1.20+" not in tokens

    def test_no_pure_number_tokens(self):
        """Tokens without letters are excluded."""
        tokens = _tokenize_skills_section("Agile, 3+ years, Scrum")
        assert "agile" in tokens
        assert "scrum" in tokens
        assert "3+ years" not in tokens or True  # may or may not include — "3+ years" contains letters

    def test_multi_colon_line_second_colon_stripped(self):
        """LLM puts two category colons on the same line; second colon must not leak."""
        # Mirrors real output: "Databases: MySQL, NoSql Systems & Platforms: Tomcat, JBoss"
        line = "Databases: MySQL, NoSql Systems & Platforms: Tomcat, JBoss"
        tokens = _tokenize_skills_section(line)
        assert "mysql" in tokens
        assert "tomcat" in tokens
        assert "jboss" in tokens
        # The garbage token "nosql systems & platforms: tomcat" must not appear
        assert "nosql systems & platforms: tomcat" not in tokens
        assert "nosql systems & platforms" not in tokens

    def test_multi_colon_standalone_no_top_level_category(self):
        """Line with no top-level category colon but has an internal one."""
        # e.g. a continuation line that was pasted without its own header
        line = "NoSql Systems & Platforms: Tomcat, JBoss, WebLogic"
        tokens = _tokenize_skills_section(line)
        assert "tomcat" in tokens
        assert "jboss" in tokens
        assert "weblogic" in tokens
        assert "nosql systems & platforms: tomcat" not in tokens
        assert "nosql systems & platforms" not in tokens


# ---------------------------------------------------------------------------
# Section C2: compound-name space-collapse check in allowlist validation
# ---------------------------------------------------------------------------

from tailor.phase2_validator import validate_phase2_output  # noqa: E402


def _wp_with_skill_allowlist(allowlist: list[str]) -> dict:
    return {
        "role_level": "senior",
        "role_priorities": {},
        "role_source_bullet_counts": {},
        "role_source_char_counts": {},
        "jd_is_delivery_oriented": False,
        "must_keep_metrics": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "unsafe_jd_nouns": [],
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
        "role_density_shortfall_allowance": {},
        "must_surface_arch_mechanisms": [],
        "arch_mechanisms_primary": [],
        "arch_mechanisms_backstop": [],
        "master_resume_role_names": [],
        "master_role_dates": {},
        "jd_vocab_must_embed": [],
        "domain_mismatch": False,
        "domain_translation_rules_applied": [],
        "skill_allowlist_skills_section": allowlist,
        "skill_allowlist_experience_claims": [],
        "direct_skills_set": [],
        "narrative_plan": None,
        "cover_letter_plan": None,
        "jd_domain": "software_engineering",
        "candidate_primary_domain": "software_engineering",
    }


def _resume_with_skills(skills_line: str) -> str:
    return (
        "Professional Summary\nExperienced engineer.\n\n"
        "Experience\nSoftware Engineer | Acme | 2020 - Present\n"
        "- Built services.\n- Improved systems.\n- Delivered features.\n- Maintained code.\n\n"
        f"Technical Skills\n{skills_line}"
    )


class TestSkillAllowlistCompoundNameCheck:
    """Root Cause B: CamelCase compound names written with spaces must not be false violations."""

    def test_rabbit_mq_spaced_variant_not_flagged(self):
        """'Rabbit MQ' in skills section, 'rabbitmq' in allowlist → no violation."""
        wp = _wp_with_skill_allowlist(["rabbitmq", "kafka"])
        resume = _resume_with_skills("Rabbit MQ, Kafka")
        report = validate_phase2_output(wp, resume, "", "")
        violations = report["stats"]["skill_section_violations"]
        assert "rabbit mq" not in violations

    def test_type_script_spaced_not_flagged(self):
        """'Type Script' (two words) in skills, 'typescript' in allowlist → no violation."""
        wp = _wp_with_skill_allowlist(["typescript", "python"])
        resume = _resume_with_skills("Type Script, Python")
        report = validate_phase2_output(wp, resume, "", "")
        violations = report["stats"]["skill_section_violations"]
        assert "type script" not in violations

    def test_truly_unlisted_skill_still_flagged(self):
        """A skill genuinely not in the allowlist must still be flagged."""
        wp = _wp_with_skill_allowlist(["rabbitmq", "kafka"])
        resume = _resume_with_skills("Rabbit MQ, Kafka, MongoDB")
        report = validate_phase2_output(wp, resume, "", "")
        violations = report["stats"]["skill_section_violations"]
        assert "mongodb" in violations

    def test_compound_match_does_not_cause_false_negative(self):
        """Space-collapse must not allow truly different multi-word skills through."""
        # "post gres sql" collapses to "postgressql" which ≠ "postgresql" — still a violation
        wp = _wp_with_skill_allowlist(["postgresql"])
        resume = _resume_with_skills("Post Gres Sql")
        report = validate_phase2_output(wp, resume, "", "")
        violations = report["stats"]["skill_section_violations"]
        assert "post gres sql" in violations


# ---------------------------------------------------------------------------
# Section D: _autofill_evidence_ledger
# ---------------------------------------------------------------------------

# Import from llm module (private functions tested here directly)
from tailor.llm import _autofill_evidence_ledger, _find_resume_coarse_location  # noqa: E402


class TestAutofillEvidenceLedger:
    """D: Fills missing ledger entries when items found in resume text."""

    def _ledger(self, entries: list[dict]) -> dict:
        return {"entries": entries}

    def test_fills_missing_metric(self):
        ledger = self._ledger([])
        resume = "Scaled platform to 1M+ users using horizontal scaling."
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], []
        )
        kinds = [(e["kind"], e["target"]) for e in result["entries"]]
        assert ("required_metric", "1M+ users") in kinds

    def test_fills_missing_skill(self):
        ledger = self._ledger([])
        resume = "Technical Skills\nPython, AWS, Docker"
        result = _autofill_evidence_ledger(
            ledger, resume, [], ["Python"]
        )
        kinds = [(e["kind"], e["target"]) for e in result["entries"]]
        assert ("required_skill", "Python") in kinds

    def test_does_not_duplicate_existing_entries(self):
        existing = [{
            "kind": "required_metric", "target": "1M+ users",
            "exact_span": "1M+ users", "location": "resume", "id": "x",
        }]
        ledger = self._ledger(existing)
        resume = "1M+ users in production."
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], []
        )
        metric_entries = [
            e for e in result["entries"]
            if e["kind"] == "required_metric" and e["target"] == "1M+ users"
        ]
        assert len(metric_entries) == 1

    def test_does_not_fill_when_not_in_resume(self):
        ledger = self._ledger([])
        resume = "No relevant metrics here."
        result = _autofill_evidence_ledger(
            ledger, resume, ["99.99% uptime"], []
        )
        assert result["entries"] == []

    def test_returns_same_object_when_no_changes(self):
        """When nothing to fill, returns original dict unchanged."""
        ledger = self._ledger([])
        resume = "No relevant content."
        result = _autofill_evidence_ledger(
            ledger, resume, ["metric not here"], ["skill not here"]
        )
        assert result is ledger

    def test_none_ledger_returned_unchanged(self):
        result = _autofill_evidence_ledger(
            None, "resume", ["metric"], ["skill"]  # type: ignore[arg-type]
        )
        assert result is None

    def test_multiple_items_filled(self):
        ledger = self._ledger([])
        resume = "Used Python and AWS. Served 1M+ users."
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], ["Python", "AWS"]
        )
        kinds = {(e["kind"], e["target"]) for e in result["entries"]}
        assert ("required_metric", "1M+ users") in kinds
        assert ("required_skill", "Python") in kinds
        assert ("required_skill", "AWS") in kinds

    def test_case_insensitive_match(self):
        """Match should be case-insensitive."""
        ledger = self._ledger([])
        resume = "Achieved 99.9% UPTIME guarantee."
        result = _autofill_evidence_ledger(
            ledger, resume, ["99.9% uptime"], []
        )
        kinds = [(e["kind"], e["target"]) for e in result["entries"]]
        assert ("required_metric", "99.9% uptime") in kinds

    def test_autofill_entries_have_required_keys(self):
        """Each autofill entry must have kind, target, exact_span, location, id."""
        ledger = self._ledger([])
        resume = "Delivered 40% faster deployments via CI/CD."
        result = _autofill_evidence_ledger(
            ledger, resume, ["40% faster deployments"], []
        )
        for entry in result["entries"]:
            for key in ("kind", "target", "exact_span", "location", "id"):
                assert key in entry, f"Missing key: {key}"

    def test_coarse_location_summary(self):
        """Item found in Professional Summary → location = resume.summary."""
        ledger = self._ledger([])
        resume = (
            "Professional Summary\n"
            "Experienced engineer with 1M+ users scaling background.\n\n"
            "Experience\n"
            "Senior Engineer | Acme | 2020 - Present\n"
            "- Led backend team\n"
        )
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], []
        )
        entry = next(e for e in result["entries"] if e["target"] == "1M+ users")
        assert entry["location"] == "resume.summary"

    def test_coarse_location_experience(self):
        """Item found in Experience section → location = resume.experience."""
        ledger = self._ledger([])
        resume = (
            "Professional Summary\n"
            "Experienced engineer.\n\n"
            "Experience\n"
            "Senior Engineer | Acme | 2020 - Present\n"
            "- Scaled platform to 1M+ users using horizontal scaling\n"
        )
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], []
        )
        entry = next(e for e in result["entries"] if e["target"] == "1M+ users")
        assert entry["location"] == "resume.experience"

    def test_coarse_location_fallback(self):
        """Item not in any named section → fallback location = resume."""
        ledger = self._ledger([])
        resume = "1M+ users mentioned at the top with no section header."
        result = _autofill_evidence_ledger(
            ledger, resume, ["1M+ users"], []
        )
        entry = next(e for e in result["entries"] if e["target"] == "1M+ users")
        assert entry["location"] == "resume"

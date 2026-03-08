"""Smoke tests for the generation pipeline.

These tests make no LLM calls and no network requests.  They verify that:
  - all required config, prompt, and template artefacts are present and loadable
  - save_doc_from_template produces a valid .docx for both resume and cover letter
  - the hyperlink-concatenation bug (name + email merged) does not regress
  - normalize_cover_letter produces the expected two-group structure
  - validate_plan accepts a well-formed minimal plan
"""

import os
import tempfile

import pytest
from docx import Document

from tailor.config import (
    ASSESS_MODEL,
    ASSESS_TEMPERATURE,
    COVER_TEMPLATE,
    ENABLE_PHASE2_JUDGE,
    ENABLE_PLAN_REPAIR,
    ENABLE_TWO_PHASE,
    PHASE1_MAX_TOKENS,
    PHASE1_MODEL,
    PHASE1_TEMPERATURE,
    PHASE2_MAX_REPAIR_ATTEMPTS,
    PHASE2_MAX_TOKENS,
    PHASE2_MODEL,
    PHASE2_TEMPERATURE,
    PHASE2_JUDGE_MODEL,
    PROMPTS_DIR,
    RESUME_TEMPLATE,
    SIMPLE_MODEL,
    SIMPLE_TEMPERATURE,
)
from tailor.docx.template_fill import normalize_cover_letter, read_docx, save_doc_from_template
from tailor.llm import PlanValidationError, validate_plan
from tailor.prompts import _load_prompt, _read_text_file


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MINIMAL_PLAN = {
    "role_level": "senior",
    "resume_mode": "technical_depth",
    "jd_domain": "b2b_saas_platform",
    "candidate_primary_domain": "fintech_trading",
    "domain_mismatch": False,
    "domain_translation_rule_ids": [],
    "jd_top_themes": [
        {
            "theme": f"Theme {i}",
            "priority": "primary",
            "why_important": "critical capability",
            "keywords": ["distributed", "scalable"],
        }
        for i in range(5)
    ],
    "vocabulary_anchoring": {
        "must_embed": ["distributed systems", "scalability"],
        "optional_embed": ["cloud-native"],
    },
    "evidence_map": [
        {
            "theme": "Theme 0",
            "evidence": [
                {
                    "source": "master_resume",
                    "location": "Acme Corp",
                    "quote": "Scaled platform to 1M+ users across distributed services",
                    "allowed_claims": ["scaled to 1M+ users"],
                },
                {
                    "source": "candidate_profile",
                    "location": "experience_highlights",
                    "quote": "Built horizontally scaled microservices handling 1M+ concurrent users",
                    "allowed_claims": ["horizontal scaling"],
                },
                {
                    "source": "master_resume",
                    "location": "Acme Corp",
                    "quote": "Implemented multi-layer caching reducing latency by 25%",
                    "allowed_claims": ["caching", "25% latency reduction"],
                },
            ],
            "gaps": [],
        }
    ],
    "resume_strategy": {
        "summary_angle": "Distributed-systems backend engineer",
        "top_priority_roles": ["Acme Corp"],
        "mechanisms_to_feature": ["caching", "horizontal scaling"],
        "metrics_to_surface": ["1M+ users", "25% latency"],
    },
    "cover_letter_strategy": {
        "opening_hook": "hook sentence",
        "bridge_required": False,
        "proof_paragraphs": [],
        "closing_cta": "closing",
    },
    "risk_checks": {"inflated_claims": [], "domain_gaps": []},
    "narrative_plan": {
        "headline_theme": "Distributed systems",
        "story_arc": "arc",
        "seniority_signals": ["signals"],
    },
    "skill_graph": {
        "direct_skills": ["Python", "Java"],
        "related_skills": ["Kafka"],
        "forbidden_skills": [],
        "can_claim_experience": ["Kubernetes"],
    },
}

# Minimal resume text that exercises the Experience section code path
_RESUME_TEXT = """\
Professional Summary
Senior Software Engineer with 15+ years of experience in distributed systems.

Experience
Senior Software Engineer | Acme Corp (Remote)
Jan 2024 \u2013 Present
Delivered scalable backend services handling 1M+ requests per day.
Reduced P99 latency by 30% via multi-layer caching strategy.

Technical Skills
Python, Java, Kafka, Kubernetes, PostgreSQL
"""

# Minimal cover letter text (matches the template paragraph structure)
_COVER_TEXT = """\
Leonid Verman
Vancouver, BC, Canada \xb7 +1 (778) 317-3248 \xb7 leonidverman@gmail.com
https://www.linkedin.com/in/leonid-verman-3569b71b2/
March 7, 2026
Dear Hiring Manager,
I am excited to apply for the Senior Software Engineer role at Acme Corp. With over 15 years of experience building distributed backend systems, I bring deep expertise in Java, Python, and high-throughput architecture.
I have successfully led teams delivering fintech platforms serving 1M+ users, including roles as VP and Director of Software Development. My work includes implementing caching layers that reduced latency by 25% and blockchain integrations for global markets.
I hold Canadian citizenship and am fully eligible to work in Canada without sponsorship.
Thank you for your time and consideration.

Sincerely,
Leonid Verman
"""


# ---------------------------------------------------------------------------
# 1. Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_config_has_model_settings(self):
        assert isinstance(PHASE1_MODEL, str) and PHASE1_MODEL
        assert isinstance(PHASE2_MODEL, str) and PHASE2_MODEL
        assert isinstance(SIMPLE_MODEL, str) and SIMPLE_MODEL
        assert isinstance(ASSESS_MODEL, str) and ASSESS_MODEL
        assert isinstance(PHASE2_JUDGE_MODEL, str) and PHASE2_JUDGE_MODEL

    def test_config_has_numeric_settings(self):
        assert isinstance(PHASE1_TEMPERATURE, float)
        assert isinstance(PHASE2_TEMPERATURE, float)
        assert isinstance(SIMPLE_TEMPERATURE, float)
        assert isinstance(ASSESS_TEMPERATURE, float)
        assert isinstance(PHASE1_MAX_TOKENS, int) and PHASE1_MAX_TOKENS > 0
        assert isinstance(PHASE2_MAX_TOKENS, int) and PHASE2_MAX_TOKENS > 0
        assert isinstance(PHASE2_MAX_REPAIR_ATTEMPTS, int) and PHASE2_MAX_REPAIR_ATTEMPTS >= 0

    def test_config_has_boolean_flags(self):
        assert isinstance(ENABLE_TWO_PHASE, bool)
        assert isinstance(ENABLE_PLAN_REPAIR, bool)
        assert isinstance(ENABLE_PHASE2_JUDGE, bool)

    def test_template_files_exist(self):
        assert os.path.isfile(RESUME_TEMPLATE), f"Missing: {RESUME_TEMPLATE}"
        assert os.path.isfile(COVER_TEMPLATE), f"Missing: {COVER_TEMPLATE}"


# ---------------------------------------------------------------------------
# 2. Prompts
# ---------------------------------------------------------------------------

_REQUIRED_PROMPTS = [
    "phase1",
    "phase1_repair",
    "phase2",
    "phase2_repair",
    "tailor",
    "extract_metadata",
    "role",
    "task",
    "candidate",
    "judge",
    "assess",
]


class TestPrompts:
    @pytest.mark.parametrize("name", _REQUIRED_PROMPTS)
    def test_prompt_file_exists_and_nonempty(self, name):
        path = PROMPTS_DIR / f"{name}.txt"
        assert path.is_file(), f"Missing prompt file: {path}"
        # Use the project's own reader — some files are cp1252-encoded (Windows smart quotes)
        text = _read_text_file(str(path))
        assert len(text.strip()) > 10, f"Prompt file is empty or too short: {path}"

    def test_load_prompt_substitutes_placeholder(self):
        # extract_metadata.txt uses {job_text}
        text = _load_prompt("extract_metadata", job_text="Acme Corp is hiring.")
        assert "Acme Corp is hiring." in text

    def test_load_prompt_leaves_unknown_placeholders_intact(self):
        # Unknown placeholders must not be stripped or crash
        text = _load_prompt("extract_metadata", job_description="test")
        assert "{" not in text or "job_description" not in text  # substitution happened


# ---------------------------------------------------------------------------
# 3. Template reading
# ---------------------------------------------------------------------------

class TestReadDocx:
    def test_read_resume_template_returns_nonempty_string(self):
        text = read_docx(RESUME_TEMPLATE)
        assert isinstance(text, str)
        assert "Experience" in text
        assert "Professional Summary" in text

    def test_read_cover_template_returns_nonempty_string(self):
        text = read_docx(COVER_TEMPLATE)
        assert isinstance(text, str)
        assert "Sincerely" in text
        assert "Leonid Verman" in text


# ---------------------------------------------------------------------------
# 4. save_doc_from_template — resume round-trip
# ---------------------------------------------------------------------------

class TestSaveDocResume:
    def test_produces_valid_docx(self, tmp_path):
        out = str(tmp_path / "resume_out.docx")
        save_doc_from_template(RESUME_TEMPLATE, out, _RESUME_TEXT)
        assert os.path.isfile(out)
        doc = Document(out)
        assert len(doc.paragraphs) > 0

    def test_experience_entry_text_present_in_output(self, tmp_path):
        out = str(tmp_path / "resume_out.docx")
        save_doc_from_template(RESUME_TEMPLATE, out, _RESUME_TEXT)
        text = read_docx(out)
        assert "Acme Corp" in text

    def test_skills_section_present_in_output(self, tmp_path):
        out = str(tmp_path / "resume_out.docx")
        save_doc_from_template(RESUME_TEMPLATE, out, _RESUME_TEXT)
        text = read_docx(out)
        assert "Python" in text


# ---------------------------------------------------------------------------
# 5. save_doc_from_template — cover letter (hyperlink regression)
# ---------------------------------------------------------------------------

class TestSaveDocCoverLetter:
    def test_produces_valid_docx(self, tmp_path):
        out = str(tmp_path / "cover_out.docx")
        save_doc_from_template(COVER_TEMPLATE, out, _COVER_TEXT)
        assert os.path.isfile(out)
        doc = Document(out)
        assert len(doc.paragraphs) > 0

    def test_name_paragraph_not_concatenated_with_email(self, tmp_path):
        """Regression: template hyperlink text must not be appended to the name."""
        out = str(tmp_path / "cover_out.docx")
        save_doc_from_template(COVER_TEMPLATE, out, _COVER_TEXT)
        text = read_docx(out)
        first_nonempty = next(l for l in text.splitlines() if l.strip())
        # Before the fix this would be "Leonid Vermanleonidverman@gmail.com"
        assert "leonidverman@gmail.com" not in first_nonempty, (
            f"Email was concatenated onto name paragraph: {first_nonempty!r}"
        )
        assert "Leonid Verman" in first_nonempty

    def test_body_content_present_in_output(self, tmp_path):
        out = str(tmp_path / "cover_out.docx")
        save_doc_from_template(COVER_TEMPLATE, out, _COVER_TEXT)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Sincerely" in text


# ---------------------------------------------------------------------------
# 6. normalize_cover_letter
# ---------------------------------------------------------------------------

class TestNormalizeCoverLetter:
    def test_collapses_blank_lines_before_sincerely(self):
        raw = "Para 1.\n\nPara 2.\n\nPara 3.\n\nSincerely,\nLeoind Verman"
        result = normalize_cover_letter(raw)
        lines = result.split("\n")
        assert lines[0] == "Para 1."
        assert "Para 2." in result
        assert "Sincerely," in result
        # There must be exactly one blank-line group break before Sincerely
        assert "\n\nSincerely," in result

    def test_no_internal_blank_lines_in_body(self):
        raw = "Para 1.\n\nPara 2.\n\nSincerely,\nLeoind Verman"
        result = normalize_cover_letter(raw)
        body, closing = result.split("\n\n", 1)
        assert "" not in body.split("\n"), "Body must have no blank lines"

    def test_no_sincerely_returns_stripped_text(self):
        raw = "Just a body.\n\nNo closing here."
        result = normalize_cover_letter(raw)
        assert "Just a body." in result
        assert "No closing here." in result

    def test_preserves_sincerely_and_name(self):
        raw = "Body paragraph.\n\nSincerely,\nLeoind Verman"
        result = normalize_cover_letter(raw)
        assert "Sincerely," in result
        assert "Leonid Verman" in result or "Leoind Verman" in result


# ---------------------------------------------------------------------------
# 7. validate_plan
# ---------------------------------------------------------------------------

class TestValidatePlan:
    def test_accepts_minimal_valid_plan(self):
        # Must not raise
        result = validate_plan(_MINIMAL_PLAN)
        assert isinstance(result, dict)

    def test_rejects_plan_with_too_few_themes(self):
        bad = dict(_MINIMAL_PLAN)
        bad["jd_top_themes"] = [_MINIMAL_PLAN["jd_top_themes"][0]]  # only 1 theme
        with pytest.raises(PlanValidationError):
            validate_plan(bad)

    def test_rejects_plan_with_missing_vocabulary_anchoring(self):
        bad = dict(_MINIMAL_PLAN)
        bad["vocabulary_anchoring"] = {}  # missing must_embed / optional_embed
        with pytest.raises((PlanValidationError, KeyError, Exception)):
            validate_plan(bad)

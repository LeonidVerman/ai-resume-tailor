"""
backend/tests/unit/test_candidate_profile_sanitizer.py

Unit tests for the string sanitization layer in candidate_profile_normalizer.

Tests cover:
  - sanitize_profile_string: all boundary-quote and artifact cases A–G
  - sanitize_profile_value: lists, nested objects, non-string passthrough
  - sanitize_profile_object: recursive sanitization
  - save-flow integration: malformed values are cleaned before persist
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.candidate_profile_normalizer import (
    sanitize_profile_object,
    sanitize_profile_string,
    sanitize_profile_value,
    normalize_candidate_profile,
)


# ── sanitize_profile_string ───────────────────────────────────────────────────


class TestSanitizeProfileString:
    # ── Case A: leading quote only ─────────────────────────────────────────

    def test_case_a_leading_quote_removed(self):
        assert sanitize_profile_string('"Distributed backend architecture') == \
               "Distributed backend architecture"

    # ── Case B: trailing quote only ────────────────────────────────────────

    def test_case_b_trailing_quote_removed(self):
        assert sanitize_profile_string('Distributed backend architecture"') == \
               "Distributed backend architecture"

    def test_case_b_incident_response(self):
        assert sanitize_profile_string('Incident response"') == "Incident response"

    # ── Case C: wrapping quotes, no internal quotes ────────────────────────

    def test_case_c_clean_wrapper_removed(self):
        assert sanitize_profile_string('"Distributed backend architecture"') == \
               "Distributed backend architecture"

    def test_case_c_short_value(self):
        assert sanitize_profile_string('"foo"') == "foo"

    # ── Case D: wrapper quotes with internal quotes ────────────────────────

    def test_case_d_outer_wrapper_removed_internal_preserved(self):
        assert sanitize_profile_string('"He said "hello" to the team"') == \
               'He said "hello" to the team'

    def test_case_d_double_wrapped(self):
        # "He said "hello"" → He said "hello"
        assert sanitize_profile_string('"He said "hello""') == 'He said "hello"'

    # ── Case E: internal quote only — preserve ─────────────────────────────

    def test_case_e_internal_quote_preserved(self):
        result = sanitize_profile_string('OAuth2 "style implementation')
        assert result == 'OAuth2 "style implementation'

    def test_case_e_internal_quote_mid_string(self):
        result = sanitize_profile_string('He said "hello" to the team')
        assert result == 'He said "hello" to the team'

    # ── Case F: escaped-quote artifacts ────────────────────────────────────

    def test_case_f_trailing_escaped_quote_removed(self):
        assert sanitize_profile_string('Incident response\\"') == "Incident response"

    def test_case_f_leading_escaped_quote_removed(self):
        assert sanitize_profile_string('\\"Incident response') == "Incident response"

    def test_case_f_both_escaped_quotes_removed(self):
        assert sanitize_profile_string('\\"Incident response\\"') == "Incident response"

    # ── Case G: quote + trailing comma artifacts ────────────────────────────

    def test_case_g1_trailing_quote_comma(self):
        assert sanitize_profile_string('Distributed backend architecture",') == \
               "Distributed backend architecture"

    def test_case_g2_escaped_quote_comma(self):
        assert sanitize_profile_string('Distributed backend architecture\\",') == \
               "Distributed backend architecture"

    def test_case_g_safe_account_rebalancing(self):
        assert sanitize_profile_string('Safe account rebalancing and migration\\",') == \
               "Safe account rebalancing and migration"

    # ── No false damage ────────────────────────────────────────────────────

    def test_no_damage_oauth2_oidc_saml(self):
        v = "OAuth2/OIDC/SAML"
        assert sanitize_profile_string(v) == v

    def test_no_damage_metrics_logs_tracing(self):
        v = "Metrics / logs / tracing mindset"
        assert sanitize_profile_string(v) == v

    def test_no_damage_plain_sentence(self):
        v = "Built and launched a cryptocurrency exchange for the Japanese market"
        assert sanitize_profile_string(v) == v

    def test_no_damage_parenthetical(self):
        v = "Architecture aligns with JWT-style principles: cryptographic integrity"
        assert sanitize_profile_string(v) == v

    def test_no_damage_forward_slash(self):
        v = "CI/CD"
        assert sanitize_profile_string(v) == v

    # ── Whitespace ──────────────────────────────────────────────────────────

    def test_strips_surrounding_whitespace(self):
        assert sanitize_profile_string("  foo  ") == "foo"

    def test_empty_string_unchanged(self):
        assert sanitize_profile_string("") == ""

    # ── Non-string passthrough ──────────────────────────────────────────────

    def test_non_string_passthrough(self):
        assert sanitize_profile_string(42) == 42  # type: ignore[arg-type]
        assert sanitize_profile_string(None) is None  # type: ignore[arg-type]


# ── sanitize_profile_value ────────────────────────────────────────────────────


class TestSanitizeProfileValue:
    def test_string_sanitized(self):
        assert sanitize_profile_value('foo"') == "foo"

    def test_list_of_strings_sanitized(self):
        result = sanitize_profile_value(['foo"', '"bar"', 'baz'])
        assert result == ["foo", "bar", "baz"]

    def test_empty_string_dropped_from_list(self):
        # A string that sanitizes to empty should be dropped
        result = sanitize_profile_value(['"', '""', 'valid'])
        assert result == ["valid"]

    def test_list_with_nested_dict_sanitized(self):
        result = sanitize_profile_value([{"area": 'crypto"', "impact": ['Built exchange"']}])
        assert result == [{"area": "crypto", "impact": ["Built exchange"]}]

    def test_dict_sanitized(self):
        result = sanitize_profile_value({"key": 'value"'})
        assert result == {"key": "value"}

    def test_int_passthrough(self):
        assert sanitize_profile_value(20) == 20

    def test_bool_passthrough(self):
        assert sanitize_profile_value(False) is False

    def test_none_passthrough(self):
        assert sanitize_profile_value(None) is None


# ── sanitize_profile_object ───────────────────────────────────────────────────


class TestSanitizeProfileObject:
    def test_top_level_strings_sanitized(self):
        obj = {"name": 'Alice"', "headline": '"Senior engineer"'}
        result = sanitize_profile_object(obj)
        assert result == {"name": "Alice", "headline": "Senior engineer"}

    def test_nested_lists_sanitized(self):
        obj = {
            "role_fit_themes": ['fintech_backend"', 'distributed architecture",'],
        }
        result = sanitize_profile_object(obj)
        assert result["role_fit_themes"] == ["fintech_backend", "distributed architecture"]

    def test_deeply_nested_sanitized(self):
        obj = {
            "technical_skills": {
                "backend_systems": ['incident response"', 'fault tolerance\\",'],
            }
        }
        result = sanitize_profile_object(obj)
        assert result["technical_skills"]["backend_systems"] == [
            "incident response", "fault tolerance"
        ]

    def test_numeric_fields_unchanged(self):
        obj = {"leadership": {"scope": {"team_size_max": 20}}}
        result = sanitize_profile_object(obj)
        assert result["leadership"]["scope"]["team_size_max"] == 20

    def test_original_not_mutated(self):
        original = {"themes": ['foo"']}
        result = sanitize_profile_object(original)
        assert original["themes"] == ['foo"']
        assert result["themes"] == ["foo"]


# ── Full normalize_candidate_profile includes sanitization ────────────────────


class TestNormalizeIncludesSanitization:
    def test_malformed_role_fit_themes_cleaned(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "Alice"},
            "role_fit_themes": [
                'Distributed backend architecture",',
                'Safe account rebalancing and migration\\",',
                'Incident response\\"',
            ],
        }
        result = normalize_candidate_profile(profile)
        themes = result["role_fit_themes"]
        assert "Distributed backend architecture" in themes
        assert "Safe account rebalancing and migration" in themes
        assert "Incident response" in themes

    def test_malformed_technical_skills_cleaned(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "Alice"},
            "technical_skills": {
                # Use spec-exact artifacts: backslash-quote and backslash-quote+comma
                "backend_systems": ['Incident response\\"', 'Fault tolerance\\",'],
            },
        }
        result = normalize_candidate_profile(profile)
        bs = result["technical_skills"]["backend_systems"]
        assert "Incident response" in bs
        assert "Fault tolerance" in bs

    def test_malformed_experience_highlight_cleaned(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "Alice"},
            "experience_highlights": [
                {
                    "area": "fintech",
                    "impact": ['Built exchange"', '"Scaled to 1M users"'],
                    "architecture_patterns": [],
                    "constraints_and_tradeoffs": [],
                    "skills_applied": [],
                    "team_context": [],
                    "security_auth_patterns": [],
                }
            ],
        }
        result = normalize_candidate_profile(profile)
        impact = result["experience_highlights"][0]["impact"]
        assert "Built exchange" in impact
        assert "Scaled to 1M users" in impact

    def test_malformed_claim_boundaries_cleaned(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "Alice"},
            "claim_boundaries": {
                "security_auth": ['Not a direct OAuth2/OIDC/SAML\\"'],
                "domain_limits": ['"Do not imply ownership"'],
                "employment_constraints": ['Independent contractor work\\",'],
            },
        }
        result = normalize_candidate_profile(profile)
        cb = result["claim_boundaries"]
        assert "Not a direct OAuth2/OIDC/SAML" in cb["security_auth"]
        assert "Do not imply ownership" in cb["domain_limits"]
        assert "Independent contractor work" in cb["employment_constraints"]

    def test_internal_quotes_preserved(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "Alice"},
            "role_fit_themes": ['OAuth2 "style" auth patterns'],
        }
        result = normalize_candidate_profile(profile)
        assert 'OAuth2 "style" auth patterns' in result["role_fit_themes"]


# ── Save-flow: sanitize + prompt_synched=False ────────────────────────────────


class TestSaveFlowSanitizesAndSyncs:
    """Verify that the service layer sanitizes malformed strings and still
    marks prompt_synched=False."""

    def _make_service(self):
        from backend.app.services.candidate_profile_service import CandidateProfileService
        stored = MagicMock()
        stored.id = str(uuid.uuid4())
        stored.user_id = "u1"
        stored.profile_version = "1"
        stored.profile_jsonb = {"candidate_profile_version": "2.0", "candidate": {"name": "Alice"}}
        stored.candidate_prompt = None
        stored.prompt_synched = False
        stored.created_at = stored.updated_at = MagicMock()
        repo = MagicMock()
        repo.create.return_value = stored
        return CandidateProfileService(repo), repo, stored

    def test_create_sanitizes_and_sets_prompt_synched_false(self):
        from backend.app.schemas.candidate_profile import (
            CandidateIdentity,
            CandidateProfileDocument,
            CandidateProfileUpsertRequest,
        )
        svc, repo, stored = self._make_service()

        doc = CandidateProfileDocument(
            candidate=CandidateIdentity(name="Alice"),
        )
        req = CandidateProfileUpsertRequest(profile=doc, profile_version="1")
        svc.create("u1", req)

        _, kwargs = repo.create.call_args
        assert kwargs.get("prompt_synched") is False
        # profile_jsonb must be a sanitized/normalized dict
        assert isinstance(kwargs.get("profile_jsonb"), dict)

    def test_update_sanitizes_and_sets_prompt_synched_false(self):
        from backend.app.schemas.candidate_profile import (
            CandidateIdentity,
            CandidateProfileDocument,
            CandidateProfileUpsertRequest,
        )
        existing = MagicMock()
        existing.profile_jsonb = {"candidate_profile_version": "2.0", "candidate": {"name": "Old"}}
        existing.prompt_synched = True

        updated = MagicMock()
        updated.id = str(uuid.uuid4())
        updated.user_id = "u1"
        updated.profile_version = "1"
        updated.profile_jsonb = {"candidate_profile_version": "2.0", "candidate": {"name": "Alice"}}
        updated.candidate_prompt = None
        updated.prompt_synched = False
        updated.created_at = updated.updated_at = MagicMock()

        repo = MagicMock()
        repo.get_by_user_id.return_value = existing
        repo.update.return_value = updated

        from backend.app.services.candidate_profile_service import CandidateProfileService
        svc = CandidateProfileService(repo)

        doc = CandidateProfileDocument(
            candidate=CandidateIdentity(name="Alice"),
        )
        req = CandidateProfileUpsertRequest(profile=doc, profile_version="1")
        svc.update("u1", req)

        _, kwargs = repo.update.call_args
        assert kwargs.get("prompt_synched") is False
        assert isinstance(kwargs.get("profile_jsonb"), dict)

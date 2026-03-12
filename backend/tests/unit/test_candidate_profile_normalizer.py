"""
backend/tests/unit/test_candidate_profile_normalizer.py

Unit tests for candidate_profile_normalizer.
"""

from __future__ import annotations

import pytest

from backend.app.services.candidate_profile_normalizer import (
    _is_combined,
    _normalise_list,
    _token_to_readable,
    normalize_candidate_profile,
)


# ── Token-to-readable ─────────────────────────────────────────────────────────


class TestTokenToReadable:
    def test_no_underscore_unchanged(self):
        assert _token_to_readable("fintech") == "fintech"
        assert _token_to_readable("REST") == "REST"
        assert _token_to_readable("Kafka") == "Kafka"

    def test_basic_conversion(self):
        assert _token_to_readable("horizontal_scaling") == "Horizontal scaling"
        assert _token_to_readable("fault_tolerance") == "Fault tolerance"

    def test_connector_word_lowercase(self):
        assert _token_to_readable("async_decoupling_via_queues") == "Async decoupling via queues"
        assert _token_to_readable("crypto_and_blockchain_infrastructure") == "Crypto and blockchain infrastructure"

    def test_proper_noun_map(self):
        assert _token_to_readable("api_level_access_control") == "API level access control"
        assert _token_to_readable("distributed_session_validation_via_redis") == "Distributed session validation via Redis"
        assert _token_to_readable("llm_capabilities_and_limits") == "LLM capabilities and limits"
        assert _token_to_readable("rbac_style") == "RBAC style"

    def test_all_caps_preserved(self):
        assert _token_to_readable("RAG_pipeline") == "RAG pipeline"
        assert _token_to_readable("A_B_tests") == "A B tests"

    def test_first_word_capitalized(self):
        assert _token_to_readable("high_throughput_low_latency_design").startswith("High")

    def test_empty_parts_skipped(self):
        # Double underscore should not produce blank words
        result = _token_to_readable("foo__bar")
        assert "  " not in result


# ── Is-combined detection ─────────────────────────────────────────────────────


class TestIsCombined:
    def test_single_token_no_split(self):
        assert _is_combined("horizontal_scaling") is False

    def test_plain_sentence_no_split(self):
        assert _is_combined("Built and launched a crypto exchange") is False

    def test_one_underscore_part_not_combined(self):
        # Only one part with underscore — threshold is 2
        assert _is_combined("foo bar_baz") is False

    def test_two_underscore_parts_combined(self):
        assert _is_combined("foo_bar baz_qux") is True

    def test_three_underscore_parts_combined(self):
        assert _is_combined("horizontal_scaling read_replica multi_layer") is True


# ── Normalise list ────────────────────────────────────────────────────────────


class TestNormaliseList:
    def test_converts_tokens(self):
        result = _normalise_list(["horizontal_scaling", "read_replica_traffic_isolation"])
        assert result == ["Horizontal scaling", "Read replica traffic isolation"]

    def test_dedup_case_insensitive(self):
        result = _normalise_list(["horizontal_scaling", "Horizontal Scaling", "Horizontal scaling"])
        assert len(result) == 1
        assert result[0] == "Horizontal scaling"

    def test_split_combined_item(self):
        result = _normalise_list(["foo_bar baz_qux"])
        assert result == ["Foo bar", "Baz qux"]

    def test_plain_strings_unchanged(self):
        items = ["Built and launched a crypto exchange", "REST", "AWS"]
        assert _normalise_list(items) == items

    def test_non_string_items_skipped(self):
        result = _normalise_list(["valid_item", 42, None])
        assert result == ["Valid item"]

    def test_preserves_first_occurrence_on_dedup(self):
        # Human-readable comes first, token second → human-readable wins
        result = _normalise_list(["Horizontal scaling", "horizontal_scaling"])
        assert result == ["Horizontal scaling"]

    def test_proper_nouns_in_list(self):
        result = _normalise_list(["redis_caching", "kafka_streaming"])
        assert result == ["Redis caching", "Kafka streaming"]


# ── V1→V2 migration ───────────────────────────────────────────────────────────


class TestMigrateV1ToV2:
    def _v1_profile(self):
        return {
            "candidate_profile_version": "1.0",
            "candidate": {"name": "Alice"},
            "technical_skills": {"languages": ["Java"]},
            "experience_highlights": [
                {
                    "area": "fintech_platform",
                    "impact": ["Built a trading system"],
                    "constraints_and_tradeoffs": ["Tight deadline"],
                }
            ],
            "authz_authn_experience": {
                "implemented": ["encrypted_signed_session_tokens", "api_level_access_control_rbac_style"],
                "notes": ["Not a direct OAuth2/OIDC/SAML implementation"],
                "scalability_challenges": ["stateless_services_with_centralized_revocation"],
            },
            "scalability_reliability_patterns": ["horizontal_scaling", "multi_layer_caching"],
        }

    def test_version_bumped_to_2(self):
        result = normalize_candidate_profile(self._v1_profile())
        assert result["candidate_profile_version"] == "2.0"

    def test_authz_implemented_moved_to_technical_skills(self):
        result = normalize_candidate_profile(self._v1_profile())
        sap = result["technical_skills"]["security_auth_patterns"]
        assert "Encrypted signed session tokens" in sap
        assert "API level access control RBAC style" in sap

    def test_authz_notes_moved_to_claim_boundaries(self):
        result = normalize_candidate_profile(self._v1_profile())
        sec_auth = result["claim_boundaries"]["security_auth"]
        assert "Not a direct OAuth2/OIDC/SAML implementation" in sec_auth

    def test_authz_scalability_challenges_appended_to_first_highlight(self):
        result = normalize_candidate_profile(self._v1_profile())
        ct = result["experience_highlights"][0]["constraints_and_tradeoffs"]
        assert "Stateless services with centralized revocation" in ct
        assert "Tight deadline" in ct  # original preserved

    def test_root_scalability_reliability_patterns_moved(self):
        result = normalize_candidate_profile(self._v1_profile())
        srp = result["technical_skills"]["scalability_reliability_patterns"]
        assert "Horizontal scaling" in srp
        assert "Multi layer caching" in srp

    def test_old_keys_removed(self):
        result = normalize_candidate_profile(self._v1_profile())
        assert "authz_authn_experience" not in result
        assert "scalability_reliability_patterns" not in result

    def test_idempotent_on_v2_profile(self):
        v1 = self._v1_profile()
        v2 = normalize_candidate_profile(v1)
        v2_again = normalize_candidate_profile(v2)
        assert v2_again["technical_skills"]["security_auth_patterns"] == \
               v2["technical_skills"]["security_auth_patterns"]

    def test_no_highlights_scalability_challenges_skipped(self):
        profile = self._v1_profile()
        profile["experience_highlights"] = []
        result = normalize_candidate_profile(profile)
        # Should not raise; scalability_challenges simply not placed
        assert result["candidate_profile_version"] == "2.0"


# ── Full normalize_candidate_profile ─────────────────────────────────────────


class TestNormalizeCandidateProfile:
    def test_returns_deep_copy(self):
        profile = {"candidate_profile_version": "2.0", "candidate": {"name": "X"},
                   "role_fit_themes": ["fintech_backend"]}
        result = normalize_candidate_profile(profile)
        assert result is not profile

    def test_normalises_role_fit_themes(self):
        profile = {"candidate_profile_version": "2.0", "candidate": {"name": "X"},
                   "role_fit_themes": ["fintech_backend", "ai_platform_or_llm_integration"]}
        result = normalize_candidate_profile(profile)
        assert "Fintech backend" in result["role_fit_themes"]
        assert "AI platform or LLM integration" in result["role_fit_themes"]

    def test_normalises_technical_skills(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "X"},
            "technical_skills": {"async_messaging": ["message_queues", "idempotent_processing"]},
        }
        result = normalize_candidate_profile(profile)
        assert "Message queues" in result["technical_skills"]["async_messaging"]
        assert "Idempotent processing" in result["technical_skills"]["async_messaging"]

    def test_normalises_leadership_practices(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "X"},
            "leadership": {
                "scope": {"style_keywords": ["continuous_improvement"]},
                "practices": ["transparent_decision_making"],
                "risk_management": [],
            },
        }
        result = normalize_candidate_profile(profile)
        assert "Continuous improvement" in result["leadership"]["scope"]["style_keywords"]
        assert "Transparent decision making" in result["leadership"]["practices"]

    def test_normalises_claim_boundaries(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "X"},
            "claim_boundaries": {"security_auth": ["jwt_style_principles", "rbac_based_access"]},
        }
        result = normalize_candidate_profile(profile)
        assert "JWT style principles" in result["claim_boundaries"]["security_auth"]
        assert "RBAC based access" in result["claim_boundaries"]["security_auth"]

    def test_normalises_experience_highlight_lists(self):
        profile = {
            "candidate_profile_version": "2.0",
            "candidate": {"name": "X"},
            "experience_highlights": [
                {
                    "area": "platform",
                    "impact": ["Built a trading system"],
                    "security_auth_patterns": ["api_level_access_control_rbac_style"],
                    "architecture_patterns": [],
                    "constraints_and_tradeoffs": [],
                    "skills_applied": [],
                    "team_context": [],
                }
            ],
        }
        result = normalize_candidate_profile(profile)
        sap = result["experience_highlights"][0]["security_auth_patterns"]
        assert "API level access control RBAC style" in sap

"""Regression tests for Phase 2 components: WriterPacket and Phase2Validator.

All tests are deterministic — no LLM calls, no file I/O.
"""

import json
from datetime import date

import pytest

from tailor.phase2_validator import validate_phase2_output
from tailor.writer_packet import build_writer_packet


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _minimal_plan(extra_jd_keywords: list[str] | None = None) -> dict:
    """Return a minimal valid TailoringPlan."""
    base_keywords = ["distributed systems", "scalability", "microservices", "cloud", "Python"]
    extra = extra_jd_keywords or []
    return {
        "role_level": "senior",
        "jd_top_themes": [
            {
                "theme": f"Theme {i}",
                "why_important": "important",
                # Theme 0 gets all keywords so extra_jd_keywords are always captured
                "keywords": (base_keywords + extra) if i == 0 else base_keywords[:3],
            }
            for i in range(5)
        ],
        "evidence_map": [
            {
                "theme": "Theme 0",
                "evidence": [
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Scaled platform to 1M+ users",
                        "allowed_claims": ["scaled to 1M+ users"],
                    }
                ],
                "gaps": [],
                "safe_translation": ["high-throughput distributed platform scaling"],
            }
        ],
        "resume_strategy": {
            "summary": {"include_points": [], "avoid_points": []},
            "experience": [
                {
                    "role_name": "Senior Engineer | Acme Corp",
                    "priority": "high",
                    "keep_metrics": ["1M+ users", "25%"],
                    "bullets_to_emphasize": [],
                    "bullets_to_compress": [],
                    "bullets_to_reframe": [
                        {
                            "before": "built reusable middleware layer",
                            "after_intent": "modular extensibility-oriented integration layer",
                            "constraints": ["no new tools"],
                        }
                    ],
                }
            ],
            "skills": {
                "reorder_categories": [],
                "promote_skills": ["Docker"],
                "demote_skills": [],
                "do_not_add_skills": ["YANG", "SNMP"],
            },
        },
        "cover_letter_strategy": {
            "company_and_role_mentions": [],
            "bullet_overlaps_to_reference": [],
            "structure": [],
        },
        "risk_checks": {
            "do_not_invent": ["BGP", "OSPF"],
            "likely_hallucination_traps": ["networking protocols"],
            "claims_requiring_strict_grounding": [],
        },
    }


def _candidate_profile_str() -> str:
    return json.dumps(
        {
            "candidate": {"name": "Test User"},
            "experience_highlights": [
                {
                    "area": "crypto_exchange_platform",
                    "architecture_patterns": [
                        "Horizontal scaling of trading servers",
                        "Database read replicas for read-heavy GET endpoints",
                        "Multiple caching layers to reduce DB load",
                        "Asynchronous messaging for component decoupling",
                    ],
                }
            ],
            "technical_skills": {
                "languages": ["Java", "Python", "TypeScript"],
                "infra_devops": ["AWS", "Docker", "Kubernetes"],
                "datastores": ["MySQL", "PostgreSQL", "Redis", "Kafka"],
            },
            "scalability_reliability_patterns": [
                "horizontal_scaling",
                "read_replica_traffic_isolation",
            ],
        }
    )


def _master_resume_with_kafka() -> str:
    return (
        "Professional Summary\n"
        "Experienced backend engineer.\n\n"
        "Experience\n"
        "Senior Engineer | Acme Corp | 2020 - Present\n"
        "- Scaled platform to 1M+ users using horizontal scaling\n"
        "- Implemented read replicas for read-heavy traffic\n"
        "- Built multi-layer caching to reduce database load\n"
        "- Used async messaging for service decoupling\n\n"
        "Engineer | Beta Corp | 2017 - 2020\n"
        "- Developed backend services\n"
        "- Designed stateless microservices with Redis session validation\n"
        "- Applied idempotent processing patterns\n\n"
        "Technical Skills\n"
        "Languages: Java, Python, TypeScript\n"
        "Infrastructure: AWS, Docker, Kubernetes, Kafka\n"
        "Databases: MySQL, PostgreSQL, Redis\n"
    )


def _today() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# ---------------------------------------------------------------------------
# WriterPacket tests
# ---------------------------------------------------------------------------

class TestWriterPacket:

    def test_kafka_included_when_in_resume_and_jd(self):
        """Kafka in master_resume + Kafka in JD keywords -> in must_include_skills."""
        plan = _minimal_plan(extra_jd_keywords=["Kafka", "messaging"])
        packet = build_writer_packet(
            plan,
            _candidate_profile_str(),
            _master_resume_with_kafka(),
            "We need Kafka and distributed systems experience.",
        )
        must_skills = [s.lower() for s in packet["must_include_skills"]]
        assert "kafka" in must_skills, (
            f"Expected Kafka in must_include_skills, got: {packet['must_include_skills']}"
        )

    def test_kafka_not_included_when_missing_from_jd(self):
        """Kafka in resume but NOT in JD keywords -> should not be in must_include_skills."""
        plan = _minimal_plan()  # no Kafka keyword
        packet = build_writer_packet(
            plan,
            _candidate_profile_str(),
            _master_resume_with_kafka(),
            "We need distributed systems experience.",
        )
        must_skills = [s.lower() for s in packet["must_include_skills"]]
        assert "kafka" not in must_skills

    def test_baseline_metrics_always_included(self):
        """Baseline metrics (1M+, 25%, etc.) are always in must_keep_metrics."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        assert "1M+" in packet["must_keep_metrics"]
        assert "25%" in packet["must_keep_metrics"]

    def test_plan_keep_metrics_merged(self):
        """Metrics declared in plan.resume_strategy.experience[].keep_metrics are included."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        assert "1M+ users" in packet["must_keep_metrics"] or "1M+" in packet["must_keep_metrics"]
        assert "25%" in packet["must_keep_metrics"]

    def test_do_not_add_terms_from_plan(self):
        """do_not_add_terms should include plan risk_checks.do_not_invent + skills.do_not_add_skills."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        terms_lower = [t.lower() for t in packet["do_not_add_terms"]]
        assert "bgp" in terms_lower
        assert "ospf" in terms_lower
        assert "yang" in terms_lower
        assert "snmp" in terms_lower

    def test_mechanisms_from_profile(self):
        """must_surface_mechanisms includes architecture patterns from candidate profile."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, _candidate_profile_str(), "", "job desc")
        mechanisms_lower = [m.lower() for m in packet["must_surface_mechanisms"]]
        assert any("horizontal scaling" in m for m in mechanisms_lower)
        assert any("read replica" in m for m in mechanisms_lower)

    def test_integration_reframes_detected(self):
        """Bullets with integration/extensibility intent -> integration_extensibility_reframes."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        reframes = packet["integration_extensibility_reframes"]
        assert len(reframes) == 1
        assert "integration" in reframes[0]["safe_reframe_intent"].lower() or \
               "extensib" in reframes[0]["safe_reframe_intent"].lower()

    def test_density_targets_priority_based(self):
        """density_targets uses priority-based bullet and mechanism minimums."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        dt = packet["density_targets"]
        assert dt["bullet_min_by_priority"]["high"] == 4
        assert dt["bullet_min_by_priority"]["medium"] == 3
        assert dt["bullet_min_by_priority"]["low"] == 1
        assert dt["mechanism_min_by_priority"]["high"] == 2
        assert dt["mechanism_min_by_priority"]["medium"] == 1
        assert dt["mechanism_min_by_priority"]["low"] == 0

    def test_role_priorities_in_packet(self):
        """role_priorities maps each planned role name to its priority."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        assert "role_priorities" in packet
        assert packet["role_priorities"]["Senior Engineer | Acme Corp"] == "high"

    def test_role_source_bullet_counts_populated(self):
        """role_source_bullet_counts counts bullets per role from master resume."""
        plan = _minimal_plan()
        packet = build_writer_packet(
            plan, "{}", _master_resume_with_kafka(), "job desc"
        )
        counts = packet["role_source_bullet_counts"]
        assert "Senior Engineer | Acme Corp" in counts
        assert counts["Senior Engineer | Acme Corp"] == 4

    def test_hardcoded_unsafe_nouns_present(self):
        """unsafe_jd_nouns always includes hard-coded dangerous terms."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        nouns_lower = [n.lower() for n in packet["unsafe_jd_nouns"]]
        assert "low-code" in nouns_lower
        assert "snmp" in nouns_lower


# ---------------------------------------------------------------------------
# Phase2Validator tests
# ---------------------------------------------------------------------------

class TestPhase2Validator:

    def _make_packet(self, **overrides) -> dict:
        base = {
            "must_keep_metrics": ["1M+", "25%"],
            "must_surface_mechanisms": [
                "Horizontal scaling of trading servers",
                "Database read replicas for read-heavy GET endpoints",
                "Multiple caching layers to reduce DB load",
                "Asynchronous messaging for component decoupling",
                "Distributed session validation via Redis",
            ],
            "must_include_skills": ["Docker", "Kubernetes"],
            "allowed_skill_pool": ["Docker", "Kubernetes", "Python", "Redis"],
            "do_not_add_terms": ["BGP", "OSPF"],
            "unsafe_jd_nouns": ["SNMP", "BGP", "low-code"],
            "role_priorities": {
                "Senior Engineer | Acme Corp": "high",
                "Engineer | Beta Corp": "medium",
            },
            "role_source_bullet_counts": {},
            "role_source_char_counts": {},
            "jd_is_delivery_oriented": False,
            "density_targets": {
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
            },
        }
        base.update(overrides)
        return base

    def _make_resume(self, inject_mechanisms=True, bullet_count=4) -> str:
        bullets_role1 = []
        if inject_mechanisms:
            bullets_role1 = [
                "- Scaled to 1M+ users via horizontal scaling of trading servers",
                "- Added read replicas for read-heavy endpoints, cutting load by 25%",
                "- Built multi-layer caching to reduce database pressure",
                "- Async messaging for service decoupling; distributed session validation via Redis",
            ]
        else:
            bullets_role1 = [f"- Did some work {i}" for i in range(bullet_count)]

        bullets_role2 = [
            "- Designed stateless services",
            "- Implemented idempotent processing",
            "- Used read replicas for analytics",
            "- Applied async messaging patterns",
        ]

        return (
            "Professional Summary\n"
            "Experienced backend engineer.\n\n"
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            + "\n".join(bullets_role1)
            + "\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            + "\n".join(bullets_role2)
            + "\n\n"
            "Technical Skills\n"
            "Languages: Python, Java\n"
            "Infrastructure: Docker, Kubernetes, Redis\n"
        )

    def _make_cover(self, include_date=True) -> str:
        date_line = _today() if include_date else "January 1, 2000"
        return f"{date_line}\n\nDear Hiring Manager,\n\nI am a strong fit for this role."

    # --- Metrics ---

    def test_valid_output_passes(self):
        packet = self._make_packet()
        report = validate_phase2_output(
            packet,
            self._make_resume(inject_mechanisms=True),
            self._make_cover(include_date=True),
            _today(),
        )
        assert report["ok"] is True, f"Expected ok=True, errors: {report['errors']}"

    def test_missing_metric_1m_is_error(self):
        packet = self._make_packet(must_keep_metrics=["1M+"])
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Scaled the platform with horizontal scaling\n"
            "- Added read replicas for endpoints\n"
            "- Multi-layer caching reduced DB pressure by 25%\n"
            "- Async messaging for decoupling; distributed session validation via Redis\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Applied stateless services\n"
            "- Idempotent processing patterns\n"
            "- Read replicas for analytics\n"
            "- Async queue integration\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        assert not report["ok"]
        assert any("1M+" in e for e in report["errors"]), report["errors"]

    def test_metric_variant_accepted(self):
        """'1 million+' should satisfy the '1M+' requirement."""
        packet = self._make_packet(must_keep_metrics=["1M+"])
        resume = self._make_resume().replace("1M+", "over 1 million")
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        assert "1M+" not in " ".join(report["errors"]), report["errors"]

    # --- Unsafe terms ---

    def test_unsafe_term_bgp_is_error(self):
        packet = self._make_packet(unsafe_jd_nouns=["BGP"])
        resume = self._make_resume() + "\nUsed BGP routing for network management.\n"
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        assert not report["ok"]
        assert any("BGP" in e for e in report["errors"]), report["errors"]

    def test_unsafe_term_allowed_when_in_pool(self):
        """If the unsafe term is in allowed_skill_pool, it should not trigger an error."""
        packet = self._make_packet(
            unsafe_jd_nouns=["Redis"],
            allowed_skill_pool=["Redis", "Docker", "Kubernetes"],
        )
        resume = self._make_resume()  # Redis appears in skills section
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        unsafe_errors = [e for e in report["errors"] if "Redis" in e and "Unsafe" in e]
        assert not unsafe_errors, f"Redis should be allowed: {report['errors']}"

    # --- Required skills ---

    def test_missing_required_skill_is_error(self):
        packet = self._make_packet(must_include_skills=["Kafka"])
        resume = self._make_resume()  # no Kafka
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        assert not report["ok"]
        assert any("Kafka" in e for e in report["errors"]), report["errors"]

    def test_present_required_skill_passes(self):
        packet = self._make_packet(must_include_skills=["Docker"])
        resume = self._make_resume()  # Docker in Technical Skills
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        skill_errors = [e for e in report["errors"] if "Docker" in e and "skill" in e.lower()]
        assert not skill_errors, f"Docker should be found: {report['errors']}"

    # --- Date correctness ---

    def test_missing_current_date_is_error(self):
        packet = self._make_packet()
        report = validate_phase2_output(
            packet,
            self._make_resume(),
            self._make_cover(include_date=False),
            _today(),
        )
        assert not report["ok"]
        assert any("CURRENT_DATE" in e for e in report["errors"]), report["errors"]

    def test_correct_date_passes(self):
        packet = self._make_packet()
        report = validate_phase2_output(
            packet,
            self._make_resume(),
            self._make_cover(include_date=True),
            _today(),
        )
        date_errors = [e for e in report["errors"] if "CURRENT_DATE" in e]
        assert not date_errors, f"Date should pass: {report['errors']}"

    # --- Mechanism density ---

    def test_insufficient_mechanisms_is_error(self):
        """Roles without enough mechanism keywords trigger mechanism errors."""
        packet = self._make_packet(
            must_keep_metrics=[],
            must_include_skills=[],
        )
        bare_resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Improved scalability of the platform significantly\n"
            "- Reduced database load using caching solutions\n"
            "- Enhanced system reliability and performance\n"
            "- Deployed services to cloud infrastructure\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Delivered backend features on time\n"
            "- Mentored junior developers\n"
            "- Wrote unit tests\n"
            "- Performed code reviews\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, bare_resume, self._make_cover(), _today())
        mechanism_errors = [e for e in report["errors"] if "mechanism" in e.lower()]
        assert mechanism_errors, f"Expected mechanism errors, got: {report['errors']}"

    def test_mechanism_keyword_match(self):
        """Explicit mechanism keywords in bullet text satisfy the mechanism requirement."""
        packet = self._make_packet(
            must_keep_metrics=[],
            must_include_skills=[],
            role_priorities={"Senior Engineer | Acme Corp": "high"},
            role_source_bullet_counts={},
        )
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Used read replicas to isolate read traffic from writes\n"
            "- Applied horizontal scaling of the trading platform to handle peak load\n"
            "- Deployed containerized services using Docker\n"
            "- Implemented caching layer to reduce database pressure\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        mechanism_errors = [e for e in report["errors"] if "mechanism" in e.lower()]
        assert not mechanism_errors, f"keyword mechanism match should pass: {report['errors']}"

    # --- Bullet density ---

    def test_too_few_bullets_is_error(self):
        packet = self._make_packet()
        sparse_resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Only one bullet\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Only one bullet here too\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, sparse_resume, self._make_cover(), _today())
        density_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert density_errors, f"Expected density errors, got: {report['errors']}"

    def test_top_k_roles_promoted_to_high(self):
        """First 2 non-thin roles are promoted to 'high' effective priority.

        A 3-role resume: Acme (plan=high), Beta (plan=medium), Gamma (plan=low).
        Top-K promotion upgrades both Acme and Beta to effective 'high'.
        Gamma is 3rd non-thin — keeps plan priority 'low'.
        With 3 bullets each: Acme/Beta fail (3 < 4), Gamma passes (3 >= 1).
        """
        packet = self._make_packet(
            must_keep_metrics=[],
            must_include_skills=[],
            role_priorities={
                "Senior Engineer | Acme Corp": "high",
                "Engineer | Beta Corp": "medium",
                "Dev | Gamma Corp": "low",
            },
            role_source_bullet_counts={},
            role_source_char_counts={},
        )
        three_role_resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Implemented horizontal scaling for the platform\n"
            "- Built caching layer with read replicas to isolate DB load\n"
            "- Used async messaging for service decoupling\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Designed distributed system with read replicas\n"
            "- Implemented async messaging patterns\n"
            "- Used Redis for caching\n\n"
            "Dev | Gamma Corp | 2015 - 2017\n"
            "- Wrote backend code\n"
            "- Fixed production bugs\n"
            "- Deployed new features\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, three_role_resume, self._make_cover(), _today())
        # Acme and Beta both promoted to effective high → 3 < 4 → errors
        acme_errors = [e for e in report["errors"] if "bullet" in e.lower() and "Acme Corp" in e]
        beta_errors = [e for e in report["errors"] if "bullet" in e.lower() and "Beta Corp" in e]
        gamma_errors = [e for e in report["errors"] if "bullet" in e.lower() and "Gamma Corp" in e]
        assert acme_errors, f"Expected Acme Corp bullet error (promoted to high): {report['errors']}"
        assert beta_errors, f"Expected Beta Corp bullet error (promoted to high): {report['errors']}"
        assert not gamma_errors, f"Gamma Corp (low, 3rd non-thin) should pass: {report['errors']}"

    def test_thin_role_safeguard_relaxes_bullet_minimum(self):
        """Role with <2 source bullets gets minimum bullet count reduced by 1."""
        packet = self._make_packet(
            must_keep_metrics=[],
            must_include_skills=[],
            role_priorities={"Short Role | Corp": "medium"},
            role_source_bullet_counts={"Short Role | Corp": 1},  # thin source
        )
        resume = (
            "Experience\n"
            "Short Role | Corp | 2024 - Present\n"
            "- Implemented async messaging pipeline for order processing\n"
            "- Built read replicas to reduce database load\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        report = validate_phase2_output(packet, resume, self._make_cover(), _today())
        # Medium normally needs 3 bullets; thin safeguard reduces to 2. 2 >= 2 -> passes.
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, f"Thin-role safeguard should relax minimum: {report['errors']}"

    # --- Stats ---

    def test_stats_populated(self):
        """ValidationReport stats keys should all be present."""
        packet = self._make_packet()
        report = validate_phase2_output(
            packet, self._make_resume(), self._make_cover(), _today()
        )
        stats = report["stats"]
        assert "metrics_found" in stats
        assert "missing_required_skills" in stats
        assert "unsafe_terms_found" in stats
        assert "role_bullet_counts" in stats
        assert "role_mechanism_counts" in stats


# ---------------------------------------------------------------------------
# Thin / non-repositioning role override tests
# ---------------------------------------------------------------------------

class TestThinRoleOverride:
    """Tests for thin-role detection and effective priority override."""

    def _packet_for_thin(self, role_name: str, source_bullets: int = 0,
                         source_chars: int = 0, jd_delivery: bool = False) -> dict:
        return {
            "must_keep_metrics": [],
            "must_surface_mechanisms": [],
            "must_include_skills": [],
            "allowed_skill_pool": [],
            "do_not_add_terms": [],
            "unsafe_jd_nouns": [],
            "role_priorities": {role_name: "high"},
            "role_source_bullet_counts": {role_name: source_bullets},
            "role_source_char_counts": {role_name: source_chars},
            "jd_is_delivery_oriented": jd_delivery,
            "density_targets": {
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
            },
        }

    def _cover(self) -> str:
        d = date.today()
        date_str = f"{d.strftime('%B')} {d.day}, {d.year}"
        return f"{date_str}\n\nDear Hiring Manager,\n\nI am a strong fit."

    def test_mercor_name_triggers_thin_override(self):
        """Role name containing 'mercor' is classified as thin regardless of content."""
        role_name = "AI Evaluator | Mercor"
        packet = self._packet_for_thin(role_name, source_bullets=5, source_chars=300)
        # 1 bullet — passes thin_override (min=2 would still fail, but 2 should pass)
        resume = (
            "Experience\n"
            f"{role_name} | 2024 - Present\n"
            "- Evaluated model outputs\n"
            "- Provided quality feedback\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(packet, resume, self._cover(), _today())
        # Should emit a warning (not error) about thin override
        thin_warnings = [w for w in report["warnings"] if "thin override" in w.lower()]
        assert thin_warnings, f"Expected thin override warning: {report['warnings']}"
        # With 2 bullets and thin_override min=2, no bullet errors
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, f"Thin override should allow 2 bullets: {report['errors']}"

    def test_thin_content_triggers_without_name_match(self):
        """Role with 1 source bullet triggers thin_override even without a name pattern."""
        role_name = "Software Engineer | Some Corp"
        packet = self._packet_for_thin(role_name, source_bullets=1, source_chars=300)
        resume = (
            "Experience\n"
            f"{role_name} | 2024 - Present\n"
            "- Built backend services\n"
            "- Wrote unit tests\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(packet, resume, self._cover(), _today())
        thin_warnings = [w for w in report["warnings"] if "thin override" in w.lower()]
        assert thin_warnings, f"Expected thin override warning for thin content: {report['warnings']}"

    def test_mercor_passes_with_two_bullets_zero_mechanisms(self):
        """Thin-override role passes with exactly 2 bullets and 0 mechanism keywords."""
        role_name = "AI Model Evaluator | Mercor"
        packet = self._packet_for_thin(role_name, source_bullets=5, source_chars=300)
        resume = (
            "Experience\n"
            f"{role_name} | 2024 - Present\n"
            "- Reviewed AI-generated responses for accuracy\n"
            "- Documented evaluation findings and submitted reports\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(packet, resume, self._cover(), _today())
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        mechanism_errors = [e for e in report["errors"] if "mechanism" in e.lower()]
        assert not bullet_errors, f"2 bullets should satisfy thin_override min=2: {report['errors']}"
        assert not mechanism_errors, f"0 mechanisms required for thin_override: {report['errors']}"

    def test_non_thin_roles_after_thin_still_require_high_density(self):
        """First 2 non-thin roles after a thin role still get high-priority enforcement."""
        packet = {
            "must_keep_metrics": [],
            "must_surface_mechanisms": [],
            "must_include_skills": [],
            "allowed_skill_pool": [],
            "do_not_add_terms": [],
            "unsafe_jd_nouns": [],
            "role_priorities": {
                "AI Evaluator | Mercor": "high",      # thin role
                "Senior Engineer | Acme Corp": "high",  # non-thin #1
                "Engineer | Beta Corp": "medium",       # non-thin #2, promoted to high
            },
            "role_source_bullet_counts": {},
            "role_source_char_counts": {},
            "jd_is_delivery_oriented": False,
            "density_targets": {
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
            },
        }
        resume = (
            "Experience\n"
            "AI Evaluator | Mercor | 2024 - Present\n"
            "- Evaluated AI model outputs\n"
            "- Submitted feedback reports\n\n"
            "Senior Engineer | Acme Corp | 2022 - 2024\n"
            "- Built backend platform\n"
            "- Wrote unit tests\n"
            "- Deployed services\n\n"
            "Engineer | Beta Corp | 2019 - 2022\n"
            "- Developed features\n"
            "- Fixed bugs\n"
            "- Reviewed code\n\n"
            "Technical Skills\nPython\n"
        )
        d = date.today()
        cover = f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."
        report = validate_phase2_output(packet, resume, cover, _today())
        # Mercor is thin — passes with 2 bullets (no bullet error)
        mercor_bullet_errors = [e for e in report["errors"] if "Mercor" in e and "bullet" in e.lower()]
        assert not mercor_bullet_errors, f"Thin Mercor should pass: {report['errors']}"
        # Acme Corp is non-thin #1 → promoted to high → 3 bullets < 4 → error
        acme_errors = [e for e in report["errors"] if "Acme Corp" in e and "bullet" in e.lower()]
        assert acme_errors, f"Acme Corp (non-thin, high) should require 4 bullets: {report['errors']}"
        # Beta Corp is non-thin #2 → promoted to high → 3 bullets < 4 → error
        beta_errors = [e for e in report["errors"] if "Beta Corp" in e and "bullet" in e.lower()]
        assert beta_errors, f"Beta Corp (promoted to high) should require 4 bullets: {report['errors']}"


# ---------------------------------------------------------------------------
# Additional WriterPacket tests for new fields
# ---------------------------------------------------------------------------

class TestWriterPacketNewFields:

    def test_role_source_char_counts_populated(self):
        """role_source_char_counts has a positive count for roles with content in master resume."""
        plan = _minimal_plan()
        packet = build_writer_packet(
            plan, "{}", _master_resume_with_kafka(), "job desc"
        )
        counts = packet["role_source_char_counts"]
        assert "Senior Engineer | Acme Corp" in counts
        assert counts["Senior Engineer | Acme Corp"] > 0

    def test_jd_is_delivery_oriented_true_when_keywords_present(self):
        """jd_is_delivery_oriented=True when JD themes contain delivery keywords."""
        plan = _minimal_plan(extra_jd_keywords=["microservices", "kubernetes"])
        packet = build_writer_packet(plan, "{}", "", "job desc")
        assert packet["jd_is_delivery_oriented"] is True

    def test_jd_is_delivery_oriented_false_when_no_keywords(self):
        """jd_is_delivery_oriented=False when JD has no delivery-oriented keywords."""
        plan = _minimal_plan()  # base keywords: distributed systems, scalability, etc.
        # Remove delivery keywords — use a plan with only neutral keywords
        for theme in plan["jd_top_themes"]:
            theme["keywords"] = ["data analysis", "reporting", "documentation"]
        packet = build_writer_packet(plan, "{}", "", "job desc")
        assert packet["jd_is_delivery_oriented"] is False


# ---------------------------------------------------------------------------
# Very-old-role bullet relaxation tests
# ---------------------------------------------------------------------------

class TestVeryOldRoleRelaxation:
    """Tests for the thin_override + very-old-role (> VERY_OLD_ROLE_YEARS) relaxation."""

    # Fixed reference date: 2026-02-24 (matches project memory today).
    _NOW = date(2026, 2, 24)

    def _packet(self, role_name: str, plan_priority: str = "high") -> dict:
        return {
            "must_keep_metrics": [],
            "must_surface_mechanisms": [],
            "must_include_skills": [],
            "allowed_skill_pool": [],
            "do_not_add_terms": [],
            "unsafe_jd_nouns": [],
            "role_priorities": {role_name: plan_priority},
            "role_source_bullet_counts": {},
            "role_source_char_counts": {},
            "jd_is_delivery_oriented": False,
            "density_targets": {
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
            },
        }

    def _cover(self) -> str:
        d = self._NOW
        return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."

    def _resume_one_bullet(self, header: str) -> str:
        return (
            "Experience\n"
            f"{header}\n"
            "- Maintained legacy system components\n\n"
            "Technical Skills\nPython\n"
        )

    # --- Test 1: thin_override + end_year=2008, now=2026 → relaxed to min=1 ---

    def test_very_old_thin_override_allows_one_bullet(self):
        """thin_override role ending 2008 (18 years ago) accepts 1 bullet (very old)."""
        role_name = "AI Evaluator | Some Corp"
        header = f"{role_name} | 2004 - 2008"
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"Very-old thin_override (2008) should pass with 1 bullet: {report['errors']}"
        )

    # --- Test 2: thin_override + end_year=2015, now=2026 → NOT very old (11 years) ---

    def test_recent_thin_override_keeps_min_two_bullets(self):
        """thin_override role ending 2015 (11 years ago) still requires 2 bullets."""
        role_name = "AI Evaluator | Some Corp"
        header = f"{role_name} | 2013 - 2015"
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors, (
            f"thin_override ending 2015 should still need 2 bullets: {report['errors']}"
        )
        assert any("minimum is 2" in e for e in bullet_errors), bullet_errors

    # --- Test 3: thin_override + no date in header → unknown → min=2 ---

    def test_thin_override_unknown_date_keeps_min_two_bullets(self):
        """thin_override with no parseable date applies the default min=2."""
        role_name = "AI Evaluator | Some Corp"
        header = role_name  # no date range
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors, (
            f"thin_override with unknown date should need 2 bullets: {report['errors']}"
        )

    # --- Test 4: high priority + end_year=2008 → no relaxation (still high rules) ---

    def test_high_priority_very_old_role_not_relaxed(self):
        """Very-old-role relaxation only applies to thin_override, not high-priority roles."""
        role_name = "Senior Engineer | Acme Corp"
        header = f"{role_name} | 2004 - 2008"
        packet = self._packet(role_name, plan_priority="high")
        # Non-thin role → top-K promoted to "high" effective priority (4 bullets required)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors, (
            f"High-priority very-old role should still require 4 bullets: {report['errors']}"
        )
        assert any("minimum is 4" in e for e in bullet_errors), bullet_errors

    # --- Test 5: thin_override + Present → not old → min=2 ---

    def test_thin_override_present_role_not_relaxed(self):
        """Ongoing thin_override role (Present) is not very old; requires 2 bullets."""
        role_name = "AI Evaluator | Mercor"
        header = f"{role_name} | 2024 - Present"
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors, (
            f"Ongoing thin_override role should require 2 bullets: {report['errors']}"
        )
        assert any("minimum is 2" in e for e in bullet_errors), bullet_errors

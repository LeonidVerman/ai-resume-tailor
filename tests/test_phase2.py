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

    def test_priority_based_enforcement_high_needs_more_than_medium(self):
        """High-priority role requires more bullets than medium-priority role."""
        # Resume with 3 bullets per role:
        # high needs 4 → error; medium needs 3 → passes.
        three_bullet_resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Implemented horizontal scaling for 1M+ user platform, reducing latency by 25%\n"
            "- Built caching layer with read replicas to isolate DB load\n"
            "- Used async messaging and Docker for service decoupling\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Designed distributed system with read replicas\n"
            "- Implemented async messaging patterns\n"
            "- Used Redis for caching\n\n"
            "Technical Skills\nDocker, Kubernetes\n"
        )
        packet = self._make_packet()
        report = validate_phase2_output(packet, three_bullet_resume, self._make_cover(), _today())
        # High role should fail bullet check (3 < 4)
        high_bullet_errors = [
            e for e in report["errors"]
            if "bullet" in e.lower() and "Acme Corp" in e
        ]
        assert high_bullet_errors, f"Expected high-role bullet error: {report['errors']}"
        # Medium role should NOT have a bullet error (3 >= 3)
        medium_bullet_errors = [
            e for e in report["errors"]
            if "bullet" in e.lower() and "Beta Corp" in e
        ]
        assert not medium_bullet_errors, f"Medium role should pass: {report['errors']}"

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

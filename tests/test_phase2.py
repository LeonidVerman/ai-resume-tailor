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
        """must_surface_arch_mechanisms includes architecture patterns from candidate profile."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, _candidate_profile_str(), "", "job desc")
        mechanisms_lower = [m.lower() for m in packet["must_surface_arch_mechanisms"]]
        assert any("horizontal scaling" in m for m in mechanisms_lower)
        assert any("read replica" in m for m in mechanisms_lower)

    def test_mechanisms_with_unsafe_nouns_are_filtered(self):
        """Mechanism phrases containing an unsafe JD noun are excluded from must_surface_arch_mechanisms."""
        import json as _json
        # Profile with one clean pattern and one containing a hardcoded unsafe noun.
        profile_str = _json.dumps({
            "experience_highlights": [
                {
                    "area": "networking",
                    "architecture_patterns": [
                        "horizontal scaling of API servers",
                        "SNMP-based asset discovery for network inventory",
                    ],
                }
            ],
            "scalability_reliability_patterns": [],
        })
        plan = _minimal_plan()
        packet = build_writer_packet(plan, profile_str, "", "job desc")
        mechanisms_lower = [m.lower() for m in packet["must_surface_arch_mechanisms"]]
        # Safe pattern kept
        assert any("horizontal scaling" in m for m in mechanisms_lower)
        # Pattern containing the unsafe noun must be dropped
        assert not any("snmp" in m for m in mechanisms_lower)
        assert not any("asset discovery" in m for m in mechanisms_lower)

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

    def test_unmarked_bullets_counted_as_fallback(self):
        """Roles with plain sentences (no - or •) use the fallback bullet counter."""
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "Nov 2022 – Present\n"
            "Scaled the platform to handle 1M+ concurrent users.\n"
            "Reduced p99 latency by 25% via multi-layer caching.\n"
            "Led migration of three legacy services to microservices.\n"
        )
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        counts = packet["role_source_bullet_counts"]
        # Three content lines, one date line → fallback should yield 3
        assert counts.get("Senior Engineer | Acme Corp", 0) == 3

    def test_explicit_bullets_not_displaced_by_fallback(self):
        """Explicit - bullets are counted normally; fallback is not applied."""
        # _master_resume_with_kafka uses explicit bullets — count must stay at 4
        plan = _minimal_plan()
        packet = build_writer_packet(
            plan, "{}", _master_resume_with_kafka(), "job desc"
        )
        counts = packet["role_source_bullet_counts"]
        assert counts["Senior Engineer | Acme Corp"] == 4

    def test_role_name_pipe_spacing_normalized_for_matching(self):
        """Plan role names with different spacing around | still match resume headers."""
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Built distributed systems\n"
            "- Improved reliability via read replicas\n"
        )
        # Plan uses no spaces around pipe
        plan = _minimal_plan()
        # Override role name in the plan to use collapsed spacing
        plan["resume_strategy"]["experience"][0]["role_name"] = "Senior Engineer|Acme Corp"
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        counts = packet["role_source_bullet_counts"]
        assert counts.get("Senior Engineer|Acme Corp", 0) == 2

    def test_hardcoded_unsafe_nouns_present(self):
        """unsafe_jd_nouns always includes hard-coded dangerous terms."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", "", "job desc")
        nouns_lower = [n.lower() for n in packet["unsafe_jd_nouns"]]
        assert "low-code" in nouns_lower
        assert "snmp" in nouns_lower

    def test_abbreviated_plan_role_name_still_matches_resume(self):
        """Abbreviated plan title gets correct bullet count via pipe-part matching.

        Real-world case: the DOCX has 'VP / Director / Lead Software Developer | Corp'
        but Phase 1 abbreviated it to 'VP / Director | Corp'.  The old substring
        check failed; _roles_match must resolve it correctly.
        """
        resume = (
            "Experience\n"
            "VP / Director of Software Development / Lead Software Developer"
            " | CardinalChain Software Inc | 2019 - 2024\n"
            "- Directed engineering for enterprise-scale crypto trading platforms\n"
            "- Improved throughput by 25% via transactional cache\n"
            "- Mentored and managed a team of developers\n\n"
            "Technical Skills\nLanguages: Python\n"
        )
        plan = _minimal_plan()
        plan["resume_strategy"]["experience"][0]["role_name"] = (
            "VP / Director of Software Development | CardinalChain Software Inc"
        )
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        counts = packet["role_source_bullet_counts"]
        abbreviated_key = "VP / Director of Software Development | CardinalChain Software Inc"
        assert counts.get(abbreviated_key) == 3, (
            f"Expected 3 bullets via pipe-part match; got {counts.get(abbreviated_key)}"
        )


# ---------------------------------------------------------------------------
# Phase2Validator tests
# ---------------------------------------------------------------------------

class TestPhase2Validator:

    def _make_packet(self, **overrides) -> dict:
        base = {
            "must_keep_metrics": ["1M+", "25%"],
            "must_surface_arch_mechanisms": [
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
            "must_surface_arch_mechanisms": [],
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
        # 1 bullet — passes thin_override (min=1)
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
        # With 2 bullets and thin_override min=1, no bullet errors
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
        assert not bullet_errors, f"2 bullets should satisfy thin_override min=1: {report['errors']}"
        assert not mechanism_errors, f"0 mechanisms required for thin_override: {report['errors']}"

    def test_non_thin_roles_after_thin_still_require_high_density(self):
        """First 2 non-thin roles after a thin role still get high-priority enforcement."""
        packet = {
            "must_keep_metrics": [],
            "must_surface_arch_mechanisms": [],
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
    """Tests for very-old-role (> VERY_OLD_ROLE_YEARS) bullet relaxation.

    Very-old-role relaxation applies to ALL effective priorities (thin_override,
    high, medium, low).  Any role whose end year is more than VERY_OLD_ROLE_YEARS
    years ago is relaxed to min_bullets=1 and mech_required=0.
    """

    # Fixed reference date: 2026-02-24 (matches project memory today).
    _NOW = date(2026, 2, 24)

    def _packet(self, role_name: str, plan_priority: str = "high") -> dict:
        return {
            "must_keep_metrics": [],
            "must_surface_arch_mechanisms": [],
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

    def test_recent_thin_override_allows_one_bullet(self):
        """thin_override role ending 2015 (11 years ago) passes with 1 bullet (min=1)."""
        role_name = "AI Evaluator | Some Corp"
        header = f"{role_name} | 2013 - 2015"
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"thin_override ending 2015 should pass with 1 bullet (min=1): {report['errors']}"
        )

    # --- Test 3: thin_override + no date in header → unknown → min=2 ---

    def test_thin_override_unknown_date_allows_one_bullet(self):
        """thin_override with no parseable date uses the default min=1."""
        role_name = "AI Evaluator | Some Corp"
        header = role_name  # no date range
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"thin_override with unknown date should pass with 1 bullet (min=1): {report['errors']}"
        )

    # --- Test 4: high priority + end_year=2008 → relaxed to 1 bullet (very old) ---

    def test_high_priority_very_old_role_relaxed_to_one_bullet(self):
        """Very-old-role relaxation applies to high-priority roles too."""
        role_name = "Senior Engineer | Acme Corp"
        header = f"{role_name} | 2004 - 2008"
        packet = self._packet(role_name, plan_priority="high")
        # Non-thin role → top-K would give high priority, but very-old overrides to 1 bullet.
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"High-priority very-old role should be relaxed to 1 bullet: {report['errors']}"
        )

    # --- Test 5: thin_override + Present → not old → min=2 ---

    def test_thin_override_present_role_allows_one_bullet(self):
        """Ongoing thin_override role (Present) passes with 1 bullet (min=1)."""
        role_name = "AI Evaluator | Mercor"
        header = f"{role_name} | 2024 - Present"
        packet = self._packet(role_name)
        report = validate_phase2_output(
            packet, self._resume_one_bullet(header), self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"Ongoing thin_override role should pass with 1 bullet (min=1): {report['errors']}"
        )

    # --- Test 6: date on SEPARATE line (Borland scenario) → still detected as very old ---

    def test_very_old_role_date_on_separate_line(self):
        """When the LLM puts the date on a separate line, the role is still detected as very old."""
        role_name = "Software Engineer | Borland Software AG"
        # Header has no date; date appears on the next line (dropped by _parse_roles).
        resume = (
            "Experience\n"
            f"{role_name}\n"
            "2004 - 2008\n"           # separate date line
            "- Maintained legacy components\n\n"
            "Technical Skills\nC++\n"
        )
        packet = self._packet(role_name, plan_priority="low")
        report = validate_phase2_output(
            packet, resume, self._cover(),
            f"{self._NOW.strftime('%B')} {self._NOW.day}, {self._NOW.year}",
            now=self._NOW,
        )
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert not bullet_errors, (
            f"thin role with separate date line (2008) should pass with 1 bullet: {report['errors']}"
        )


# ---------------------------------------------------------------------------
# Role density shortfall allowance tests
# ---------------------------------------------------------------------------

class TestRoleDensityShortfallAllowance:
    """Tests for role_density_shortfall_allowance field in WriterPacket.

    The allowance field records how many new derived bullets the writer is
    permitted to add per role when the master resume has fewer bullets than
    the density minimum.  Allowance is capped at 1 and suppressed for thin
    roles and roles with no plan evidence.
    """

    # Bullet text long enough so 2 bullets (≈180 chars) exceed the thin char
    # threshold of 150, ensuring content-based thinness is not triggered.
    _LONG_BULLET = (
        "Implemented a scalable distributed caching layer that reduced database load significantly"
    )

    def _plan(self, role_name: str, priority: str, with_evidence: bool = True) -> dict:
        """Minimal TailoringPlan for allowance testing."""
        evidence_entry = {
            "theme": "Theme 0",
            "evidence": [
                {
                    "source": "master_resume",
                    "location": "Corp",
                    "quote": "Built scalable platform",
                    "allowed_claims": ["built scalable platform"] if with_evidence else [],
                }
            ],
            "gaps": [],
            "safe_translation": [],
        }
        return {
            "role_level": "senior",
            "jd_top_themes": [
                {"theme": f"Theme {i}", "why_important": "important", "keywords": ["distributed systems"]}
                for i in range(5)
            ],
            "evidence_map": [evidence_entry],
            "resume_strategy": {
                "summary": {"include_points": [], "avoid_points": []},
                "experience": [
                    {
                        "role_name": role_name,
                        "priority": priority,
                        "keep_metrics": [],
                        "bullets_to_emphasize": [],
                        "bullets_to_compress": [],
                        "bullets_to_reframe": [],
                    }
                ],
                "skills": {
                    "reorder_categories": [],
                    "promote_skills": [],
                    "demote_skills": [],
                    "do_not_add_skills": [],
                },
            },
            "cover_letter_strategy": {
                "company_and_role_mentions": [],
                "bullet_overlaps_to_reference": [],
                "structure": [],
            },
            "risk_checks": {
                "do_not_invent": [],
                "likely_hallucination_traps": [],
                "claims_requiring_strict_grounding": [],
            },
        }

    def _resume(self, role_header: str, bullet_count: int) -> str:
        """Master resume with the given role and explicit bullet lines."""
        bullets = "\n".join(f"- {self._LONG_BULLET}" for _ in range(bullet_count))
        return (
            "Experience\n"
            f"{role_header}\n"
            f"{bullets}\n\n"
            "Technical Skills\n"
            "Languages: Python\n"
        )

    def test_high_priority_shortfall_grants_allowance_one(self):
        """High priority with 3 source bullets (min=4) → allowance=1."""
        role = "Senior Engineer | Acme Corp | 2020 - Present"
        packet = build_writer_packet(
            self._plan(role, "high"), "{}", self._resume(role, 3), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 1

    def test_high_priority_no_shortfall_gives_zero(self):
        """High priority with 5 source bullets (min=4) → allowance=0 (no shortfall)."""
        role = "Senior Engineer | Acme Corp | 2020 - Present"
        packet = build_writer_packet(
            self._plan(role, "high"), "{}", self._resume(role, 5), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 0

    def test_medium_priority_shortfall_grants_allowance_one(self):
        """Medium priority with 2 source bullets (min=3) → allowance=1."""
        role = "Engineer | Beta Corp | 2017 - 2020"
        packet = build_writer_packet(
            self._plan(role, "medium"), "{}", self._resume(role, 2), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 1

    def test_low_priority_always_zero(self):
        """Low priority roles always have allowance=0 regardless of bullet count."""
        role = "Junior Dev | Foo Corp | 2015 - 2017"
        packet = build_writer_packet(
            self._plan(role, "low"), "{}", self._resume(role, 5), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role, 0) == 0

    def test_thin_role_by_name_gets_zero_allowance(self):
        """Thin role by name pattern ('mercor') overrides shortfall to allowance=0.

        3 source bullets → would normally give shortfall=1 for high priority,
        but thin_override classification (name contains 'mercor') forces 0.
        """
        role = "AI Evaluator | Mercor | 2023 - Present"
        # 3 bullets: not thin by content (3 >= 2 count threshold, 270 chars > 150)
        # but the name pattern triggers thin_override
        packet = build_writer_packet(
            self._plan(role, "high"), "{}", self._resume(role, 3), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 0

    def test_no_evidence_gives_zero_allowance(self):
        """No allowed_claims in evidence_map → allowance=0 even with shortfall."""
        role = "Senior Engineer | Acme Corp | 2020 - Present"
        packet = build_writer_packet(
            self._plan(role, "high", with_evidence=False),
            "{}",
            self._resume(role, 3),  # shortfall=1 but no evidence
            "job desc",
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 0

    def test_large_shortfall_capped_at_one(self):
        """Shortfall of 2 is still capped at allowance=1.

        2 source bullets, high priority min=4 → shortfall=2.
        2 bullets × ~90 chars = ~180 chars > 150 threshold → not thin by content.
        allowance = min(2, 1) = 1.
        """
        role = "Senior Engineer | Acme Corp | 2020 - Present"
        packet = build_writer_packet(
            self._plan(role, "high"), "{}", self._resume(role, 2), "job desc"
        )
        assert packet["role_density_shortfall_allowance"].get(role) == 1

    def test_packet_always_includes_allowance_field(self):
        """WriterPacket always includes role_density_shortfall_allowance as a dict."""
        plan = _minimal_plan()
        packet = build_writer_packet(plan, "{}", _master_resume_with_kafka(), "job desc")
        assert "role_density_shortfall_allowance" in packet
        assert isinstance(packet["role_density_shortfall_allowance"], dict)

    # --- Effective-priority promotion tests ---

    def _plan_multi_role(self, roles: list[tuple[str, str]], with_evidence: bool = True) -> dict:
        """Plan with multiple experience entries: [(role_name, priority), ...]."""
        evidence_entry = {
            "theme": "Theme 0",
            "evidence": [
                {
                    "source": "master_resume",
                    "location": "Corp",
                    "quote": "Built scalable platform",
                    "allowed_claims": ["built scalable platform"] if with_evidence else [],
                }
            ],
            "gaps": [],
            "safe_translation": [],
        }
        return {
            "role_level": "senior",
            "jd_top_themes": [
                {"theme": f"Theme {i}", "why_important": "x", "keywords": ["distributed systems"]}
                for i in range(3)
            ],
            "evidence_map": [evidence_entry],
            "resume_strategy": {
                "summary": {"include_points": [], "avoid_points": []},
                "experience": [
                    {
                        "role_name": name,
                        "priority": priority,
                        "keep_metrics": [],
                        "bullets_to_emphasize": [],
                        "bullets_to_compress": [],
                        "bullets_to_reframe": [],
                    }
                    for name, priority in roles
                ],
                "skills": {
                    "reorder_categories": [],
                    "promote_skills": [],
                    "demote_skills": [],
                    "do_not_add_skills": [],
                },
            },
            "cover_letter_strategy": {"company_and_role_mentions": [], "bullet_overlaps_to_reference": [], "structure": []},
            "risk_checks": {"do_not_invent": [], "likely_hallucination_traps": [], "claims_requiring_strict_grounding": []},
        }

    def _resume_multi(self, role_bullets: list[tuple[str, int]]) -> str:
        """Master resume with multiple roles: [(role_header, bullet_count), ...]."""
        parts = ["Experience"]
        for header, n in role_bullets:
            parts.append(header)
            for _ in range(n):
                parts.append(f"- {self._LONG_BULLET}")
            parts.append("")
        parts.append("Technical Skills\nLanguages: Python")
        return "\n".join(parts)

    def test_top_k_promotion_raises_allowance_for_low_plan_priority(self):
        """Top-2 non-thin roles get effective high priority even if plan says low.

        Plan order: R1(low), R2(low), R3(high).
        Source bullets: R1=1, R2=1, R3=4.
        R1 and R2 are non-thin (1 bullet each BUT chars exceed 150 threshold)
        and are the first 2 non-thin roles → promoted to effective high.
        shortfall vs min=4 is 3, capped at allowance=1.
        """
        # Use long bullets so char count exceeds 150 (not thin by content)
        r1 = "Engineer | Alpha Corp | 2022 - Present"
        r2 = "Engineer | Beta Corp | 2020 - 2022"
        r3 = "Senior Engineer | Gamma Corp | 2018 - 2020"
        plan = self._plan_multi_role([(r1, "low"), (r2, "low"), (r3, "high")])
        # R1 and R2 each have 2 bullets (~180 chars) — over the thin char threshold
        resume = self._resume_multi([(r1, 2), (r2, 2), (r3, 4)])
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        allowance = packet["role_density_shortfall_allowance"]
        assert allowance.get(r1) == 1, f"R1 promoted to high → allowance=1; got {allowance}"
        assert allowance.get(r2) == 1, f"R2 promoted to high → allowance=1; got {allowance}"
        assert allowance.get(r3) == 0, f"R3 has 4 bullets (no shortfall) → allowance=0; got {allowance}"

    def test_thin_role_in_top_k_position_blocks_allowance(self):
        """Thin role occupying a top-K position does not receive allowance.

        R1 is the first role but has 1 source bullet → thin_override.
        R2 and R3 become the first 2 non-thin roles.
        """
        r1_thin = "AI Evaluator | Mercor | 2023 - Present"   # name triggers thin
        r2 = "Engineer | Beta Corp | 2020 - 2022"
        r3 = "Senior Engineer | Gamma Corp | 2018 - 2020"
        plan = self._plan_multi_role([(r1_thin, "high"), (r2, "low"), (r3, "low")])
        resume = self._resume_multi([(r1_thin, 3), (r2, 2), (r3, 2)])
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        allowance = packet["role_density_shortfall_allowance"]
        assert allowance.get(r1_thin) == 0, f"Thin role → always 0; got {allowance}"
        # R2 and R3 are first 2 non-thin → promoted to high → shortfall=2 → allowance=1
        assert allowance.get(r2) == 1, f"R2 promoted non-thin → allowance=1; got {allowance}"
        assert allowance.get(r3) == 1, f"R3 promoted non-thin → allowance=1; got {allowance}"

    def test_no_evidence_blocks_all_allowances_including_promoted(self):
        """Empty allowed_claims in evidence_map → allowance=0 for all roles."""
        r1 = "Engineer | Alpha Corp | 2022 - Present"
        r2 = "Engineer | Beta Corp | 2020 - 2022"
        plan = self._plan_multi_role([(r1, "low"), (r2, "low")], with_evidence=False)
        resume = self._resume_multi([(r1, 2), (r2, 2)])
        packet = build_writer_packet(plan, "{}", resume, "job desc")
        allowance = packet["role_density_shortfall_allowance"]
        assert allowance.get(r1) == 0, f"No evidence → allowance=0; got {allowance}"
        assert allowance.get(r2) == 0, f"No evidence → allowance=0; got {allowance}"


# ---------------------------------------------------------------------------
# _roles_match unit tests
# ---------------------------------------------------------------------------

class TestRolesMatch:
    """Tests for _roles_match — the bidirectional pipe-part role name matcher."""

    def setup_method(self):
        from tailor.phase2_validator import _roles_match
        self.match = _roles_match

    def test_exact_match(self):
        assert self.match("Senior Eng | Acme Corp", "Senior Eng | Acme Corp")

    def test_plan_is_substring_of_resume_header(self):
        """Plan without date matches resume header that includes a date."""
        assert self.match(
            "Senior Engineer | Acme Corp",
            "Senior Engineer | Acme Corp | 2020 - Present",
        )

    def test_abbreviated_plan_title_matches_full_resume_title(self):
        """Core bug fix: Phase 1 abbreviates multi-part title; pipe-part match resolves it."""
        assert self.match(
            "VP / Director of Software Development | CardinalChain Software Inc",
            "VP / Director of Software Development / Lead Software Developer | CardinalChain Software Inc",
        )

    def test_reversed_direction_also_matches(self):
        """Full name in plan, abbreviated in written resume — also resolves."""
        assert self.match(
            "VP / Director of Software Development / Lead Software Developer | CardinalChain Software Inc",
            "VP / Director of Software Development | CardinalChain Software Inc",
        )

    def test_different_company_does_not_match(self):
        assert not self.match(
            "Senior Engineer | Acme Corp",
            "Senior Engineer / Lead | Beta Corp",
        )

    def test_title_mismatch_does_not_match(self):
        assert not self.match(
            "QA Engineer | Corp",
            "Senior Engineer / Lead | Corp",
        )


# ---------------------------------------------------------------------------
# WriterPacket taxonomy fields
# ---------------------------------------------------------------------------

class TestWriterPacketTaxonomyFields:
    """WriterPacket must include all taxonomy fields (§1.1)."""

    def _packet(self):
        plan = _minimal_plan()
        return build_writer_packet(plan, "{}", _master_resume_with_kafka(), "job desc")

    def test_must_surface_arch_mechanisms_present(self):
        pkt = self._packet()
        assert "must_surface_arch_mechanisms" in pkt
        assert isinstance(pkt["must_surface_arch_mechanisms"], list)

    def test_must_surface_strategic_signals_present(self):
        pkt = self._packet()
        assert "must_surface_strategic_signals" in pkt
        assert isinstance(pkt["must_surface_strategic_signals"], list)

    def test_must_surface_operational_signals_present(self):
        pkt = self._packet()
        assert "must_surface_operational_signals" in pkt
        assert isinstance(pkt["must_surface_operational_signals"], list)

    def test_mechanism_dedup_map_present(self):
        pkt = self._packet()
        assert "mechanism_dedup_map" in pkt
        assert isinstance(pkt["mechanism_dedup_map"], dict)

    def test_role_weight_profile_present(self):
        pkt = self._packet()
        assert "role_weight_profile" in pkt
        rwp = pkt["role_weight_profile"]
        assert "arch_weight" in rwp
        assert "strategic_weight" in rwp
        assert "operational_weight" in rwp

    def test_legacy_field_absent(self):
        """must_surface_mechanisms has been removed from the WriterPacket."""
        pkt = self._packet()
        assert "must_surface_mechanisms" not in pkt

    def test_provenance_fields_present(self):
        """arch_mechanisms_primary and arch_mechanisms_backstop must always be emitted."""
        pkt = self._packet()
        assert "arch_mechanisms_primary" in pkt
        assert isinstance(pkt["arch_mechanisms_primary"], list)
        assert "arch_mechanisms_backstop" in pkt
        assert isinstance(pkt["arch_mechanisms_backstop"], list)

    def test_arch_mechanisms_contain_only_arch_phrases(self):
        """Capability statements must not leak into arch list."""
        profile = {
            "experience_highlights": [
                {
                    "architecture_patterns": [
                        "horizontal scaling of trading servers",
                        "Experienced in building distributed systems",  # should be rejected
                        "Redis caching layer",
                    ]
                }
            ],
            "scalability_reliability_patterns": [],
        }
        import json as _json
        plan = _minimal_plan()
        pkt = build_writer_packet(plan, _json.dumps(profile), "", "job desc")
        arch = pkt["must_surface_arch_mechanisms"]
        for phrase in arch:
            assert "experienced in" not in phrase.lower(), (
                f"Capability statement leaked into arch list: {phrase!r}"
            )

    def test_safe_translations_classified_into_arch(self):
        """safe_translation arch phrases end up in must_surface_arch_mechanisms."""
        plan = _minimal_plan()
        # The fixture plan has safe_translation=["high-throughput distributed platform scaling"]
        # which contains "distributed" → ARCH
        pkt = build_writer_packet(plan, "{}", "", "job desc")
        arch = pkt["must_surface_arch_mechanisms"]
        assert any("distributed" in p.lower() for p in arch), (
            f"Expected arch phrase with 'distributed' from safe_translation; got: {arch}"
        )


class TestWriterPacketWeightProfile:
    """role_weight_profile varies by role_level (§1.5)."""

    def _packet_for_level(self, role_level: str) -> dict:
        plan = _minimal_plan()
        plan["role_level"] = role_level
        return build_writer_packet(plan, "{}", "", "job desc")

    def test_director_weight_profile(self):
        pkt = self._packet_for_level("director")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.4
        assert rwp["strategic_weight"] == 0.4
        assert rwp["operational_weight"] == 0.2

    def test_senior_weight_profile(self):
        pkt = self._packet_for_level("senior")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.7
        assert rwp["strategic_weight"] == 0.2
        assert rwp["operational_weight"] == 0.1

    def test_unknown_level_uses_default(self):
        pkt = self._packet_for_level("wizard")  # truly unknown level
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.7  # default

    def test_staff_weight_profile(self):
        pkt = self._packet_for_level("staff")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.65
        assert rwp["strategic_weight"] == 0.25
        assert rwp["operational_weight"] == 0.1

    def test_manager_weight_profile(self):
        pkt = self._packet_for_level("manager")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.3
        assert rwp["strategic_weight"] == 0.4
        assert rwp["operational_weight"] == 0.3

    def test_principal_weight_profile(self):
        pkt = self._packet_for_level("principal")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.5
        assert rwp["strategic_weight"] == 0.4
        assert rwp["operational_weight"] == 0.1


# ---------------------------------------------------------------------------
# Validator: director soft signal checks
# ---------------------------------------------------------------------------

def _make_director_packet(
    strategic_signals: list[str] | None = None,
    operational_signals: list[str] | None = None,
) -> dict:
    return {
        "role_level": "director",
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": strategic_signals or [],
        "must_surface_operational_signals": operational_signals or [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {"CTO | Acme Corp": "high"},
        "role_source_bullet_counts": {"CTO | Acme Corp": 10},
        "role_source_char_counts": {"CTO | Acme Corp": 1000},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
        },
    }


def _director_resume_with_signals(strategic_count: int, operational_count: int) -> str:
    """Build a resume that contains the specified number of each signal type."""
    strategic_phrases = [
        "Led technical vision for enterprise platform",
        "Drove cross-functional alignment on roadmap",
        "Spearheaded product development strategy",
    ]
    operational_phrases = [
        "Established code review standards across teams",
        "Improved observability via structured metrics",
    ]
    bullets = []
    for phrase in strategic_phrases[:strategic_count]:
        bullets.append(f"- {phrase}")
    for phrase in operational_phrases[:operational_count]:
        bullets.append(f"- {phrase}")
    # Pad to 4 bullets to satisfy high-priority density minimum
    while len(bullets) < 4:
        bullets.append("- Managed engineering deliverables on schedule")
    bullet_text = "\n".join(bullets)
    d = date.today()
    cover = f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."
    return (
        "Experience\n"
        "CTO | Acme Corp | 2020 - Present\n"
        f"{bullet_text}\n\n"
        "Technical Skills\nPython\n"
    ), cover


class TestValidatorDirectorSoftChecks:
    """Soft director signal checks produce warnings (not errors) (§1.6)."""

    def _validate(self, pkt, strategic_count, operational_count):
        resume, cover = _director_resume_with_signals(strategic_count, operational_count)
        return validate_phase2_output(pkt, resume, cover, _today())

    def test_director_with_enough_signals_no_warnings(self):
        """Director resume with 2+ strategic and 1+ operational → no signal warnings."""
        pkt = _make_director_packet(
            strategic_signals=["Led technical vision for enterprise platform",
                                "Drove cross-functional alignment on roadmap"],
            operational_signals=["Established code review standards across teams"],
        )
        report = self._validate(pkt, strategic_count=2, operational_count=1)
        signal_warnings = [w for w in report["warnings"] if "signal" in w.lower()]
        assert not signal_warnings, f"Unexpected signal warnings: {signal_warnings}"

    def test_director_missing_strategic_signals_emits_warning(self):
        """Director resume with 0 strategic signals → warning (not error)."""
        pkt = _make_director_packet(
            strategic_signals=["Led technical vision for enterprise platform",
                                "Drove cross-functional alignment on roadmap"],
            operational_signals=["Established code review standards across teams"],
        )
        report = self._validate(pkt, strategic_count=0, operational_count=1)
        signal_warnings = [w for w in report["warnings"] if "strategic" in w.lower()]
        assert signal_warnings, f"Expected strategic signal warning; got: {report['warnings']}"
        # Must be a warning, not an error
        signal_errors = [e for e in report["errors"] if "strategic" in e.lower()]
        assert not signal_errors, f"Strategic check must be warning, not error: {report['errors']}"

    def test_director_missing_operational_signals_emits_warning(self):
        """Director resume with 0 operational signals → warning (not error)."""
        pkt = _make_director_packet(
            strategic_signals=["Led technical vision for enterprise platform",
                                "Drove cross-functional alignment on roadmap"],
            operational_signals=["Established code review standards across teams"],
        )
        report = self._validate(pkt, strategic_count=2, operational_count=0)
        signal_warnings = [w for w in report["warnings"] if "operational" in w.lower()]
        assert signal_warnings, f"Expected operational signal warning; got: {report['warnings']}"
        signal_errors = [e for e in report["errors"] if "operational" in e.lower()]
        assert not signal_errors, f"Operational check must be warning, not error: {report['errors']}"

    def test_non_director_no_signal_checks(self):
        """Senior role_level does not emit strategic/operational signal warnings."""
        pkt = _make_director_packet(
            strategic_signals=["Led technical vision"],
            operational_signals=["code review culture"],
        )
        pkt["role_level"] = "senior"
        report = self._validate(pkt, strategic_count=0, operational_count=0)
        signal_warnings = [w for w in report["warnings"] if "signal" in w.lower()]
        assert not signal_warnings, f"Senior role should not have signal warnings: {signal_warnings}"

    def test_stats_include_signal_counts(self):
        """stats dict includes strategic_signal_count and operational_signal_count."""
        pkt = _make_director_packet(
            strategic_signals=["Led technical vision for enterprise platform"],
            operational_signals=["Established code review standards across teams"],
        )
        report = self._validate(pkt, strategic_count=1, operational_count=1)
        assert "strategic_signal_count" in report["stats"]
        assert "operational_signal_count" in report["stats"]


# ---------------------------------------------------------------------------
# Validator: mechanism density uses arch list
# ---------------------------------------------------------------------------

class TestValidatorArchMechanismDensity:
    """Mechanism density counts use must_surface_arch_mechanisms (§1.6)."""

    def _make_packet(self, arch_mechanisms: list[str]) -> dict:
        return {
            "role_level": "senior",
            "must_keep_metrics": [],
            "must_surface_arch_mechanisms": arch_mechanisms,
            "must_surface_strategic_signals": [],
            "must_surface_operational_signals": [],
            "must_include_skills": [],
            "allowed_skill_pool": [],
            "do_not_add_terms": [],
            "unsafe_jd_nouns": [],
            "role_priorities": {"Senior Engineer | Acme Corp": "high"},
            "role_source_bullet_counts": {"Senior Engineer | Acme Corp": 10},
            "role_source_char_counts": {"Senior Engineer | Acme Corp": 1000},
            "jd_is_delivery_oriented": False,
            "density_targets": {
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
            },
        }

    def _cover(self) -> str:
        d = date.today()
        return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."

    def test_arch_phrase_in_bullet_counted_as_mechanism(self):
        """A bullet containing an arch phrase from the list counts toward mechanism density."""
        arch = ["database read replicas"]
        pkt = self._make_packet(arch)
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Improved read performance via database read replicas\n"
            "- Scaled backend services via horizontal scaling\n"
            "- Delivered new feature ahead of schedule\n"
            "- Mentored junior engineers\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        mech_errors = [e for e in report["errors"] if "mechanism" in e.lower()]
        assert not mech_errors, f"Bullet with arch phrase should count: {report['errors']}"

    def test_non_arch_phrase_not_sufficient_when_arch_list_provided(self):
        """A bullet with a generic keyword but not in arch list still counts via fallback."""
        arch = ["specific custom mechanism phrase"]
        pkt = self._make_packet(arch)
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Improved throughput via Redis caching\n"   # Redis in _MECHANISM_KEYWORDS fallback
            "- Scaled services via Kafka messaging\n"
            "- Delivered features on time\n"
            "- Led code reviews\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        # Fallback keyword check should still find Redis/Kafka as mechanisms
        mech_errors = [e for e in report["errors"] if "mechanism" in e.lower()]
        assert not mech_errors, f"Fallback keyword check should pass: {report['errors']}"


# ---------------------------------------------------------------------------
# WriterPacket provenance split (G1 / G3 / G4)
# ---------------------------------------------------------------------------

class TestWriterPacketProvenance:
    """Tests for arch_mechanisms_primary / arch_mechanisms_backstop split."""

    def _profile_with_patterns(self, patterns: list[str]) -> str:
        return json.dumps({
            "experience_highlights": [{"architecture_patterns": patterns}],
            "scalability_reliability_patterns": [],
        })

    def _plan_with_safe_translation(self, translations: list[str]) -> dict:
        plan = _minimal_plan()
        plan["evidence_map"][0]["safe_translation"] = translations
        return plan

    def test_legacy_field_absent(self):
        """must_surface_mechanisms must NOT appear in the emitted WriterPacket."""
        pkt = build_writer_packet(_minimal_plan(), _candidate_profile_str(), "", "jd")
        assert "must_surface_mechanisms" not in pkt

    def test_provenance_fields_present(self):  # G1
        """arch_mechanisms_primary and arch_mechanisms_backstop always emitted."""
        pkt = build_writer_packet(_minimal_plan(), _candidate_profile_str(), "", "jd")
        assert "arch_mechanisms_primary" in pkt
        assert isinstance(pkt["arch_mechanisms_primary"], list)
        assert "arch_mechanisms_backstop" in pkt
        assert isinstance(pkt["arch_mechanisms_backstop"], list)

    def test_profile_phrases_land_in_primary(self):  # G1
        """Profile architecture_patterns are in arch_mechanisms_primary."""
        profile = self._profile_with_patterns(["horizontal scaling of servers"])
        pkt = build_writer_packet(_minimal_plan(), profile, "", "jd")
        assert any("horizontal scaling" in p.lower() for p in pkt["arch_mechanisms_primary"])

    def test_safe_translation_arch_in_backstop(self):  # G1
        """Arch-quality safe_translation phrases land in arch_mechanisms_backstop."""
        plan = self._plan_with_safe_translation(["Redis caching layer for session data"])
        pkt = build_writer_packet(plan, "{}", "", "jd")
        assert any("redis" in p.lower() for p in pkt["arch_mechanisms_backstop"])

    def test_safe_translation_capability_not_in_backstop(self):  # G3
        """Capability statements from safe_translation are NOT in backstop arch list."""
        plan = self._plan_with_safe_translation(
            ["Strong cloud infrastructure experience with deep expertise"]
        )
        pkt = build_writer_packet(plan, "{}", "", "jd")
        backstop_text = " ".join(pkt["arch_mechanisms_backstop"]).lower()
        assert "cloud infrastructure experience" not in backstop_text

    def test_backstop_not_duplicates_primary(self):  # G1 — dedup across sources
        """A safe_translation phrase that duplicates a profile phrase is not in backstop."""
        profile = self._profile_with_patterns(["horizontal scaling of servers"])
        plan = self._plan_with_safe_translation(["horizontal scaling of servers"])
        pkt = build_writer_packet(plan, profile, "", "jd")
        # Should be in primary only, not backstop
        assert any("horizontal scaling" in p.lower() for p in pkt["arch_mechanisms_primary"])
        assert not any("horizontal scaling" in p.lower() for p in pkt["arch_mechanisms_backstop"])

    def test_substring_dedup_prefers_longer_phrase(self):  # G4
        """Shorter phrase subsumed by a longer one is dropped from the arch list."""
        profile = self._profile_with_patterns([
            "database read replicas",
            "Database read replicas for read-heavy GET endpoints",
        ])
        pkt = build_writer_packet(_minimal_plan(), profile, "", "jd")
        arch = pkt["must_surface_arch_mechanisms"]
        short_present = any(p.strip().lower() == "database read replicas" for p in arch)
        long_present = any("read-heavy" in p.lower() for p in arch)
        assert long_present, "The longer, more specific phrase must survive"
        assert not short_present, "The shorter redundant phrase must be dropped"


# ---------------------------------------------------------------------------
# A1: role.txt loading for expanded role-level enum
# ---------------------------------------------------------------------------

class TestRoleTxtLoading:
    """role.txt must load without error and mention every valid role level."""

    def test_role_txt_loads_for_all_levels(self):
        """_load_prompt_optional('role', ROLE_LEVEL=level) returns non-empty text
        for every valid role level, including the three new ones."""
        from tailor.prompts import _load_prompt_optional
        for level in ("director", "manager", "principal", "staff", "senior", "mid", "junior"):
            text = _load_prompt_optional("role", ROLE_LEVEL=level)
            assert text, f"role.txt returned empty for level={level!r}"
            assert level in text.lower(), (
                f"role.txt does not mention level {level!r}. "
                f"Ensure role.txt has guidance for all seven levels."
            )


# ---------------------------------------------------------------------------
# A2: Role presence validation (missing-role detection)
# ---------------------------------------------------------------------------

def _make_role_presence_packet(
    master_role_names: list[str] | None = None,
    role_priorities: dict | None = None,
) -> dict:
    """Minimal writer_packet for role-presence tests."""
    names = master_role_names or []
    return {
        "role_level": "senior",
        "master_resume_role_names": names,
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": role_priorities or {},
        "role_source_bullet_counts": {},
        "role_source_char_counts": {},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


class TestRolePresenceValidation:
    """Validator detects roles missing from the output (A2)."""

    def _cover(self) -> str:
        d = date.today()
        return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."

    def test_missing_old_role_produces_error(self):
        """A master-resume role absent from the output triggers MISSING_ROLE error."""
        pkt = _make_role_presence_packet(
            master_role_names=["Developer | OldCo | 1999 - 2003"],
            role_priorities={"Senior Engineer | Acme Corp": "high"},
        )
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Built services using horizontal scaling\n"
            "- Implemented read replicas\n"
            "- Improved caching with Redis\n"
            "- Mentored junior engineers\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        role_errors = [e for e in report["errors"] if "MISSING_ROLE" in e]
        assert role_errors, f"Expected MISSING_ROLE error but got: {report['errors']}"
        assert "OldCo" in role_errors[0] or "Developer" in role_errors[0]

    def test_role_in_earlier_roles_block_passes(self):
        """A role collapsed into an 'Earlier roles' block satisfies the presence check."""
        pkt = _make_role_presence_packet(
            master_role_names=["Developer | OldCo | 1999 - 2003"],
            role_priorities={"Senior Engineer | Acme Corp": "high"},
        )
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Built services using horizontal scaling\n"
            "- Implemented read replicas\n"
            "- Improved caching with Redis\n"
            "- Mentored junior engineers\n\n"
            "Earlier roles\n"
            "OldCo | Developer | 1999 - 2003\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        role_errors = [e for e in report["errors"] if "MISSING_ROLE" in e]
        assert not role_errors, (
            f"Role in 'Earlier roles' block should satisfy presence check: {report['errors']}"
        )

    def test_all_roles_present_as_full_headers_passes(self):
        """When all master roles appear as full headers, no MISSING_ROLE error is raised."""
        pkt = _make_role_presence_packet(
            master_role_names=[
                "Senior Engineer | Acme Corp",
                "Engineer | Beta Corp",
            ],
            role_priorities={
                "Senior Engineer | Acme Corp": "high",
                "Engineer | Beta Corp": "medium",
            },
        )
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Built services using horizontal scaling\n"
            "- Implemented read replicas\n"
            "- Improved caching with Redis\n"
            "- Mentored junior engineers\n\n"
            "Engineer | Beta Corp | 2017 - 2020\n"
            "- Developed backend APIs\n"
            "- Maintained CI/CD pipelines\n"
            "- Delivered feature work\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        role_errors = [e for e in report["errors"] if "MISSING_ROLE" in e]
        assert not role_errors, f"All roles present — no MISSING_ROLE expected: {report['errors']}"

    def test_missing_role_in_repair_brief_global_issues(self):
        """Missing roles appear in repair_brief.global_issues.missing_roles."""
        pkt = _make_role_presence_packet(
            master_role_names=["Old Dev | GoneCo | 2000 - 2004"],
        )
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Built services\n"
            "- Improved caching\n"
            "- Led deployments\n"
            "- Mentored team\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        missing = report["repair_brief"]["global_issues"]["missing_roles"]
        assert missing, "missing_roles in repair_brief should be non-empty"
        assert any("GoneCo" in r or "Old Dev" in r for r in missing)

    def test_no_master_roles_skips_check(self):
        """When master_resume_role_names is empty, no MISSING_ROLE errors are raised."""
        pkt = _make_role_presence_packet(master_role_names=[])
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2020 - Present\n"
            "- Built services\n"
            "- Improved caching\n"
            "- Led deployments\n"
            "- Mentored team\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        role_errors = [e for e in report["errors"] if "MISSING_ROLE" in e]
        assert not role_errors


# ---------------------------------------------------------------------------
# A3 Track 1: Mechanism placement (first 2 bullets)
# ---------------------------------------------------------------------------

def _make_mechanism_placement_packet(arch_mechanisms: list[str]) -> dict:
    """Minimal packet for mechanism placement tests; high-priority role with mechanisms."""
    return {
        "role_level": "senior",
        "master_resume_role_names": [],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": arch_mechanisms,
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {"Senior Engineer | Acme Corp": "high"},
        "role_source_bullet_counts": {"Senior Engineer | Acme Corp": 10},
        "role_source_char_counts": {"Senior Engineer | Acme Corp": 1000},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 1, "medium": 0, "low": 0},
        },
    }


class TestMechanismPlacementCheck:
    """At least 1 mechanism must appear in the first 2 bullets of high-priority roles (A3T1)."""

    def _cover(self) -> str:
        d = date.today()
        return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."

    def test_mechanism_only_in_third_bullet_emits_placement_warning(self):
        """Mechanism present but only in bullet 3 → placement warning emitted."""
        pkt = _make_mechanism_placement_packet(["database read replicas"])
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Led backend feature delivery\n"
            "- Improved system reliability\n"
            "- Scaled reads via database read replicas\n"
            "- Mentored junior engineers\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        placement_warnings = [w for w in report["warnings"] if "first 2 bullets" in w]
        assert placement_warnings, (
            f"Expected placement warning when mechanism is only in bullet 3, "
            f"got warnings: {report['warnings']}"
        )

    def test_mechanism_in_first_bullet_no_placement_warning(self):
        """Mechanism in the very first bullet → no placement warning."""
        pkt = _make_mechanism_placement_packet(["database read replicas"])
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Scaled reads via database read replicas for heavy GET traffic\n"
            "- Improved system reliability\n"
            "- Led backend feature delivery\n"
            "- Mentored junior engineers\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        placement_warnings = [w for w in report["warnings"] if "first 2 bullets" in w]
        assert not placement_warnings, (
            f"No placement warning expected when mechanism is in first bullet: {report['warnings']}"
        )

    def test_mechanism_in_second_bullet_no_placement_warning(self):
        """Mechanism in bullet 2 → no placement warning."""
        pkt = _make_mechanism_placement_packet(["database read replicas"])
        resume = (
            "Experience\n"
            "Senior Engineer | Acme Corp | 2022 - Present\n"
            "- Led backend feature delivery\n"
            "- Scaled reads via database read replicas for heavy GET traffic\n"
            "- Improved system reliability\n"
            "- Mentored junior engineers\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        placement_warnings = [w for w in report["warnings"] if "first 2 bullets" in w]
        assert not placement_warnings, (
            f"No placement warning expected when mechanism is in bullet 2: {report['warnings']}"
        )


# ---------------------------------------------------------------------------
# A3 Track 2: Manager-level leadership/delivery density
# ---------------------------------------------------------------------------

def _make_manager_packet(jd_is_delivery_oriented: bool = False) -> dict:
    """Minimal writer_packet for manager-level density tests."""
    return {
        "role_level": "manager",
        "master_resume_role_names": [],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {"Engineering Manager | Acme Corp": "high"},
        "role_source_bullet_counts": {"Engineering Manager | Acme Corp": 10},
        "role_source_char_counts": {"Engineering Manager | Acme Corp": 1000},
        "jd_is_delivery_oriented": jd_is_delivery_oriented,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


class TestManagerDensityChecks:
    """Manager-level leadership/delivery density warnings (A3T2)."""

    def _cover(self) -> str:
        d = date.today()
        return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."

    def _resume_with_bullets(self, bullets: list[str]) -> str:
        bullet_text = "\n".join(f"- {b}" for b in bullets)
        return (
            "Experience\n"
            "Engineering Manager | Acme Corp | 2020 - Present\n"
            f"{bullet_text}\n\n"
            "Technical Skills\nPython\n"
        )

    def test_manager_no_leadership_bullets_emits_warning(self):
        """Manager role with zero leadership-verb bullets in high-priority role → warning."""
        pkt = _make_manager_packet()
        resume = self._resume_with_bullets([
            "Delivered product features on schedule",
            "Improved system reliability through automated testing",
            "Partnered with product on quarterly roadmap",  # 'Partnered' counts
            "Collaborated across teams to align deliverables",
        ])
        # Remove 'partnered' to guarantee 0 leadership verbs
        resume = self._resume_with_bullets([
            "Delivered product features on schedule",
            "Improved system reliability",
            "Collaborated across teams",
            "Worked with stakeholders on priorities",
        ])
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        leadership_warnings = [w for w in report["warnings"] if "leadership-verb" in w.lower()]
        assert leadership_warnings, (
            f"Expected leadership-verb warning for manager with 0 leadership bullets, "
            f"got: {report['warnings']}"
        )

    def test_manager_two_leadership_bullets_no_warning(self):
        """Manager role with ≥2 leadership-verb bullets → no leadership warning."""
        pkt = _make_manager_packet()
        resume = self._resume_with_bullets([
            "Led a team of 8 engineers delivering platform features",
            "Managed cross-functional delivery for two product lines",
            "Collaborated with product to define quarterly goals",
            "Improved on-call reliability through runbook improvements",
        ])
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        leadership_warnings = [w for w in report["warnings"] if "leadership-verb" in w.lower()]
        assert not leadership_warnings, (
            f"No leadership warning expected with 2 leadership bullets: {report['warnings']}"
        )

    def test_manager_delivery_vocab_warning_when_jd_delivery_oriented(self):
        """Manager + delivery JD + no delivery vocab in resume → delivery warning."""
        pkt = _make_manager_packet(jd_is_delivery_oriented=True)
        resume = self._resume_with_bullets([
            "Led a team of engineers delivering platform features",
            "Managed cross-functional collaboration across teams",
            "Mentored junior engineers on best practices",
            "Guided code review culture and quality standards",
        ])
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        delivery_warnings = [
            w for w in report["warnings"] if "delivery vocabulary" in w.lower()
        ]
        assert delivery_warnings, (
            f"Expected delivery vocabulary warning for manager with delivery JD, "
            f"got: {report['warnings']}"
        )

    def test_manager_delivery_vocab_present_no_warning(self):
        """Manager + delivery JD + delivery vocab in resume → no delivery warning."""
        pkt = _make_manager_packet(jd_is_delivery_oriented=True)
        resume = self._resume_with_bullets([
            "Led team delivery against roadmap commitments",
            "Managed backlog refinement and sprint planning",
            "Mentored engineers on testing best practices",
            "Guided risk assessment for quarterly releases",
        ])
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        delivery_warnings = [
            w for w in report["warnings"] if "delivery vocabulary" in w.lower()
        ]
        assert not delivery_warnings, (
            f"No delivery warning expected when vocab present: {report['warnings']}"
        )

    def test_non_manager_no_leadership_checks(self):
        """Senior role_level does not emit manager leadership/delivery warnings."""
        pkt = _make_manager_packet()
        pkt["role_level"] = "senior"
        resume = self._resume_with_bullets([
            "Delivered product features on schedule",
            "Improved system reliability",
            "Collaborated across teams",
            "Worked with stakeholders on priorities",
        ])
        report = validate_phase2_output(pkt, resume, self._cover(), _today())
        manager_warnings = [
            w for w in report["warnings"]
            if "leadership-verb" in w.lower() or "delivery vocabulary" in w.lower()
        ]
        assert not manager_warnings, (
            f"Senior role should not emit manager-specific warnings: {manager_warnings}"
        )


# ---------------------------------------------------------------------------
# Check 9: JD vocabulary anchoring (spec 2.3)
# ---------------------------------------------------------------------------

def _make_vocab_packet(must_embed: list[str], optional_embed: list[str] | None = None) -> dict:
    """Minimal writer_packet with vocab anchoring fields for validator tests."""
    return {
        "role_level": "senior",
        "jd_vocab_must_embed": must_embed,
        "jd_vocab_optional_embed": optional_embed or [],
        "master_resume_role_names": [],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {"Senior Engineer | Acme Corp": "high"},
        "role_source_bullet_counts": {"Senior Engineer | Acme Corp": 10},
        "role_source_char_counts": {"Senior Engineer | Acme Corp": 1000},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


def _vocab_resume(extra_lines: str = "") -> str:
    d = date.today()
    return (
        f"Professional Summary\n"
        f"Experienced engineer.\n\n"
        f"Experience\n"
        f"Senior Engineer | Acme Corp | 2022 - Present\n"
        f"- Built distributed systems for high-throughput workloads.\n"
        f"- Improved scalability of the data pipeline significantly.\n"
        f"- Mentored engineers on code quality best practices.\n"
        f"- Delivered three major features ahead of schedule.\n"
        f"{extra_lines}\n\n"
        f"Technical Skills\nPython, Java\n"
    )


def _vocab_cover() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nI am a fit."


class TestJDVocabValidator:
    """Check 9: JD vocabulary anchor presence in resume text."""

    def test_two_anchors_present_passes(self):
        """When ≥2 must_embed anchors appear in resume, no JD_VOCAB_MISSING error."""
        pkt = _make_vocab_packet(["distributed systems", "scalability"])
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert not vocab_errors, f"Expected no vocab error, got: {vocab_errors}"
        assert report["stats"]["vocab_anchors_found"] == 2

    def test_one_anchor_present_fails(self):
        """When only 1 must_embed anchor appears in resume, JD_VOCAB_MISSING error emitted."""
        pkt = _make_vocab_packet(["distributed systems", "kubernetes"])
        resume = _vocab_resume()  # contains "distributed systems" but not "kubernetes"
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert vocab_errors, f"Expected JD_VOCAB_MISSING error, got errors: {report['errors']}"
        assert "1/2" in vocab_errors[0]
        assert report["stats"]["vocab_anchors_found"] == 1

    def test_zero_anchors_present_fails(self):
        """When no must_embed anchors appear in resume, JD_VOCAB_MISSING error emitted."""
        pkt = _make_vocab_packet(["kubernetes", "terraform"])
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert vocab_errors, f"Expected JD_VOCAB_MISSING error, got: {report['errors']}"
        assert "0/2" in vocab_errors[0]
        assert report["stats"]["vocab_anchors_found"] == 0

    def test_empty_must_embed_no_check(self):
        """When jd_vocab_must_embed is empty, no vocabulary check is performed."""
        pkt = _make_vocab_packet([])
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert not vocab_errors, f"No vocab check expected for empty must_embed"
        assert report["stats"]["vocab_anchors_found"] == 0

    def test_missing_packet_field_no_check(self):
        """When jd_vocab_must_embed is absent from packet, no vocab check is performed."""
        pkt = _make_vocab_packet(["distributed systems", "scalability"])
        del pkt["jd_vocab_must_embed"]
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert not vocab_errors

    def test_anchor_matching_case_insensitive(self):
        """Anchor matching is case-insensitive."""
        pkt = _make_vocab_packet(["Distributed Systems", "SCALABILITY"])
        resume = _vocab_resume()  # has "distributed systems" and "scalability" in lowercase
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        vocab_errors = [e for e in report["errors"] if "JD_VOCAB_MISSING" in e]
        assert not vocab_errors, f"Case-insensitive match should pass, got: {vocab_errors}"

    def test_repair_brief_includes_missing_vocab_anchors(self):
        """repair_brief.global_issues.missing_vocab_anchors lists unembedded anchors."""
        pkt = _make_vocab_packet(["kubernetes", "terraform"])
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        missing = report["repair_brief"]["global_issues"].get("missing_vocab_anchors", [])
        assert "kubernetes" in missing
        assert "terraform" in missing

    def test_repair_brief_empty_when_anchors_present(self):
        """When anchors are satisfied, missing_vocab_anchors is empty."""
        pkt = _make_vocab_packet(["distributed systems", "scalability"])
        resume = _vocab_resume()
        report = validate_phase2_output(pkt, resume, _vocab_cover(), _today())
        missing = report["repair_brief"]["global_issues"].get("missing_vocab_anchors", [])
        assert missing == []


# ---------------------------------------------------------------------------
# Check 11: Employer integrity (no invented roles, no modified company names)
# ---------------------------------------------------------------------------

_MASTER_ROLES = [
    "Engineering Manager | CardinalChain Software",
    "Senior Engineer | Acme Corp",
    "Developer | OldCo | 2010 - 2014",
]

_MASTER_DATES = {
    "Engineering Manager | CardinalChain Software": "Nov 2024 – Sep 2025",
    "Senior Engineer | Acme Corp": "Jan 2022 – Oct 2024",
}


def _make_employer_integrity_packet(
    master_role_names: list[str] | None = None,
    master_role_dates: dict | None = None,
    role_priorities: dict | None = None,
) -> dict:
    """Minimal writer_packet for employer integrity validator tests."""
    names = master_role_names if master_role_names is not None else list(_MASTER_ROLES)
    return {
        "role_level": "manager",
        "master_resume_role_names": names,
        "master_role_dates": master_role_dates if master_role_dates is not None else {},
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": role_priorities or {
            "Engineering Manager | CardinalChain Software": "high",
            "Senior Engineer | Acme Corp": "medium",
        },
        "role_source_bullet_counts": {
            "Engineering Manager | CardinalChain Software": 10,
            "Senior Engineer | Acme Corp": 8,
        },
        "role_source_char_counts": {
            "Engineering Manager | CardinalChain Software": 900,
            "Senior Engineer | Acme Corp": 700,
        },
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


def _ei_cover() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nSolid fit."


def _ei_resume(
    roles_block: str,
    earlier_roles_block: str = "",
) -> str:
    """Build a resume string with configurable role headers and an optional Earlier roles block."""
    earlier = f"\nEarlier roles\n{earlier_roles_block}\n" if earlier_roles_block else ""
    return (
        "Experience\n"
        f"{roles_block}"
        f"{earlier}"
        "Technical Skills\nPython\n"
    )


class TestEmployerIntegrityValidator:
    """Check 11: No invented roles; no modified company names."""

    # ------------------------------------------------------------------
    # Pass cases
    # ------------------------------------------------------------------

    def test_exact_master_roles_preserved_passes(self):
        """Exact master role headers in output → no EMPLOYER_INTEGRITY error."""
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Engineering Manager | CardinalChain Software | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of eight engineers.\n"
            "- Delivered two product launches.\n"
            "- Reduced incident rate by 30%.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability of data pipelines.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY" in e]
        assert not ei_errors, f"Exact headers should pass, got: {ei_errors}"

    def test_abbreviated_title_same_company_passes(self):
        """Title substring of master title with exact company → passes.

        The plan may emit a shortened title (e.g. drop a secondary slash-component)
        while keeping the company name verbatim.  Substring matching on the title
        segment accommodates this.  'Manager' IS a substring of 'Engineering Manager'.
        """
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Manager | CardinalChain Software | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of eight engineers.\n"
            "- Delivered two product launches.\n"
            "- Reduced incident rate by 30%.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability of data pipelines.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY" in e]
        assert not ei_errors, f"Substring title with exact company should pass: {ei_errors}"

    def test_pipe_spacing_variation_passes(self):
        """Pipe spacing variations (no spaces around pipe) are normalised and pass."""
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Engineering Manager|CardinalChain Software|Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of eight engineers.\n"
            "- Delivered two product launches.\n"
            "- Reduced incident rate by 30%.\n\n"
            "Senior Engineer|Acme Corp|Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability of data pipelines.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY" in e]
        assert not ei_errors, f"Pipe-spacing variation should pass: {ei_errors}"

    def test_no_master_roles_skips_check(self):
        """When master_resume_role_names is empty, employer integrity check is skipped."""
        pkt = _make_employer_integrity_packet(master_role_names=[])
        roles_block = (
            "Engineering Manager | Brex | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning.\n"
            "- Managed the team.\n"
            "- Delivered features.\n"
            "- Reduced incidents.\n\n"
        )
        resume = _ei_resume(roles_block)
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY" in e]
        assert not ei_errors, "Empty master_role_names must skip the check"

    # ------------------------------------------------------------------
    # Fail cases
    # ------------------------------------------------------------------

    def test_invented_role_triggers_error(self):
        """Output role not in master → EMPLOYER_INTEGRITY_NEW_ROLE error."""
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Engineering Manager | Brex | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY_NEW_ROLE" in e]
        assert ei_errors, f"Invented company 'Brex' should trigger EMPLOYER_INTEGRITY_NEW_ROLE"
        assert "Brex" in ei_errors[0]

    def test_modified_company_name_triggers_error(self):
        """Mutated company name (extra word) → EMPLOYER_INTEGRITY_NEW_ROLE error."""
        pkt = _make_employer_integrity_packet()
        # "CardinalChain Software Inc" ≠ "CardinalChain Software"
        roles_block = (
            "Engineering Manager | CardinalChain Software Inc | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        ei_errors = [e for e in report["errors"] if "EMPLOYER_INTEGRITY_NEW_ROLE" in e]
        assert ei_errors, "Modified company name should trigger EMPLOYER_INTEGRITY_NEW_ROLE"
        assert "CardinalChain Software Inc" in ei_errors[0]

    def test_employer_integrity_violations_in_repair_brief(self):
        """Violated headers appear in repair_brief.global_issues.employer_integrity_violations."""
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Engineering Manager | Brex | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        violations = report["repair_brief"]["global_issues"].get("employer_integrity_violations", [])
        assert violations, "repair_brief must list employer integrity violations"
        assert any("Brex" in v for v in violations)

    def test_employer_integrity_violations_in_stats(self):
        """stats['employer_integrity_violations'] lists violated headers."""
        pkt = _make_employer_integrity_packet()
        roles_block = (
            "Engineering Manager | Brex | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        stats_violations = report["stats"].get("employer_integrity_violations", [])
        assert any("Brex" in v for v in stats_violations)

    # ------------------------------------------------------------------
    # Date integrity
    # ------------------------------------------------------------------

    def test_matching_dates_passes(self):
        """Dates identical to master (after normalization) → no DATE_INTEGRITY error."""
        pkt = _make_employer_integrity_packet(master_role_dates=dict(_MASTER_DATES))
        roles_block = (
            "Engineering Manager | CardinalChain Software | Nov 2024 – Sep 2025\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        date_errors = [e for e in report["errors"] if "DATE_INTEGRITY" in e]
        assert not date_errors, f"Matching dates should pass, got: {date_errors}"

    def test_modified_end_date_triggers_error(self):
        """Changing 'Sep 2025' to 'Present' triggers DATE_INTEGRITY_MODIFIED error."""
        pkt = _make_employer_integrity_packet(master_role_dates=dict(_MASTER_DATES))
        # master: "Nov 2024 – Sep 2025" — output: "Nov 2024 – Present"
        roles_block = (
            "Engineering Manager | CardinalChain Software | Nov 2024 – Present\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        date_errors = [e for e in report["errors"] if "DATE_INTEGRITY_MODIFIED" in e]
        assert date_errors, (
            "Changed end date from 'Sep 2025' to 'Present' should trigger DATE_INTEGRITY_MODIFIED"
        )
        assert "CardinalChain Software" in date_errors[0] or "Nov 2024" in date_errors[0]

    def test_no_master_dates_skips_date_check(self):
        """When master_role_dates is empty, date integrity check is skipped."""
        pkt = _make_employer_integrity_packet(master_role_dates={})
        roles_block = (
            "Engineering Manager | CardinalChain Software | Nov 2024 – Present\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        date_errors = [e for e in report["errors"] if "DATE_INTEGRITY_MODIFIED" in e]
        assert not date_errors, "Empty master_role_dates must skip date integrity check"

    def test_date_integrity_violations_in_repair_brief(self):
        """Date violations appear in repair_brief.global_issues.date_integrity_violations."""
        pkt = _make_employer_integrity_packet(master_role_dates=dict(_MASTER_DATES))
        roles_block = (
            "Engineering Manager | CardinalChain Software | Nov 2024 – Present\n"
            "- Led roadmap planning and delivery.\n"
            "- Managed a team of engineers.\n"
            "- Delivered product launches.\n"
            "- Reduced incident rate.\n\n"
            "Senior Engineer | Acme Corp | Jan 2022 – Oct 2024\n"
            "- Architected distributed systems.\n"
            "- Improved scalability.\n"
            "- Mentored engineers.\n\n"
        )
        resume = _ei_resume(roles_block, earlier_roles_block="OldCo | Developer | 2010 - 2014")
        report = validate_phase2_output(pkt, resume, _ei_cover(), _today())
        date_violations = report["repair_brief"]["global_issues"].get("date_integrity_violations", [])
        assert date_violations, "repair_brief must list date integrity violations"

    def test_writer_packet_includes_master_role_dates(self):
        """build_writer_packet propagates master_role_dates into the WriterPacket."""
        master_resume = (
            "Experience\n"
            "Engineering Manager | CardinalChain Software\n"
            "Nov 2024 – Sep 2025\n"
            "- Led the team.\n\n"
            "Senior Engineer | Acme Corp\n"
            "Jan 2022 – Oct 2024\n"
            "- Built services.\n\n"
            "Technical Skills\nPython\n"
        )
        plan = _minimal_plan()
        plan["role_level"] = "manager"
        pkt = build_writer_packet(plan, "{}", master_resume, "job description")
        dates = pkt.get("master_role_dates", {})
        assert dates, "master_role_dates must be populated from the master resume"
        # At least one role header should map to a date string
        assert any("2024" in v or "2022" in v for v in dates.values()), (
            f"Expected date strings in master_role_dates values, got: {dates}"
        )


# ---------------------------------------------------------------------------
# Check 12/13/14: Narrative validators
# ---------------------------------------------------------------------------

_NARRATIVE_ANCHOR_ROLE = "Engineering Manager | Acme Corp"

_SAMPLE_NARRATIVE_PLAN = {
    "anchor_role_id": _NARRATIVE_ANCHOR_ROLE,
    "theme_ranked": [
        {
            "theme_id": "T1",
            "label": "Platform Reliability",
            "priority": "primary",
            "signature_terms": ["reliability", "uptime", "incident", "sla"],
        },
        {
            "theme_id": "T2",
            "label": "Team Leadership",
            "priority": "secondary",
            "signature_terms": ["managed", "team", "mentored", "hiring"],
        },
        {
            "theme_id": "T3",
            "label": "Product Delivery",
            "priority": "supporting",
            "signature_terms": ["roadmap", "delivery", "shipped", "launched"],
        },
    ],
    "summary_coverage": {
        "must_cover_theme_ids": ["T1", "T2"],
        "should_cover_theme_ids": ["T3"],
    },
    "anchor_role_coverage": {
        "first_k_bullets": 3,
        "top_k_themes_to_cover": 2,
        "min_theme_occurrences": {"T1": 2, "T2": 1},
    },
    "domain_translation_binding": {
        "min_total_rule_instantiations": 0,
        "min_instantiations_in_anchor_role": 0,
        "require_target_frame_in_anchor_role_first_k": False,
    },
}


def _make_narrative_packet(
    narrative_plan: dict | None = None,
    domain_mismatch: bool = False,
) -> dict:
    """Minimal writer_packet for narrative validator tests."""
    plan = narrative_plan if narrative_plan is not None else _SAMPLE_NARRATIVE_PLAN
    return {
        "role_level": "manager",
        "narrative_plan": plan,
        "domain_mismatch": domain_mismatch,
        "master_resume_role_names": [_NARRATIVE_ANCHOR_ROLE],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {_NARRATIVE_ANCHOR_ROLE: "high"},
        "role_source_bullet_counts": {_NARRATIVE_ANCHOR_ROLE: 8},
        "role_source_char_counts": {_NARRATIVE_ANCHOR_ROLE: 700},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


def _narrative_cover() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}\n\nDear Hiring Manager,\n\nExcited to apply."


def _narrative_resume(
    summary_lines: str,
    anchor_bullets: str,
    extra_roles: str = "",
) -> str:
    """Build a resume with a configurable summary and anchor role bullets."""
    return (
        "Professional Summary\n"
        f"{summary_lines}\n\n"
        "Experience\n"
        f"{_NARRATIVE_ANCHOR_ROLE} | 2022 - Present\n"
        f"{anchor_bullets}\n\n"
        f"{extra_roles}"
        "Technical Skills\nPython\n"
    )


class TestNarrativeValidators:
    """Checks 12/13/14: SummaryThemeValidator, AnchorRoleDominanceValidator,
    DomainTranslationAnchorValidator."""

    # ------------------------------------------------------------------
    # Check 12: SummaryThemeValidator
    # ------------------------------------------------------------------

    def test_summary_missing_theme_fails(self):
        """Summary lacks T1 signature terms → NARRATIVE_SUMMARY_THEME_MISSING for T1."""
        pkt = _make_narrative_packet()
        # Summary only mentions T2 terms (team, managed), no T1 (reliability/uptime/incident/sla)
        resume = _narrative_resume(
            summary_lines="Experienced manager leading teams and managing hiring processes.",
            anchor_bullets=(
                "- Improved platform reliability through better monitoring.\n"
                "- Drove uptime improvements via incident response protocols.\n"
                "- Managed team of engineers and supported hiring."
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errors = [e for e in report["errors"] if "NARRATIVE_SUMMARY_THEME_MISSING" in e]
        assert errors, f"Expected NARRATIVE_SUMMARY_THEME_MISSING, got: {report['errors']}"
        assert "T1" in errors[0], f"Expected T1 missing, got: {errors[0]}"

    def test_summary_all_themes_covered_passes(self):
        """Summary covers both T1 and T2 signature terms → no summary error."""
        pkt = _make_narrative_packet()
        # Summary mentions T1 (reliability, incident) and T2 (team, managed)
        resume = _narrative_resume(
            summary_lines=(
                "Engineering leader improving platform reliability and incident response, "
                "managing high-performance teams."
            ),
            anchor_bullets=(
                "- Drove reliability improvements across the platform.\n"
                "- Established incident response protocols to improve uptime.\n"
                "- Managed team and led hiring for three open positions."
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        summary_errors = [e for e in report["errors"] if "NARRATIVE_SUMMARY_THEME_MISSING" in e]
        assert not summary_errors, f"Summary covers both themes — no error expected: {summary_errors}"

    def test_summary_missing_theme_in_repair_brief(self):
        """Missing theme IDs appear in repair_brief.global_issues.narrative_missing_summary_themes."""
        pkt = _make_narrative_packet()
        resume = _narrative_resume(
            summary_lines="Experienced manager leading teams.",
            anchor_bullets=(
                "- Improved reliability and uptime significantly.\n"
                "- Led incident response efforts.\n"
                "- Managed a team of eight engineers."
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        missing = report["repair_brief"]["global_issues"].get("narrative_missing_summary_themes", [])
        assert "T1" in missing, f"T1 should be in missing summary themes: {missing}"

    # ------------------------------------------------------------------
    # Check 13: AnchorRoleDominanceValidator
    # ------------------------------------------------------------------

    def test_anchor_role_bullets_missing_theme_fails(self):
        """First 3 anchor bullets have 0 T1 hits (need 2) → NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED."""
        pkt = _make_narrative_packet()
        # Summary covers both themes to avoid Check 12 failure
        # First 3 anchor bullets only mention T2 terms, no T1
        resume = _narrative_resume(
            summary_lines=(
                "Engineering leader improving reliability and incident response, "
                "managing teams effectively."
            ),
            anchor_bullets=(
                "- Managed a cross-functional team of ten engineers.\n"
                "- Led hiring and mentored junior engineers.\n"
                "- Coordinated team performance reviews.\n"
                "- Reduced platform incidents through better monitoring."  # T1, but bullet 4
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        anchor_errors = [e for e in report["errors"] if "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED" in e]
        assert anchor_errors, (
            f"Expected NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED, got: {report['errors']}"
        )
        assert "T1" in anchor_errors[0], f"T1 should be in the error: {anchor_errors[0]}"

    def test_anchor_role_bullets_all_themes_covered_passes(self):
        """First 3 bullets have ≥2 T1 hits and ≥1 T2 hit → no anchor dominance error."""
        pkt = _make_narrative_packet()
        resume = _narrative_resume(
            summary_lines=(
                "Engineering leader driving reliability and incident response, managing teams."
            ),
            anchor_bullets=(
                "- Improved platform reliability through enhanced monitoring and uptime SLAs.\n"
                "- Led incident response protocols reducing mean time to recovery.\n"
                "- Managed and mentored a team of ten engineers."
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        anchor_errors = [e for e in report["errors"] if "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED" in e]
        assert not anchor_errors, f"All themes covered — no error expected: {anchor_errors}"

    def test_anchor_role_missing_theme_in_repair_brief(self):
        """Missing anchor theme IDs appear in repair_brief.global_issues."""
        pkt = _make_narrative_packet()
        resume = _narrative_resume(
            summary_lines="Engineering leader improving reliability and incident response, managing teams.",
            anchor_bullets=(
                "- Managed cross-functional teams.\n"
                "- Led hiring process for three roles.\n"
                "- Mentored junior engineers."
            ),
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        missing = report["repair_brief"]["global_issues"].get("narrative_anchor_missing_theme_ids", [])
        assert "T1" in missing, f"T1 should be missing: {missing}"

    # ------------------------------------------------------------------
    # Check 14: DomainTranslationAnchorValidator
    # ------------------------------------------------------------------

    def test_domain_translation_anchor_not_met_fails(self):
        """Ledger entries exist but none in anchor role → DOMAIN_TRANSLATION_ANCHOR_NOT_MET."""
        narrative_plan = {
            **_SAMPLE_NARRATIVE_PLAN,
            "domain_translation_binding": {
                "min_total_rule_instantiations": 1,
                "min_instantiations_in_anchor_role": 1,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        }
        pkt = _make_narrative_packet(narrative_plan=narrative_plan, domain_mismatch=True)
        resume = _narrative_resume(
            summary_lines="Engineering leader improving reliability and incident response, managing teams.",
            anchor_bullets=(
                "- Improved reliability and uptime of the platform.\n"
                "- Led incident response and managed team.\n"
                "- Drove uptime SLAs and mentored engineers."
            ),
        )
        # Ledger has entries, but in a non-anchor role
        dt_ledger = [
            {
                "rule_id": "rule_1",
                "target_frame_used": "payment processing",
                "exact_span": "payment processing flow",
                "location": "resume.role[Senior Engineer | OtherCo].bullet[1]",
            }
        ]
        report = validate_phase2_output(
            pkt, resume, _narrative_cover(), _today(),
            domain_translation_ledger=dt_ledger,
        )
        anchor_err = [e for e in report["errors"] if "DOMAIN_TRANSLATION_ANCHOR_NOT_MET" in e]
        assert anchor_err, (
            f"Expected DOMAIN_TRANSLATION_ANCHOR_NOT_MET, got: {report['errors']}"
        )

    def test_domain_translation_all_pass(self):
        """Ledger has entry in anchor role → no domain translation anchor error."""
        narrative_plan = {
            **_SAMPLE_NARRATIVE_PLAN,
            "domain_translation_binding": {
                "min_total_rule_instantiations": 1,
                "min_instantiations_in_anchor_role": 1,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        }
        pkt = _make_narrative_packet(narrative_plan=narrative_plan, domain_mismatch=True)
        resume = _narrative_resume(
            summary_lines="Engineering leader improving reliability and incident response, managing teams.",
            anchor_bullets=(
                "- Improved reliability and uptime of the platform.\n"
                "- Led incident response and managed team.\n"
                "- Drove uptime SLAs and mentored engineers."
            ),
        )
        # Ledger has entry in the anchor role
        dt_ledger = [
            {
                "rule_id": "rule_1",
                "target_frame_used": "reliability engineering",
                "exact_span": "reliability and uptime",
                "location": f"resume.role[{_NARRATIVE_ANCHOR_ROLE}].bullet[1]",
            }
        ]
        report = validate_phase2_output(
            pkt, resume, _narrative_cover(), _today(),
            domain_translation_ledger=dt_ledger,
        )
        dt_errors = [
            e for e in report["errors"]
            if "DOMAIN_TRANSLATION_ANCHOR_NOT_MET" in e
            or "DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET" in e
        ]
        assert not dt_errors, f"Anchor entry present — no DT error expected: {dt_errors}"

    def test_domain_translation_min_total_not_met_fails(self):
        """Active ledger entries below min_total → DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET."""
        narrative_plan = {
            **_SAMPLE_NARRATIVE_PLAN,
            "domain_translation_binding": {
                "min_total_rule_instantiations": 2,
                "min_instantiations_in_anchor_role": 0,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        }
        pkt = _make_narrative_packet(narrative_plan=narrative_plan, domain_mismatch=True)
        resume = _narrative_resume(
            summary_lines="Engineering leader improving reliability and incident response, managing teams.",
            anchor_bullets=(
                "- Improved reliability and uptime of the platform.\n"
                "- Led incident response and managed team.\n"
                "- Drove SLAs and mentored engineers."
            ),
        )
        # Only 1 active entry, need 2
        dt_ledger = [
            {
                "rule_id": "rule_1",
                "target_frame_used": "reliability",
                "exact_span": "reliability and uptime",
                "location": f"resume.role[{_NARRATIVE_ANCHOR_ROLE}].bullet[1]",
            }
        ]
        report = validate_phase2_output(
            pkt, resume, _narrative_cover(), _today(),
            domain_translation_ledger=dt_ledger,
        )
        total_err = [e for e in report["errors"] if "DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET" in e]
        assert total_err, f"Expected DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET, got: {report['errors']}"

    def test_narrative_plan_absent_skips_all_narrative_checks(self):
        """Empty narrative_plan → no narrative errors emitted."""
        pkt = _make_narrative_packet(narrative_plan={})
        # Resume has no summary and no anchor role bullets (worst case)
        resume = (
            "Experience\n"
            f"{_NARRATIVE_ANCHOR_ROLE} | 2022 - Present\n"
            "- Built infrastructure.\n"
            "- Deployed services.\n"
            "- Monitored systems.\n"
            "- Improved tooling.\n\n"
            "Technical Skills\nPython\n"
        )
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        narrative_errors = [
            e for e in report["errors"]
            if any(
                code in e
                for code in (
                    "NARRATIVE_SUMMARY_THEME_MISSING",
                    "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED",
                    "DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET",
                    "DOMAIN_TRANSLATION_ANCHOR_NOT_MET",
                    "DOMAIN_TRANSLATION_FIRST_K_NOT_MET",
                )
            )
        ]
        assert not narrative_errors, (
            f"Empty narrative_plan must skip all narrative checks: {narrative_errors}"
        )

    def test_domain_translation_skipped_when_no_mismatch(self):
        """domain_mismatch=False → Check 14 is skipped even if ledger provided."""
        narrative_plan = {
            **_SAMPLE_NARRATIVE_PLAN,
            "domain_translation_binding": {
                "min_total_rule_instantiations": 5,  # would fail if checked
                "min_instantiations_in_anchor_role": 5,
                "require_target_frame_in_anchor_role_first_k": True,
            },
        }
        pkt = _make_narrative_packet(narrative_plan=narrative_plan, domain_mismatch=False)
        resume = _narrative_resume(
            summary_lines="Engineering leader improving reliability and incident response, managing teams.",
            anchor_bullets=(
                "- Improved reliability and uptime of the platform.\n"
                "- Led incident response and managed team.\n"
                "- Drove SLAs and mentored engineers."
            ),
        )
        report = validate_phase2_output(
            pkt, resume, _narrative_cover(), _today(),
            domain_translation_ledger=[],  # empty but provided
        )
        dt_errors = [
            e for e in report["errors"]
            if "DOMAIN_TRANSLATION" in e
        ]
        assert not dt_errors, (
            f"No mismatch → Check 14 must be skipped: {dt_errors}"
        )


# ---------------------------------------------------------------------------
# Skill graph validators (Checks 15/16)
# ---------------------------------------------------------------------------

_SG_DIRECT = ["python", "docker", "kubernetes", "postgresql", "rest apis"]
_SG_SKILLS_SECTION_ONLY = ["event-driven architecture"]  # allowed_usage=skills_section_only
_SG_CAN_CLAIM = ["container orchestration"]              # allowed_usage=can_claim_experience


def _make_skill_graph_packet(
    skill_allowlist_skills_section: list[str] | None = None,
    skill_allowlist_experience_claims: list[str] | None = None,
) -> dict:
    """Minimal writer_packet for skill graph validator tests."""
    ss = skill_allowlist_skills_section if skill_allowlist_skills_section is not None \
        else _SG_DIRECT + _SG_SKILLS_SECTION_ONLY + _SG_CAN_CLAIM
    exp = skill_allowlist_experience_claims if skill_allowlist_experience_claims is not None \
        else _SG_DIRECT + _SG_CAN_CLAIM
    return {
        "role_level": "senior",
        "narrative_plan": {},
        "domain_mismatch": False,
        "master_resume_role_names": ["Senior Engineer | Acme Corp"],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "role_priorities": {"Senior Engineer | Acme Corp": "high"},
        "role_source_bullet_counts": {"Senior Engineer | Acme Corp": 5},
        "role_source_char_counts": {"Senior Engineer | Acme Corp": 400},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 3, "medium": 2, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
        "skill_allowlist_skills_section": ss,
        "skill_allowlist_experience_claims": exp,
        # 2-tier skill policy: git/jenkins are in truth tier only (not JD-scoped focus)
        "skill_policy": {
            "skills_truth_allowlist": _SG_DIRECT + _SG_CAN_CLAIM + _SG_SKILLS_SECTION_ONLY + ["git", "jenkins"],
            "skills_focus_allowlist": _SG_DIRECT + _SG_CAN_CLAIM + _SG_SKILLS_SECTION_ONLY,
            "focus_skills_min_count": 5,
            "claim_experience_allowlist": _SG_DIRECT + _SG_CAN_CLAIM,
            "cover_letter_tool_allowlist_global": _SG_DIRECT,
        },
    }


def _sg_resume(skills_section: str, bullets: str = "") -> str:
    """Build a minimal resume for skill graph tests."""
    bullet_block = bullets or (
        "- Built distributed systems for scalability.\n"
        "- Improved platform reliability and uptime.\n"
        "- Led incident response processes.\n"
        "- Mentored junior engineers."
    )
    return (
        "Professional Summary\n"
        "Experienced engineer focused on distributed systems.\n\n"
        "Experience\n"
        f"Senior Engineer | Acme Corp | 2021 - Present\n"
        f"{bullet_block}\n\n"
        f"Technical Skills\n{skills_section}\n"
    )


class TestSkillGraphValidators:
    """Checks 15/16: SkillSectionAllowlistValidator, ExperienceToolClaimAllowlistValidator."""

    def test_skill_section_violation(self):
        """Skills section lists 'Terraform' not in allowlist → SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION."""
        pkt = _make_skill_graph_packet()
        resume = _sg_resume("Python, Docker, Terraform")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION" in e]
        assert errs, f"Expected skill section violation for 'terraform', got: {report['errors']}"
        assert any("terraform" in e for e in errs), errs

    def test_experience_tool_violation(self):
        """Bullet mentions 'Azure' not in experience claim allowlist → SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION."""
        pkt = _make_skill_graph_packet()
        bullets = (
            "- Deployed microservices on Azure for high availability.\n"
            "- Built distributed systems for scalability."
        )
        resume = _sg_resume("Python, Docker", bullets)
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION" in e]
        assert errs, f"Expected experience tool violation for 'azure', got: {report['errors']}"
        assert any("azure" in e for e in errs), errs

    def test_skills_section_allowed_concept_passes(self):
        """'event-driven architecture' in skills_section_only allowlist → no Check 15 error."""
        pkt = _make_skill_graph_packet()
        resume = _sg_resume("Python, Docker, event-driven architecture")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION" in e]
        assert not errs, f"Allowed concept must not trigger violation: {errs}"

    def test_experience_direct_skill_passes(self):
        """Bullet mentions 'Kubernetes' which is in direct_skills (experience claim allowed) → no Check 16 error."""
        pkt = _make_skill_graph_packet()
        bullets = (
            "- Orchestrated containers using Kubernetes for high availability.\n"
            "- Built distributed systems for scalability."
        )
        resume = _sg_resume("Python, Docker, Kubernetes", bullets)
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION" in e]
        assert not errs, f"Direct skill 'kubernetes' in experience claim allowlist must not fail: {errs}"


class TestTwoTierSkillAllowlist:
    """Checks 15 (truth tier) and 15b (focus-coverage warning) for 2-tier skill policy."""

    def test_truth_allows_git_and_jenkins(self):
        """git and jenkins are in truth allowlist → no SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION."""
        pkt = _make_skill_graph_packet()
        resume = _sg_resume("Python, Docker, git, Jenkins")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION" in e]
        assert not errs, f"git/jenkins in truth allowlist must not trigger violation: {errs}"

    def test_truth_blocks_genuinely_invented_skill(self):
        """A skill not in any allowlist tier → SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION."""
        pkt = _make_skill_graph_packet()
        resume = _sg_resume("Python, Docker, OracleDB_Pro_X9_Ultra")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION" in e]
        assert errs, f"Completely invented skill must be flagged: {report['errors']}"

    def test_focus_warning_emitted_when_below_min(self):
        """Skills section with fewer focus skills than focus_skills_min_count → SKILL_FOCUS_MIN_NOT_MET warning."""
        pkt = _make_skill_graph_packet()
        # Only 2 focus skills (min is 5)
        resume = _sg_resume("Python, Docker")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        warnings = report.get("warnings", [])
        assert any("SKILL_FOCUS_MIN_NOT_MET" in w for w in warnings), (
            f"Expected SKILL_FOCUS_MIN_NOT_MET warning, got: {warnings}"
        )

    def test_focus_no_warning_when_min_met(self):
        """Skills section meets focus_skills_min_count (≥ 5) → no SKILL_FOCUS_MIN_NOT_MET warning."""
        pkt = _make_skill_graph_packet()
        # Include all 7 focus skills: _SG_DIRECT (5) + _SG_CAN_CLAIM (1) + _SG_SKILLS_SECTION_ONLY (1)
        all_focus = ", ".join(_SG_DIRECT + _SG_CAN_CLAIM + _SG_SKILLS_SECTION_ONLY)
        resume = _sg_resume(all_focus)
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        warnings = report.get("warnings", [])
        assert not any("SKILL_FOCUS_MIN_NOT_MET" in w for w in warnings), (
            f"Unexpected SKILL_FOCUS_MIN_NOT_MET warning: {warnings}"
        )

    def test_cl_v4_uses_global_allowlist(self):
        """Tool in cover_letter_tool_allowlist_global (not per-proof list) → no CL_TOOL_ALLOWLIST_VIOLATION."""
        pkt = _make_skill_graph_packet()
        # Add "terraform" to global allowlist; it is NOT in per-proof allowed_tool_mentions
        pkt = dict(pkt)
        pkt["skill_policy"] = dict(pkt["skill_policy"])
        pkt["skill_policy"]["cover_letter_tool_allowlist_global"] = ["terraform"]
        pkt["direct_skills_set"] = []  # ensure not covered by direct skills path
        pkt["cover_letter_plan"] = {
            "structure_version": "CL_V1_4PARA_2PROOF",
            "bridge_sentence_required": False,
            "proof_points": [
                {
                    "proof_id": "P1",
                    "required_exact_span": "I used Terraform to manage infrastructure.",
                    "allowed_tool_mentions": [],  # terraform NOT in per-proof list
                },
            ],
        }
        # Cover letter: Dear salutation + 3 body paragraphs (hook, P1 with terraform, P2)
        cl = (
            "Dear Hiring Manager,\n\n"
            "Hook paragraph.\n\n"
            "I used Terraform to manage infrastructure.\n\n"
            "We scaled using proven patterns."
        )
        report = validate_phase2_output(pkt, _sg_resume("Python, Docker"), cl, _today())
        errs = [e for e in report["errors"] if "CL_TOOL_ALLOWLIST_VIOLATION" in e]
        assert not errs, (
            f"Terraform in cl_global_allowlist must not trigger CL_TOOL_ALLOWLIST_VIOLATION: {errs}"
        )

    def test_backward_compat_no_skill_policy_key(self):
        """Packet without skill_policy falls back to skill_allowlist_skills_section."""
        pkt = _make_skill_graph_packet()
        pkt = {k: v for k, v in pkt.items() if k != "skill_policy"}
        # "Terraform" is not in old-style allowlist → still flagged
        resume = _sg_resume("Python, Terraform")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        errs = [e for e in report["errors"] if "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION" in e]
        assert errs, f"Terraform should still be flagged without skill_policy: {errs}"

    def test_focus_warning_is_not_a_hard_error(self):
        """Focus shortfall emits a warning only, not a hard error; report.ok is unaffected by it."""
        pkt = _make_skill_graph_packet()
        # Minimal skills → focus shortfall
        resume = _sg_resume("Python")
        report = validate_phase2_output(pkt, resume, _narrative_cover(), _today())
        warnings = report.get("warnings", [])
        focus_warnings = [w for w in warnings if "SKILL_FOCUS_MIN_NOT_MET" in w]
        # Warning emitted, but it is NOT in errors
        assert focus_warnings, "SKILL_FOCUS_MIN_NOT_MET should appear in warnings"
        focus_errors = [e for e in report["errors"] if "SKILL_FOCUS_MIN_NOT_MET" in e]
        assert not focus_errors, "SKILL_FOCUS_MIN_NOT_MET must not appear in errors"

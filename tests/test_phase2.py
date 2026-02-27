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

    def test_mechanisms_with_unsafe_nouns_are_filtered(self):
        """Mechanism phrases containing an unsafe JD noun are excluded from must_surface_mechanisms."""
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
        mechanisms_lower = [m.lower() for m in packet["must_surface_mechanisms"]]
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

    def test_legacy_must_surface_mechanisms_still_present(self):
        """Backward compat: legacy field still in packet (release N)."""
        pkt = self._packet()
        assert "must_surface_mechanisms" in pkt

    def test_legacy_field_equals_arch_list(self):
        """Legacy must_surface_mechanisms == must_surface_arch_mechanisms (release N)."""
        pkt = self._packet()
        assert pkt["must_surface_mechanisms"] == pkt["must_surface_arch_mechanisms"]

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
        pkt = self._packet_for_level("staff")
        rwp = pkt["role_weight_profile"]
        assert rwp["arch_weight"] == 0.7  # default


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
        "must_surface_mechanisms": [],
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
            "must_surface_mechanisms": arch_mechanisms,  # legacy compat
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

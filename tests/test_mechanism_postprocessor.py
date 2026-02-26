"""Tests for mechanism_postprocessor.postprocess_mechanism_enforcement."""

import unittest

from tailor.mechanism_postprocessor import postprocess_mechanism_enforcement


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_writer_packet(
    must_surface: list[str],
    mechanism_min: int = 2,
    role_priority_keys: list[str] | None = None,
    source_bullet_count: int = 5,
) -> dict:
    """Build a minimal writer_packet for testing."""
    role_priority_keys = role_priority_keys or []
    return {
        "must_surface_mechanisms": must_surface,
        "density_targets": {
            "mechanism_min_by_priority": {"high": mechanism_min, "medium": 1, "low": 0},
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
        },
        "role_priorities": {k: "high" for k in role_priority_keys},
        "role_source_bullet_counts": {k: source_bullet_count for k in role_priority_keys},
        "role_source_char_counts": {k: 500 for k in role_priority_keys},
        "jd_is_delivery_oriented": False,
        "must_keep_metrics": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
    }


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestMechanismPostprocessor(unittest.TestCase):

    def test_role_with_zero_mechanisms_gets_two_injected(self):
        """High-priority role with 4 bullets and 0 mechanisms receives 2 injected phrases."""
        resume = "\n".join([
            "Professional Summary",
            "Senior engineer with broad experience.",
            "",
            "Experience",
            "Senior Engineer | Acme Corp | 2022 - Present",
            "- Built a robust distributed pipeline for real-time ingestion",
            "- Improved query response time by 40% through indexing",
            "- Led cross-functional team of 8 engineers",
            "- Delivered new feature set ahead of schedule",
            "",
            "Education",
            "BS Computer Science | State University | 2014 - 2018",
        ])

        wp = _make_writer_packet(
            must_surface=["Redis", "horizontal scaling"],
            mechanism_min=2,
            role_priority_keys=["Senior Engineer | Acme Corp"],
        )

        result = postprocess_mechanism_enforcement(
            {"resume": resume, "cover_letter": "Dear Hiring Manager,"},
            wp,
        )

        new_resume = result["resume"]
        # Locate the experience block text
        exp_start = new_resume.index("Senior Engineer | Acme Corp")
        exp_end = new_resume.index("Education")
        role_block = new_resume[exp_start:exp_end].lower()

        self.assertIn("redis", role_block, "Redis should have been injected")
        self.assertIn("horizontal scaling", role_block, "horizontal scaling should have been injected")

    def test_role_already_satisfied_is_unchanged(self):
        """A role already meeting its mechanism minimum is left completely untouched."""
        resume = "\n".join([
            "Experience",
            "Senior Engineer | Acme Corp | 2022 - Present",
            "- Deployed Redis caching layer that cut p99 latency by 60%",
            "- Applied horizontal scaling via stateless service replication",
            "- Shipped 3 major features per quarter",
            "- Mentored 4 junior engineers",
            "",
            "Education",
            "BS CS | University | 2014",
        ])

        wp = _make_writer_packet(
            must_surface=["Redis", "horizontal scaling"],
            mechanism_min=2,
            role_priority_keys=["Senior Engineer | Acme Corp"],
        )

        result = postprocess_mechanism_enforcement(
            {"resume": resume, "cover_letter": "Cover letter text."},
            wp,
        )

        # Resume should be identical — no injection needed
        self.assertEqual(result["resume"], resume)
        self.assertEqual(result["cover_letter"], "Cover letter text.")

    def test_no_duplicate_if_phrase_already_present(self):
        """A phrase already in the role block is not injected a second time."""
        resume = "\n".join([
            "Experience",
            "Senior Engineer | Acme Corp | 2022 - Present",
            "- Deployed Redis caching layer reducing database load by 70%",
            "- Improved performance through query optimisation",
            "- Led architecture review sessions",
            "- Shipped major release that reduced bug reports by 30%",
            "",
            "Education",
            "BS CS | University | 2014",
        ])

        # "Redis" is already in the block; "Kafka" is not
        wp = _make_writer_packet(
            must_surface=["Redis", "Kafka"],
            mechanism_min=2,
            role_priority_keys=["Senior Engineer | Acme Corp"],
        )

        result = postprocess_mechanism_enforcement(
            {"resume": resume, "cover_letter": ""},
            wp,
        )

        new_resume = result["resume"]
        exp_start = new_resume.index("Senior Engineer | Acme Corp")
        exp_end = new_resume.index("Education")
        role_block = new_resume[exp_start:exp_end]

        # "Redis" should appear exactly once
        self.assertEqual(
            role_block.lower().count("redis"), 1,
            "Redis must not be duplicated",
        )
        # "Kafka" should have been injected (role had 1 mechanism, needed 2)
        self.assertIn("kafka", role_block.lower(), "Kafka should have been injected")

    def test_multiple_roles_distributes_phrases_without_reuse(self):
        """Two high-priority roles each get distinct phrases from a pool of 4."""
        resume = "\n".join([
            "Experience",
            "Senior Engineer | Acme Corp | 2022 - Present",
            "- Built the ingestion pipeline from scratch",
            "- Optimised batch processing job runtime",
            "- Shipped new analytics dashboard",
            "- Coordinated with product team on requirements",
            "",
            "Software Engineer | Beta Corp | 2019 - 2022",
            "- Developed backend services consumed by mobile clients",
            "- Wrote integration tests for payment service",
            "- Maintained build pipeline and deployment scripts",
            "- Participated in on-call rotation",
            "",
            "Education",
            "BS CS | University | 2015",
        ])

        pool = ["Redis", "Kafka", "Docker", "Kubernetes"]
        wp = _make_writer_packet(
            must_surface=pool,
            mechanism_min=2,
            role_priority_keys=[
                "Senior Engineer | Acme Corp",
                "Software Engineer | Beta Corp",
            ],
        )

        result = postprocess_mechanism_enforcement(
            {"resume": resume, "cover_letter": ""},
            wp,
        )

        new_resume = result["resume"]

        # Locate each role block
        acme_start = new_resume.index("Senior Engineer | Acme Corp")
        beta_start = new_resume.index("Software Engineer | Beta Corp")
        edu_start = new_resume.index("Education")

        acme_block = new_resume[acme_start:beta_start].lower()
        beta_block = new_resume[beta_start:edu_start].lower()

        # Each role must contain at least 2 of the pool phrases
        acme_found = [p for p in pool if p.lower() in acme_block]
        beta_found = [p for p in pool if p.lower() in beta_block]

        self.assertGreaterEqual(len(acme_found), 2, f"Acme role phrases: {acme_found}")
        self.assertGreaterEqual(len(beta_found), 2, f"Beta role phrases: {beta_found}")

        # Phrases injected into each role should be disjoint (no reuse)
        acme_set = set(acme_found)
        beta_set = set(beta_found)
        self.assertTrue(
            acme_set.isdisjoint(beta_set),
            f"Phrases should not be reused across roles: acme={acme_set} beta={beta_set}",
        )

    def test_formatting_preserved(self):
        """Injection must not add or remove lines; bullet prefixes must stay intact."""
        resume = "\n".join([
            "Experience",
            "Senior Engineer | Acme Corp | 2022 - Present",
            "- Built the ingestion pipeline",
            "- Optimised performance via query rewriting",
            "- Shipped analytics dashboard",
            "- Coordinated stakeholder reviews",
            "",
            "Education",
            "BS CS | University | 2015",
        ])

        wp = _make_writer_packet(
            must_surface=["Redis", "Kafka"],
            mechanism_min=2,
            role_priority_keys=["Senior Engineer | Acme Corp"],
        )

        result = postprocess_mechanism_enforcement(
            {"resume": resume, "cover_letter": ""},
            wp,
        )

        original_lines = resume.split("\n")
        new_lines = result["resume"].split("\n")

        # Same number of lines (no lines added or removed)
        self.assertEqual(
            len(original_lines), len(new_lines),
            "Injection must not change the total line count",
        )

        # All lines that were not bullets should be identical
        for orig, new in zip(original_lines, new_lines):
            if orig.strip().startswith("- ") or new.strip().startswith("- "):
                # Bullet lines may have been extended — check prefix preserved
                if orig.strip().startswith("- "):
                    self.assertTrue(
                        new.strip().startswith("- "),
                        f"Bullet prefix must be preserved: {new!r}",
                    )
                    # The original content should still be present as a prefix
                    self.assertTrue(
                        new.startswith(orig.rstrip()),
                        f"Original bullet content must be intact: orig={orig!r} new={new!r}",
                    )
            else:
                # Non-bullet lines must be identical
                self.assertEqual(
                    orig, new,
                    f"Non-bullet line was modified: orig={orig!r} new={new!r}",
                )


class TestNormalizeViaPhrase(unittest.TestCase):
    """Unit tests for _normalize_via_phrase helper."""

    def setUp(self):
        from tailor.mechanism_postprocessor import _normalize_via_phrase
        self.nvp = _normalize_via_phrase

    def test_title_case_common_word_lowercased(self):
        self.assertEqual(
            self.nvp("Horizontal scaling of trading servers"),
            "horizontal scaling of trading servers",
        )

    def test_title_case_noun_lowercased(self):
        self.assertEqual(
            self.nvp("Database read replicas for read-heavy GET endpoints"),
            "database read replicas for read-heavy GET endpoints",
        )

    def test_all_caps_acronym_preserved(self):
        self.assertEqual(self.nvp("API rate limiting"), "API rate limiting")

    def test_all_caps_acronym_sql_preserved(self):
        self.assertEqual(self.nvp("SQL query optimisation"), "SQL query optimisation")

    def test_camelcase_brand_preserved(self):
        self.assertEqual(self.nvp("PostgreSQL replication"), "PostgreSQL replication")

    def test_camelcase_brand_mongo_preserved(self):
        self.assertEqual(self.nvp("MongoDB sharding"), "MongoDB sharding")

    def test_already_lowercase_unchanged(self):
        self.assertEqual(self.nvp("transactional cache"), "transactional cache")

    def test_empty_string(self):
        self.assertEqual(self.nvp(""), "")


class TestInjectTrailingPunctuation(unittest.TestCase):
    """Injection must strip trailing .,;: from bullet before appending via clause."""

    def _inject(self, bullet: str, phrase: str) -> str:
        from tailor.mechanism_postprocessor import _inject_phrases_into_role_lines
        lines = [bullet]
        _inject_phrases_into_role_lines(lines, 0, 1, [phrase])
        return lines[0]

    def test_trailing_period_stripped(self):
        result = self._inject(
            "- Improved reliability and regulatory compliance.",
            "Database read replicas",
        )
        self.assertNotIn("compliance. via", result)
        self.assertIn("compliance via database read replicas", result.lower())

    def test_trailing_comma_stripped(self):
        result = self._inject(
            "- Delivered high-performance components,",
            "Horizontal scaling",
        )
        self.assertNotIn("components, via", result)
        self.assertIn("components via horizontal scaling", result.lower())

    def test_no_trailing_punctuation_unchanged(self):
        result = self._inject("- Improved system throughput", "Horizontal scaling")
        self.assertIn("throughput via horizontal scaling", result.lower())


if __name__ == "__main__":
    unittest.main()

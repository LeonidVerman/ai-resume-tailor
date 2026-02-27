"""Tests for mechanism_taxonomy: classification, canonicalization, dedup, and taxonomy build."""

import unittest

from tailor.mechanism_taxonomy import (
    PhraseCategory,
    classify_phrase,
    build_mechanism_taxonomy,
    _get_canonical_phrase,
    _normalize_for_dedup,
)


# ---------------------------------------------------------------------------
# classify_phrase
# ---------------------------------------------------------------------------

class TestClassifyPhrase(unittest.TestCase):

    # --- ARCH ---

    def test_arch_caching(self):
        self.assertEqual(classify_phrase("transactional caching layer"), PhraseCategory.ARCH)

    def test_arch_horizontal_scaling(self):
        self.assertEqual(classify_phrase("Horizontal scaling of trading servers"), PhraseCategory.ARCH)

    def test_arch_read_replica(self):
        self.assertEqual(classify_phrase("Database read replicas for read-heavy GET endpoints"), PhraseCategory.ARCH)

    def test_arch_async(self):
        self.assertEqual(classify_phrase("async message queue decoupling"), PhraseCategory.ARCH)

    def test_arch_redis(self):
        self.assertEqual(classify_phrase("Redis-backed session store"), PhraseCategory.ARCH)

    def test_arch_kafka(self):
        self.assertEqual(classify_phrase("Kafka event streaming pipeline"), PhraseCategory.ARCH)

    def test_arch_distributed(self):
        self.assertEqual(classify_phrase("high-throughput distributed platform scaling"), PhraseCategory.ARCH)

    def test_arch_circuit_breaker(self):
        self.assertEqual(classify_phrase("circuit breaker for downstream API calls"), PhraseCategory.ARCH)

    def test_arch_kubernetes(self):
        self.assertEqual(classify_phrase("Kubernetes cluster autoscaling"), PhraseCategory.ARCH)

    # --- STRATEGIC ---

    def test_strategic_led_verb(self):
        self.assertEqual(classify_phrase("Led engineering vision for crypto trading platform"), PhraseCategory.STRATEGIC)

    def test_strategic_drove(self):
        self.assertEqual(classify_phrase("Drove alignment across 3 engineering orgs"), PhraseCategory.STRATEGIC)

    def test_strategic_roadmap_keyword(self):
        self.assertEqual(classify_phrase("Defined product roadmap for enterprise SaaS"), PhraseCategory.STRATEGIC)

    def test_strategic_stakeholder(self):
        self.assertEqual(classify_phrase("stakeholder alignment across product and engineering"), PhraseCategory.STRATEGIC)

    def test_strategic_technical_vision(self):
        self.assertEqual(classify_phrase("technical vision for platform modernization"), PhraseCategory.STRATEGIC)

    # --- OPERATIONAL ---

    def test_operational_agile(self):
        self.assertEqual(classify_phrase("agile sprint planning and retrospectives"), PhraseCategory.OPERATIONAL)

    def test_operational_code_review(self):
        self.assertEqual(classify_phrase("code review culture and engineering standards"), PhraseCategory.OPERATIONAL)

    def test_operational_observability(self):
        self.assertEqual(classify_phrase("observability via structured logging and metrics"), PhraseCategory.OPERATIONAL)

    def test_operational_documentation(self):
        self.assertEqual(classify_phrase("documentation standards for system components"), PhraseCategory.OPERATIONAL)

    def test_operational_sla(self):
        self.assertEqual(classify_phrase("SLA compliance and incident response process"), PhraseCategory.OPERATIONAL)

    # --- REJECT ---

    def test_reject_skilled_in(self):
        self.assertEqual(classify_phrase("Skilled in designing high-performance systems"), PhraseCategory.REJECT)

    def test_reject_experienced_in(self):
        self.assertEqual(classify_phrase("Experienced in distributed architecture"), PhraseCategory.REJECT)

    def test_reject_proficient_in(self):
        self.assertEqual(classify_phrase("proficient in cloud technologies"), PhraseCategory.REJECT)

    def test_reject_empty(self):
        self.assertEqual(classify_phrase(""), PhraseCategory.REJECT)

    def test_reject_generic_unclassified(self):
        # Generic phrase with no recognizable keyword → REJECT
        self.assertEqual(classify_phrase("strong communication skills"), PhraseCategory.REJECT)

    # --- ARCH beats STRATEGIC when both could match ---

    def test_arch_takes_priority_over_strategic(self):
        # "Led" prefix + "caching" → ARCH wins (implementation beats leadership)
        result = classify_phrase("Led caching layer implementation")
        self.assertEqual(result, PhraseCategory.ARCH)


# ---------------------------------------------------------------------------
# _get_canonical_phrase (variant map)
# ---------------------------------------------------------------------------

class TestGetCanonicalPhrase(unittest.TestCase):

    def test_read_replica_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("read replica"), "database read replicas")

    def test_read_replicas_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("read replicas"), "database read replicas")

    def test_multi_layer_caching_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("multi layer caching"), "multiple caching layers")

    def test_multilayer_caching_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("multilayer caching"), "multiple caching layers")

    def test_async_decoupling_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("async decoupling"), "asynchronous messaging")

    def test_horizontal_scale_canonicalized(self):
        self.assertEqual(_get_canonical_phrase("horizontal scale"), "horizontal scaling")

    def test_unknown_phrase_unchanged(self):
        self.assertEqual(_get_canonical_phrase("Redis caching layer"), "Redis caching layer")

    def test_strip_whitespace(self):
        self.assertEqual(_get_canonical_phrase("  horizontal scaling  "), "horizontal scaling")


# ---------------------------------------------------------------------------
# build_mechanism_taxonomy — taxonomy split
# ---------------------------------------------------------------------------

class TestBuildMechanismTaxonomy(unittest.TestCase):

    def _tax(self, phrases, unsafe=None):
        return build_mechanism_taxonomy(phrases, unsafe_nouns=unsafe)

    def test_arch_phrases_classified_correctly(self):
        phrases = [
            "horizontal scaling of trading servers",
            "Database read replicas for read-heavy GET endpoints",
            "Redis-backed session caching",
        ]
        result = self._tax(phrases)
        self.assertEqual(len(result["arch"]), 3)
        self.assertEqual(len(result["strategic"]), 0)
        self.assertEqual(len(result["operational"]), 0)
        self.assertEqual(len(result["rejected"]), 0)

    def test_capability_statements_rejected(self):
        phrases = [
            "Experienced in distributed systems",
            "Skilled in designing APIs",
            "horizontal scaling pipeline",
        ]
        result = self._tax(phrases)
        self.assertEqual(len(result["rejected"]), 2)
        self.assertEqual(len(result["arch"]), 1)

    def test_polluted_mix_splits_correctly(self):
        phrases = [
            "horizontal scaling of trading servers",        # ARCH
            "Led technical vision for platform",            # STRATEGIC
            "agile sprint ceremonies",                      # OPERATIONAL
            "Experienced in building APIs",                 # REJECT
            "Redis caching layer for session data",         # ARCH
        ]
        result = self._tax(phrases)
        self.assertEqual(len(result["arch"]), 2)
        self.assertEqual(len(result["strategic"]), 1)
        self.assertEqual(len(result["operational"]), 1)
        self.assertEqual(len(result["rejected"]), 1)

    def test_unsafe_noun_filtered(self):
        phrases = [
            "horizontal scaling of trading servers",
            "ServiceNow integration via REST API",  # contains unsafe "ServiceNow"
        ]
        result = self._tax(phrases, unsafe=["ServiceNow"])
        self.assertEqual(len(result["arch"]), 1)
        self.assertNotIn("ServiceNow", " ".join(result["arch"]))

    def test_empty_and_whitespace_filtered(self):
        result = self._tax(["", "  ", "horizontal scaling"])
        self.assertEqual(len(result["arch"]), 1)


# ---------------------------------------------------------------------------
# build_mechanism_taxonomy — deduplication
# ---------------------------------------------------------------------------

class TestBuildMechanismTaxonomyDedup(unittest.TestCase):

    def _tax(self, phrases):
        return build_mechanism_taxonomy(phrases)

    def test_exact_duplicate_deduplicated(self):
        result = self._tax([
            "Redis caching layer",
            "Redis caching layer",
        ])
        self.assertEqual(len(result["arch"]), 1)

    def test_variant_map_dedup_read_replica(self):
        """'read replica' and 'read replicas' both canonicalize to 'database read replicas'."""
        result = self._tax(["read replica", "read replicas"])
        arch = result["arch"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0], "database read replicas")

    def test_variant_map_dedup_multi_layer_caching(self):
        """Near-duplicate caching phrases collapse to one canonical."""
        result = self._tax(["multi layer caching", "multilayer caching"])
        arch = result["arch"]
        self.assertEqual(len(arch), 1)
        self.assertEqual(arch[0], "multiple caching layers")

    def test_dedup_map_records_suppressed_variants(self):
        result = self._tax(["read replica", "read replicas"])
        dedup = result["dedup_map"]
        # Both originals differ from the canonical "database read replicas"
        self.assertEqual(len(dedup), 1)
        canonical = list(dedup.keys())[0]
        self.assertEqual(canonical, "database read replicas")
        # Both "read replica" and "read replicas" are suppressed variants
        self.assertEqual(len(dedup[canonical]), 2)
        self.assertIn("read replica", dedup[canonical])
        self.assertIn("read replicas", dedup[canonical])

    def test_different_arch_phrases_not_deduplicated(self):
        result = self._tax([
            "horizontal scaling of trading servers",
            "Redis caching layer",
            "database read replicas for GET endpoints",
        ])
        self.assertEqual(len(result["arch"]), 3)

    def test_dedup_map_empty_when_no_duplicates(self):
        result = self._tax([
            "horizontal scaling of trading servers",
            "Redis caching",
        ])
        self.assertEqual(result["dedup_map"], {})


# ---------------------------------------------------------------------------
# build_mechanism_taxonomy — WriterPacket backward compat scenario
# ---------------------------------------------------------------------------

class TestBuildMechanismTaxonomyCompat(unittest.TestCase):

    def test_arch_list_is_non_empty_for_pure_arch_input(self):
        """Arch-only inputs produce non-empty arch list — postprocessor will see phrases."""
        phrases = ["horizontal scaling", "Redis caching", "read replicas"]
        result = build_mechanism_taxonomy(phrases)
        self.assertGreater(len(result["arch"]), 0)

    def test_all_buckets_always_present(self):
        result = build_mechanism_taxonomy([])
        self.assertIn("arch", result)
        self.assertIn("strategic", result)
        self.assertIn("operational", result)
        self.assertIn("rejected", result)
        self.assertIn("dedup_map", result)


if __name__ == "__main__":
    unittest.main()

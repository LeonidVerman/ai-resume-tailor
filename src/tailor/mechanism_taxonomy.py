"""Mechanism phrase taxonomy: classification, canonicalization, and deduplication.

Splits a raw pool of mechanism phrases (which may contain arch patterns,
leadership signals, operational signals, and boilerplate) into typed buckets
for use in the WriterPacket.

No LLM is involved — all logic is deterministic keyword/prefix matching.
"""

from __future__ import annotations

import re
from enum import Enum


# ---------------------------------------------------------------------------
# PhraseCategory
# ---------------------------------------------------------------------------

class PhraseCategory(str, Enum):
    ARCH = "arch"
    STRATEGIC = "strategic"
    OPERATIONAL = "operational"
    REJECT = "reject"


# ---------------------------------------------------------------------------
# Classification keyword sets
# ---------------------------------------------------------------------------

# Implementation-level "how" phrases — the core architectural mechanisms that
# belong in bullet-level "via/using/through" clauses.
_ARCH_KEYWORDS: frozenset[str] = frozenset({
    # Spec-defined
    "cache", "caching", "replica", "read replica", "queue", "messaging",
    "async", "horizontal scaling", "horizontally scaled", "sharding",
    "stateless", "idempotent", "idempotency", "retry", "redis",
    "circuit breaker", "throughput", "latency", "load balancing",
    "traffic isolation",
    # From existing _MECHANISM_KEYWORDS (backward compat)
    "asynchronous messaging", "message queue", "message broker",
    "event-driven", "replication", "transactional cache", "ci/cd",
    "containeriz", "database optimization", "api integration",
    "fault tolerance", "distributed system", "kafka", "aws",
    "docker", "kubernetes", "rest api", "restful", "websocket",
    # Additional patterns
    "database read replica", "load", "concurrency", "rate limit",
    "backpressure", "connection pool", "transaction",
})

# Leadership/vision/roadmap/product-alignment signals.
_STRATEGIC_LEADING_VERBS: tuple[str, ...] = (
    "led ", "drove ", "established ", "owned ", "defined ",
    "spearheaded ", "championed ",
)

_STRATEGIC_KEYWORDS: frozenset[str] = frozenset({
    "roadmap", "vision", "strategy", "alignment", "stakeholder",
    "leadership", "product development", "cross-functional",
    "technical vision", "executive", "portfolio", "program management",
    "set direction", "org ", "p&l",
})

# Process/quality/observability/delivery-governance signals.
_OPERATIONAL_KEYWORDS: frozenset[str] = frozenset({
    "agile", "code review", "test coverage", "documentation",
    "observability", "monitor", "monitoring", "operational excellence",
    "operational", "incident response", "on-call", "sla", "slo",
    "runbook", "testing", "coverage", "sprint", "scrum",
    "delivery pipeline", "release process",
})

# Boilerplate capability statements → REJECT (not actionable phrases).
_REJECT_STARTS: tuple[str, ...] = (
    "skilled in", "experienced in", "proficient in",
    "knowledgeable in", "expertise in", "background in",
    "familiar with", "versed in",
)


# ---------------------------------------------------------------------------
# Variant / canonicalization map
# key: normalized-input → canonical preferred output phrase
# ---------------------------------------------------------------------------

_VARIANT_MAP: dict[str, str] = {
    "multi layer caching": "multiple caching layers",
    "multilayer caching": "multiple caching layers",
    "multi-layer caching": "multiple caching layers",
    "async decoupling via queues": "asynchronous messaging",
    "async decoupling": "asynchronous messaging",
    "read replica traffic isolation": "database read replicas",
    "read replica": "database read replicas",
    "read replicas": "database read replicas",
    "horizontal scale": "horizontal scaling",
    "horizontally scale": "horizontal scaling",
    "transactional cache": "transactional caching",
}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_for_dedup(phrase: str) -> str:
    """Stable lowercase key for duplicate detection.

    Does NOT modify the phrase for output — only used for grouping.
    """
    s = phrase.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = s.rstrip(".,;:")
    return s


def _get_canonical_phrase(phrase: str) -> str:
    """Return the preferred canonical output form for a phrase.

    If the normalized form is in the variant map, return the mapped canonical
    (e.g. "read replica" → "database read replicas").  Otherwise return the
    original phrase stripped of leading/trailing whitespace.
    """
    norm = _normalize_for_dedup(phrase)
    return _VARIANT_MAP.get(norm, phrase.strip())


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

def classify_phrase(phrase: str) -> PhraseCategory:
    """Classify *phrase* into ARCH, STRATEGIC, OPERATIONAL, or REJECT.

    Classification order (first match wins):

    1. REJECT  — boilerplate capability statements (starts with "skilled in", etc.)
    2. ARCH    — contains any arch keyword (implementation-level "how" phrase)
    3. STRATEGIC — starts with a leadership verb OR contains a strategic keyword
    4. OPERATIONAL — contains a process/quality/observability keyword
    5. REJECT  — anything unclassified (too generic to enforce)
    """
    p = phrase.strip().lower()
    if not p:
        return PhraseCategory.REJECT

    # 1. Boilerplate → REJECT
    for pat in _REJECT_STARTS:
        if p.startswith(pat):
            return PhraseCategory.REJECT

    # 2. Arch: implementation-level patterns take priority
    for kw in _ARCH_KEYWORDS:
        if kw in p:
            return PhraseCategory.ARCH

    # 3. Strategic: leadership verbs at start, or strategic noun keywords
    for verb in _STRATEGIC_LEADING_VERBS:
        if p.startswith(verb.rstrip()):
            return PhraseCategory.STRATEGIC
    for kw in _STRATEGIC_KEYWORDS:
        if kw in p:
            return PhraseCategory.STRATEGIC

    # 4. Operational: process/quality/delivery keywords
    for kw in _OPERATIONAL_KEYWORDS:
        if kw in p:
            return PhraseCategory.OPERATIONAL

    # 5. Unclassified → REJECT
    return PhraseCategory.REJECT


# ---------------------------------------------------------------------------
# Taxonomy builder
# ---------------------------------------------------------------------------

def build_mechanism_taxonomy(
    raw_phrases: list[str],
    unsafe_nouns: list[str] | None = None,
) -> dict:
    """Classify and deduplicate raw mechanism phrases into typed buckets.

    Algorithm
    ---------
    1. Filter phrases containing unsafe nouns.
    2. Resolve each phrase to its canonical form via the variant map.
    3. Deduplicate by normalized canonical: when two phrases resolve to the
       same normalized string, keep the longer/more specific one and record
       the other in ``dedup_map``.
    4. Classify each surviving canonical phrase into arch / strategic /
       operational / rejected.

    Parameters
    ----------
    raw_phrases:
        Unfiltered list of phrases from profile + plan evidence.
    unsafe_nouns:
        Phrases that must not appear in the output (filtered out if any
        unsafe noun is a substring of a candidate phrase).

    Returns
    -------
    dict with keys:
        ``arch``        — list[str]  strict-enforcement phrases
        ``strategic``   — list[str]  leadership/vision signals
        ``operational`` — list[str]  process/quality signals
        ``rejected``    — list[str]  dropped phrases (audit/debug)
        ``dedup_map``   — dict[str, list[str]]  canonical → suppressed variants
    """
    unsafe_lower = [n.lower() for n in (unsafe_nouns or []) if n]

    # --- Pass 1: group originals by canonical norm ---
    # norm → {"canonical": str, "originals": [str], "first_seen": int}
    groups: dict[str, dict] = {}
    order: list[str] = []  # norms in insertion order

    for phrase in raw_phrases:
        if not phrase or not phrase.strip():
            continue
        if unsafe_lower and any(u in phrase.lower() for u in unsafe_lower):
            continue

        canonical = _get_canonical_phrase(phrase)
        norm = _normalize_for_dedup(canonical)
        original = phrase.strip()

        if norm not in groups:
            groups[norm] = {"canonical": canonical, "originals": [original]}
            order.append(norm)
        else:
            grp = groups[norm]
            # Prefer longer canonical (more specific phrase)
            if len(canonical) > len(grp["canonical"]):
                grp["canonical"] = canonical
            grp["originals"].append(original)

    # --- Build dedup_map: canonical → [suppressed originals] ---
    # An original is "suppressed" when it differs from the chosen canonical.
    dedup_map: dict[str, list[str]] = {}
    for norm in order:
        grp = groups[norm]
        canonical = grp["canonical"]
        canon_lower = canonical.lower()
        suppressed = [o for o in grp["originals"] if o.lower() != canon_lower]
        if suppressed:
            dedup_map[canonical] = suppressed

    # --- Pass 2: classify each canonical ---
    arch: list[str] = []
    strategic: list[str] = []
    operational: list[str] = []
    rejected: list[str] = []

    for norm in order:
        canonical = groups[norm]["canonical"]
        cat = classify_phrase(canonical)
        if cat == PhraseCategory.ARCH:
            arch.append(canonical)
        elif cat == PhraseCategory.STRATEGIC:
            strategic.append(canonical)
        elif cat == PhraseCategory.OPERATIONAL:
            operational.append(canonical)
        else:
            rejected.append(canonical)

    return {
        "arch": arch,
        "strategic": strategic,
        "operational": operational,
        "rejected": rejected,
        "dedup_map": dedup_map,
    }

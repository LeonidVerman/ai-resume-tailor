"""Integration tests: validator correctly counts bullets / applies priority for roles
with trailing location suffixes in their headers.

Scenario: The Phase 2 LLM writes a role header that includes a trailing city
(e.g. "Lead Dev | Corp, Vancouver") while the WriterPacket keys use the
shorter form "Lead Dev | Corp".  After normalization both forms collapse to the
same canonical header, so priority lookups, thin-role detection, and bullet
counts all work correctly.
"""

import pytest

from tailor.phase2_validator import validate_phase2_output


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_resume(role_header: str, bullets: list[str]) -> str:
    bullet_lines = "\n".join(f"- {b}" for b in bullets)
    return (
        "Professional Summary\nExperienced engineer.\n\n"
        f"Experience\n{role_header}\nNov 2020 - Present\n{bullet_lines}\n\n"
        "Technical Skills\nPython, Java"
    )


def _make_packet(
    role_name: str,
    priority: str = "high",
    source_bullets: int = 5,
    source_chars: int = 300,
    must_include_skills: list[str] | None = None,
) -> dict:
    return {
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "must_include_skills": must_include_skills or [],
        "allowed_skill_pool": ["Python", "Java"],
        "unsafe_jd_nouns": [],
        "role_priorities": {role_name: priority},
        "role_source_bullet_counts": {role_name: source_bullets},
        "role_source_char_counts": {role_name: source_chars},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
    }


_DATE = "February 25, 2026"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLocationSuffixRoleMatching:
    """Validator must handle location suffixes transparently."""

    def test_bullet_count_correct_when_header_has_trailing_city(self):
        """4 bullets + trailing city in header → no bullet-count error."""
        resume = _make_resume(
            "Lead Dev | Corp, Vancouver",
            ["Built auth service", "Led team of 5", "Deployed microservices", "Reduced latency"],
        )
        packet = _make_packet("Lead Dev | Corp", priority="high", source_bullets=5)
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)
        # Should be no bullet-count error (4 bullets meets high min of 4)
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors == [], f"Unexpected bullet errors: {bullet_errors}"

    def test_priority_matched_correctly_with_trailing_city(self):
        """High-priority plan role is recognized even with city suffix in header."""
        resume = _make_resume(
            "Senior Engineer | Acme Corp, Toronto",
            ["Built X", "Led Y", "Deployed Z", "Improved W"],
        )
        packet = _make_packet("Senior Engineer | Acme Corp", priority="high", source_bullets=5)
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)
        # No errors expected
        assert report["ok"], f"Unexpected errors: {report['errors']}"

    def test_thin_detection_works_with_trailing_city(self):
        """Source bullet count of 1 → thin_override applied even with city suffix."""
        resume = _make_resume(
            "Freelance Consultant | XYZ Inc, Berlin",
            ["Advised clients"],
        )
        packet = _make_packet(
            "Freelance Consultant | XYZ Inc",
            priority="high",
            source_bullets=1,  # thin
            source_chars=50,
        )
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)
        # Thin override should be applied (warning, not error) and 1 bullet is enough
        thin_warnings = [w for w in report["warnings"] if "thin override" in w.lower()]
        assert thin_warnings, "Expected thin-override warning"
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors == [], f"Unexpected bullet errors after thin override: {bullet_errors}"

    def test_role_parsing_debug_contains_both_raw_and_normalized(self):
        """Debug output shows raw header and its normalized form."""
        resume = _make_resume(
            "Dev | Corp, Vancouver",
            ["Built X", "Led Y", "Did Z", "Shipped W"],
        )
        packet = _make_packet("Dev | Corp", priority="high", source_bullets=5)
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)

        debug = report["stats"]["role_parsing_debug"]
        assert len(debug) >= 1
        entry = debug[0]
        assert "raw" in entry
        assert "normalized" in entry
        assert entry["raw"] == "Dev | Corp, Vancouver"
        assert entry["normalized"] == "Dev | Corp"

    def test_bullet_count_error_when_bullets_short_with_trailing_city(self):
        """High-priority role with only 2 bullets still fails (city suffix doesn't mask error)."""
        resume = _make_resume(
            "Senior Engineer | Acme Corp, Vancouver",
            ["Built X", "Led Y"],  # only 2, need 4 for high
        )
        packet = _make_packet("Senior Engineer | Acme Corp", priority="high", source_bullets=5)
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors, "Expected bullet-count error for under-populated high role"
        assert "2 bullet" in bullet_errors[0]

    def test_pipe_spacing_normalized_in_matching(self):
        """Plan role 'Dev|Corp' (no spaces) matches resume header 'Dev | Corp'."""
        resume = _make_resume(
            "Dev | Corp",
            ["Built X", "Led Y", "Did Z", "Shipped W"],
        )
        packet = _make_packet("Dev|Corp", priority="high", source_bullets=5)
        cover = f"{_DATE}\n\nDear Hiring Manager,"
        report = validate_phase2_output(packet, resume, cover, _DATE)
        bullet_errors = [e for e in report["errors"] if "bullet" in e.lower()]
        assert bullet_errors == [], f"Unexpected bullet errors: {bullet_errors}"

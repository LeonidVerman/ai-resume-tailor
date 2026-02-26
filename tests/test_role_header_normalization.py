"""Unit tests for normalize_role_header()."""

import pytest

from tailor.phase2_validator import normalize_role_header


class TestNormalizeRoleHeader:
    # ------------------------------------------------------------------
    # Basic string normalization
    # ------------------------------------------------------------------

    def test_empty_string_unchanged(self):
        assert normalize_role_header("") == ""

    def test_none_like_empty_unchanged(self):
        # Edge-case: caller passes empty-ish value
        assert normalize_role_header("   ") == ""

    def test_no_pipe_unchanged(self):
        assert normalize_role_header("John Doe") == "John Doe"

    def test_leading_trailing_whitespace_stripped(self):
        assert normalize_role_header("  Dev | Corp  ") == "Dev | Corp"

    def test_multiple_internal_spaces_collapsed(self):
        assert normalize_role_header("Dev  |  Corp") == "Dev | Corp"

    # ------------------------------------------------------------------
    # Pipe spacing normalisation
    # ------------------------------------------------------------------

    def test_missing_spaces_around_pipe_added(self):
        assert normalize_role_header("Dev|Corp") == "Dev | Corp"

    def test_extra_spaces_around_pipe_collapsed(self):
        assert normalize_role_header("Dev   |   Corp") == "Dev | Corp"

    def test_three_segment_pipe_normalised(self):
        assert normalize_role_header("Dev|Corp|2020") == "Dev | Corp | 2020"

    def test_three_segment_with_dates(self):
        result = normalize_role_header("Senior Engineer | Acme Corp | 2020 - Present")
        assert result == "Senior Engineer | Acme Corp | 2020 - Present"

    # ------------------------------------------------------------------
    # Trailing location suffix stripping
    # ------------------------------------------------------------------

    def test_trailing_location_after_last_pipe_stripped(self):
        header = "Lead Dev | GMO Corp, Vancouver"
        assert normalize_role_header(header) == "Lead Dev | GMO Corp"

    def test_trailing_location_with_three_segments(self):
        # The third segment is a date but also has a trailing city
        header = "Dev | Corp | 2020 - 2022, New York"
        assert normalize_role_header(header) == "Dev | Corp | 2020 - 2022"

    def test_comma_before_last_pipe_not_stripped(self):
        # "Corp, LLC" has a comma but it appears BEFORE the last pipe
        header = "Dev | Corp, LLC | 2020"
        assert normalize_role_header(header) == "Dev | Corp, LLC | 2020"

    def test_real_world_gmo_example(self):
        header = "Lead Software Developer/Team Leader | GMO-Z.com Fintech CA, Vancouver"
        expected = "Lead Software Developer/Team Leader | GMO-Z.com Fintech CA"
        assert normalize_role_header(header) == expected

    def test_no_comma_no_stripping(self):
        header = "Senior Engineer | Acme Corp | 2017 - 2020"
        assert normalize_role_header(header) == header

    def test_no_pipe_no_stripping(self):
        # Without a pipe there is no "after last pipe" region to strip
        header = "Freelance Engineer, San Francisco"
        assert normalize_role_header(header) == header

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def test_already_normalized_is_unchanged(self):
        header = "Senior Engineer | Acme Corp"
        assert normalize_role_header(normalize_role_header(header)) == normalize_role_header(header)

    def test_location_stripped_result_is_stable(self):
        header = "Dev | Corp, Vancouver"
        once = normalize_role_header(header)
        twice = normalize_role_header(once)
        assert once == twice

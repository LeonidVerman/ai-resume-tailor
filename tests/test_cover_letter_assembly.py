"""Tests for deterministic cover letter assembly (spec Part 7)."""

import pytest
from backend.app.services.cover_letter_builder import (
    build_cover_letter,
    _extract_cover_letter_body,
    validate_contacts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contacts(email="user@example.com", phone="+1 604 123 4567",
               linkedin=None, location=None):
    return {"email": email, "phone": phone, "linkedin_url": linkedin, "location": location}


# ---------------------------------------------------------------------------
# _extract_cover_letter_body
# ---------------------------------------------------------------------------

class TestExtractCoverLetterBody:
    def test_strips_heading_and_signature(self):
        text = (
            "Jane Smith\n"
            "+1 604 000 0000 | jane@example.com\n\n"
            "April 30, 2026\n\n"
            "Dear Hiring Manager,\n"
            "I am excited to apply.\n"
            "My background aligns well.\n\n"
            "Sincerely,\n"
            "Jane Smith"
        )
        body = _extract_cover_letter_body(text)
        assert body.startswith("Dear Hiring Manager,")
        assert "Sincerely" not in body
        assert "Jane Smith\n+1" not in body

    def test_body_only_input_unchanged(self):
        text = "Dear Hiring Manager,\nI am applying.\nI have experience."
        body = _extract_cover_letter_body(text)
        assert body == text.strip()

    def test_no_dear_returns_full_text(self):
        text = "I am applying for the role.\nMy background is strong."
        body = _extract_cover_letter_body(text)
        assert "I am applying" in body

    def test_sincerely_variant_stripped(self):
        text = "Dear Hiring Manager,\nParagraph one.\nSincerely,\nName"
        body = _extract_cover_letter_body(text)
        assert "Sincerely" not in body


# ---------------------------------------------------------------------------
# build_cover_letter — header / contact line
# ---------------------------------------------------------------------------

class TestBuildCoverLetterHeader:
    def test_correct_name_in_header_and_signature(self):
        letter = build_cover_letter("Jane Smith", _contacts(), "Dear Hiring Manager,\nBody.")
        lines = letter.splitlines()
        assert lines[0] == "Jane Smith"
        assert lines[-1] == "Jane Smith"

    def test_contact_line_phone_and_email(self):
        letter = build_cover_letter(
            "Jane Smith",
            _contacts(phone="+1 604 123 4567", email="jane@example.com"),
            "Dear Hiring Manager,\nBody.",
        )
        assert "+1 604 123 4567 | jane@example.com" in letter

    def test_linkedin_included_when_present(self):
        letter = build_cover_letter(
            "Jane Smith",
            _contacts(linkedin="https://linkedin.com/in/jane"),
            "Dear Hiring Manager,\nBody.",
        )
        assert "https://linkedin.com/in/jane" in letter

    def test_linkedin_omitted_when_null(self):
        letter = build_cover_letter(
            "Jane Smith",
            _contacts(linkedin=None),
            "Dear Hiring Manager,\nBody.",
        )
        assert "linkedin" not in letter.lower()

    def test_no_trailing_separator(self):
        letter = build_cover_letter(
            "Jane Smith",
            _contacts(linkedin=None),
            "Dear Hiring Manager,\nBody.",
        )
        for line in letter.splitlines():
            assert not line.rstrip().endswith("|"), f"Trailing separator in: {line!r}"

    def test_date_present(self):
        from datetime import date
        letter = build_cover_letter("Jane Smith", _contacts(), "Dear Hiring Manager,\nBody.")
        today = date.today()
        assert today.strftime("%B") in letter
        assert str(today.year) in letter

    def test_sincerely_deterministic(self):
        letter = build_cover_letter("Jane Smith", _contacts(), "Dear Hiring Manager,\nBody.")
        assert "Sincerely," in letter
        lines = letter.splitlines()
        sincerely_idx = next(i for i, l in enumerate(lines) if l.strip() == "Sincerely,")
        assert lines[sincerely_idx + 1] == "Jane Smith"


# ---------------------------------------------------------------------------
# No identity leakage
# ---------------------------------------------------------------------------

class TestNoIdentityLeakage:
    def test_leonid_never_appears(self):
        letter = build_cover_letter(
            "Jane Smith",
            _contacts(email="jane@example.com"),
            "Dear Hiring Manager,\nI am excited to apply for this role.",
        )
        assert "Leonid" not in letter
        assert "Verman" not in letter

    def test_name_from_profile_not_llm(self):
        llm_body = "Dear Hiring Manager,\nThis is John Doe writing to you.\nRegards."
        letter = build_cover_letter("Jane Smith", _contacts(), llm_body)
        lines = letter.splitlines()
        assert lines[0] == "Jane Smith"
        assert lines[-1] == "Jane Smith"


# ---------------------------------------------------------------------------
# _validate_contacts_for_generation
# ---------------------------------------------------------------------------

class TestValidateContacts:
    def test_passes_with_email_and_phone(self):
        validate_contacts({"email": "a@b.com", "phone": "+1 604 000 0000"})

    def test_fails_missing_email(self):
        with pytest.raises(ValueError) as exc_info:
            validate_contacts({"email": "", "phone": "+1 604 000 0000"})
        assert "contacts.email" in str(exc_info.value)

    def test_fails_missing_phone(self):
        with pytest.raises(ValueError) as exc_info:
            validate_contacts({"email": "a@b.com", "phone": ""})
        assert "contacts.phone" in str(exc_info.value)

    def test_fails_both_missing(self):
        with pytest.raises(ValueError) as exc_info:
            validate_contacts({})
        assert "contacts.email" in str(exc_info.value)
        assert "contacts.phone" in str(exc_info.value)

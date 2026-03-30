"""
backend/tests/unit/test_resume_parser_service.py

Unit tests for ResumeParserService and helper functions.
No file I/O or DB required — all inputs are constructed in-memory.
"""

import io

import pytest

from backend.app.services.resume_parser_service import (
    ResumeParserService,
    _extract_contacts,
    _extract_name,
    _heuristic_parse,
    extract_text,
)


SAMPLE_TEXT = """\
Alice Johnson
alice@example.com | +1-555-0101 | linkedin.com/in/alicejohnson

Summary
Experienced software engineer with 10 years in fintech.

Experience
Senior Engineer — Acme Corp (2019–present)
- Led microservices migration
"""


class TestExtractContacts:
    def test_email_extracted(self):
        ci = _extract_contacts(SAMPLE_TEXT)
        assert ci.email == "alice@example.com"

    def test_linkedin_extracted(self):
        ci = _extract_contacts(SAMPLE_TEXT)
        assert ci.linkedin_url == "https://linkedin.com/in/alicejohnson"

    def test_github_extracted(self):
        text = "github.com/alice some other text"
        ci = _extract_contacts(text)
        assert ci.github_url == "https://github.com/alice"

    def test_no_contacts(self):
        ci = _extract_contacts("No contact info here at all.")
        assert ci.email is None
        assert ci.linkedin_url is None


class TestExtractName:
    def test_first_line_is_name(self):
        lines = ["Alice Johnson", "alice@example.com", "Summary"]
        assert _extract_name(lines) == "Alice Johnson"

    def test_skips_email_line(self):
        lines = ["alice@example.com", "Bob Smith", "Engineer"]
        assert _extract_name(lines) == "Bob Smith"

    def test_empty_input_returns_unknown(self):
        assert _extract_name([]) == "Unknown"


class TestHeuristicParse:
    def test_name_set(self):
        doc = _heuristic_parse(SAMPLE_TEXT)
        assert doc.name == "Alice Johnson"

    def test_summary_extracted(self):
        doc = _heuristic_parse(SAMPLE_TEXT)
        assert "software engineer" in doc.summary.lower()

    def test_raw_text_preserved(self):
        doc = _heuristic_parse(SAMPLE_TEXT)
        assert "microservices" in doc.raw_text

    def test_experience_empty_list(self):
        # Heuristic parser leaves experience extraction for future work
        doc = _heuristic_parse(SAMPLE_TEXT)
        assert doc.experience == []


class TestResumeParserService:
    def test_parse_raw_text(self):
        svc = ResumeParserService()
        doc = svc.parse_raw_text(SAMPLE_TEXT)
        assert doc.name == "Alice Johnson"
        assert doc.raw_text == SAMPLE_TEXT

    def test_empty_text_returns_unknown(self):
        svc = ResumeParserService()
        # Construct trivial bytes that extract to empty string
        doc = svc.parse(b"   \n  \t  ", "resume.txt")
        assert doc.name == "Unknown"
        assert doc.raw_text == ""

    def test_plain_text_fallback(self):
        text_bytes = SAMPLE_TEXT.encode("utf-8")
        svc = ResumeParserService()
        # .txt is not DOCX/PDF — falls back to text decode
        doc = svc.parse(text_bytes, "resume.txt")
        # Should not raise; name may be "Unknown" or parsed
        assert isinstance(doc.name, str)

    def test_extract_text_txt_filename(self):
        text_bytes = b"John Smith\njohn@example.com\n"
        result = extract_text(text_bytes, "resume.txt")
        assert "John Smith" in result

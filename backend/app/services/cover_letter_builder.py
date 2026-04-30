"""
backend/app/services/cover_letter_builder.py

Pure-Python cover letter assembly — no SQLAlchemy, no FastAPI, no DB imports.
Safe to import in both the backend and the CLI test environment.
"""

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

_SINCERELY_RE = re.compile(r"^sincerely[,.]?\s*$", re.IGNORECASE | re.MULTILINE)
_DEAR_RE = re.compile(r"^dear\b", re.IGNORECASE)

_BODY_FORBIDDEN_RE = re.compile(r"sincerely|leonid verman", re.IGNORECASE)
_CONTACT_IN_BODY_RE = re.compile(
    r"\d{3}[.\-\s]\d{3}[.\-\s]\d{4}"           # phone pattern
    r"|[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}"  # email pattern
    r"|linkedin\.com/in/",
    re.IGNORECASE,
)


def _extract_cover_letter_body(text: str) -> str:
    """Extract Dear-through-body from LLM output, stripping any heading/signature."""
    lines = text.strip().splitlines()
    dear_idx = next(
        (i for i, l in enumerate(lines) if _DEAR_RE.match(l.strip())), 0
    )
    sincerely_idx = next(
        (i for i, l in enumerate(lines) if _SINCERELY_RE.match(l.strip())),
        len(lines),
    )
    return "\n".join(lines[dear_idx:sincerely_idx]).strip()


def _sanitize_cover_letter_body(body: str, candidate_name: str) -> str:
    """Log a warning if forbidden content appears in the LLM body."""
    issues = []
    if _BODY_FORBIDDEN_RE.search(body):
        issues.append("forbidden keyword (Sincerely / legacy name)")
    if _CONTACT_IN_BODY_RE.search(body):
        issues.append("contact info leak (phone/email/linkedin)")
    if issues:
        logger.warning("COVER_LETTER_SANITIZED issues=%s", issues)
    return body


def validate_contacts(contacts: dict) -> None:
    """Raise ValueError when required contact fields are missing.

    Callers in the backend layer should catch this and convert to HTTPException.
    """
    missing = []
    if not (contacts.get("email") or "").strip():
        missing.append("contacts.email")
    if not (contacts.get("phone") or "").strip():
        missing.append("contacts.phone")
    if missing:
        raise ValueError(
            f"Candidate profile is missing required contact fields: {', '.join(missing)}. "
            "Please update your profile before generating."
        )


def build_cover_letter(
    candidate_name: str,
    contacts: dict,
    cover_letter_body: str,
) -> str:
    """Assemble the final cover letter from deterministic header + LLM body.

    Header format:
        <Name>
        <phone> | <email> [| <linkedin>]   (only non-empty values)

        <Date>

    Body: extracted from LLM output (Dear line through last body paragraph).
    Closing: deterministic Sincerely / <Name>.
    """
    body = _extract_cover_letter_body(cover_letter_body)
    body = _sanitize_cover_letter_body(body, candidate_name)

    phone = (contacts.get("phone") or "").strip()
    email = (contacts.get("email") or "").strip()
    linkedin = (contacts.get("linkedin_url") or "").strip()
    contact_parts = [p for p in (phone, email, linkedin) if p]
    contact_line = " | ".join(contact_parts)

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"

    parts = [candidate_name]
    if contact_line:
        parts.append(contact_line)
    parts.extend(["", current_date, "", body, "", "Sincerely,", candidate_name])

    return "\n".join(parts)

"""LinkedIn-specific job scraper.

Uses LinkedIn's public guest API endpoint which returns a rendered HTML
fragment without requiring authentication.  Falls back to a Playwright
stealth render of the original URL when the lightweight path fails.

Selectors validated against the LinkedIn guest API HTML structure
as of early 2026.
"""

import re

import requests
from bs4 import BeautifulSoup

_GUEST_API_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.linkedin.com/jobs/",
}


def _extract_job_id(url: str) -> str:
    """Extract the numeric job ID from a LinkedIn jobs URL.

    Handles these formats:
      /jobs/view/1234567890                        (ID only)
      /jobs/view/some-title-at-company-1234567890  (slug + ID)
      /jobs/search-results/?currentJobId=1234567890 (search results page)
      ?currentJobId=1234567890                     (any page with currentJobId param)
    """
    # Search results / recommendations page: ?currentJobId=<id>
    m = re.search(r"[?&]currentJobId=(\d+)", url)
    if m:
        return m.group(1)
    # Standalone numeric segment immediately after /jobs/view/
    m = re.search(r"/jobs/view/(\d+)(?:[/?]|$)", url)
    if m:
        return m.group(1)
    # Slug+ID format: trailing run of digits after the last hyphen
    m = re.search(r"/jobs/view/[^/?]*?-(\d{7,})(?:[/?]|$)", url)
    if m:
        return m.group(1)
    raise ValueError(f"Cannot extract job ID from LinkedIn URL: {url}")


def _parse_linkedin_html(html: str) -> dict:
    """Parse an HTML response from the LinkedIn guest API.

    The guest endpoint returns an HTML fragment (not a full page) whose
    structure has been stable across the 2024-2026 redesigns:
      - Job title: <h2 class="top-card-layout__title ...">
      - Company:   <a  class="topcard__org-name-link ...">
      - Description: <section class="show-more-less-html">
                       <div class="show-more-less-html__markup">
    """
    soup = BeautifulSoup(html, "html.parser")

    # --- Job title ---
    job_title = "Unknown"
    title_tag = (
        soup.find("h2", class_="top-card-layout__title")
        or soup.find("h1", class_="top-card-layout__title")
    )
    if title_tag:
        job_title = title_tag.get_text(strip=True)

    # --- Company ---
    company = "Unknown"
    company_tag = (
        soup.find("a", class_="topcard__org-name-link")
        or soup.find("span", class_="topcard__org-name-link")  # occasional fallback
    )
    if company_tag:
        company = company_tag.get_text(strip=True)

    # --- Description ---
    description = ""
    desc_section = soup.find("section", class_="show-more-less-html")
    if desc_section:
        # The inner markup div excludes the "Show more" / "Show less" button text
        inner = desc_section.find("div", class_="show-more-less-html__markup")
        description = (inner or desc_section).get_text("\n", strip=True)

    if not description:
        # Fallback: any section whose class list contains "description"
        for sec in soup.find_all("section"):
            if "description" in " ".join(sec.get("class", [])):
                text = sec.get_text("\n", strip=True)
                if len(text) > 100:
                    description = text
                    break

    return {"company": company, "job_title": job_title, "description": description}


def _is_auth_wall(html: str) -> bool:
    """Return True when LinkedIn served a sign-in or authwall page instead of a job."""
    lower = html.lower()
    return any(s in lower for s in (
        "authwall",
        "join linkedin",
        "sign in to linkedin",
        "uas/login",
    )) and len(html) < 20_000


def _is_valid(data: dict) -> bool:
    return bool(
        data
        and (
            data.get("job_title", "Unknown") != "Unknown"
            or len(data.get("description", "")) > 100
        )
    )


def scrape_linkedin(url: str) -> dict:
    """Fetch and extract job data from a LinkedIn job listing.

    Strategy
    --------
    1. LinkedIn guest API via plain HTTP (fastest, no browser required).
    2. Playwright stealth render of the original URL as a fallback.

    Raises RuntimeError if all strategies fail or a sign-in wall is detected.
    """
    job_id = _extract_job_id(url)

    # --- Strategy 1: guest API (no browser) ---
    api_url = _GUEST_API_URL.format(job_id=job_id)
    try:
        resp = requests.get(api_url, headers=_REQUEST_HEADERS, timeout=30)
        if resp.status_code == 200 and len(resp.text) > 500 and not _is_auth_wall(resp.text):
            data = _parse_linkedin_html(resp.text)
            if _is_valid(data):
                return data
    except Exception:
        pass

    # --- Strategy 2: Playwright stealth render ---
    # Local imports avoid a circular dependency: scrape.py imports this module,
    # so we cannot import from scrape.py at module level.
    from tailor.job.wellfound import _get_rendered_html_stealth, _is_bot_blocked  # noqa: PLC0415
    from tailor.job.scrape import extract_job_data_from_html  # noqa: PLC0415

    html = _get_rendered_html_stealth(url)

    if _is_bot_blocked(html) or _is_auth_wall(html):
        raise RuntimeError(
            "LinkedIn requires sign-in to view this job listing.\n\n"
            "Workaround:\n"
            "  • Open the job listing in your browser, copy the full job\n"
            "    description text to a file, then re-run with:\n"
            "      tailor -pd <file>\n"
        )

    # JSON-LD (LinkedIn occasionally embeds it in the fully rendered page)
    data = extract_job_data_from_html(html)
    if _is_valid(data):
        return data

    return _parse_linkedin_html(html)

"""SmartRecruiters ATS job scraper.

Uses SmartRecruiters' public posting API — no browser automation needed.

Supported URL pattern:
    https://jobs.smartrecruiters.com/{Company}/{jobId}-{slug}
"""

import re

import requests
from bs4 import BeautifulSoup

_API_BASE = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{job_id}"

_URL_RE = re.compile(
    r"jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.IGNORECASE
)


def _parse_url(url: str) -> tuple[str, str]:
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"Cannot parse SmartRecruiters URL: {url}")
    return m.group(1), m.group(2)


def scrape_smartrecruiters(url: str) -> dict:
    """Scrape a SmartRecruiters job posting via the public API."""
    company, job_id = _parse_url(url)
    api_url = _API_BASE.format(company=company, job_id=job_id)

    resp = requests.get(api_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    title = data.get("name", "Unknown")
    company_name = data.get("company", {}).get("name") or company

    sections = data.get("jobAd", {}).get("sections") or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        text = sections.get(key, {}).get("text") or ""
        if text:
            html_text = BeautifulSoup(text, "html.parser").get_text(separator="\n").strip()
            if html_text:
                parts.append(html_text)

    description = "\n\n".join(parts)

    return {
        "company": company_name,
        "job_title": title,
        "description": description,
    }

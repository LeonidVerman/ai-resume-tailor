"""Lever ATS job scraper.

Uses Lever's public posting API — no browser automation needed.

Supported URL pattern:
    https://jobs.lever.co/{company}/{posting_id}
"""

import re

import requests
from bs4 import BeautifulSoup


_API_BASE = "https://api.lever.co/v0/postings/{company}/{posting_id}"

_URL_RE = re.compile(
    r"jobs\.lever\.co/([^/?#]+)/([^/?#]+)", re.IGNORECASE
)


def _parse_lever_url(url: str) -> tuple[str, str]:
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"Cannot parse Lever URL: {url}")
    return m.group(1), m.group(2)


def scrape_lever(url: str) -> dict:
    """Scrape a Lever job posting via the public posting API."""
    company_slug, posting_id = _parse_lever_url(url)
    api_url = _API_BASE.format(company=company_slug, posting_id=posting_id)

    resp = requests.get(api_url, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    title = data.get("text", "Unknown")
    # Lever doesn't return company name in the API response; use the URL slug
    company = company_slug

    # Fields are at the top level (not nested under "content")
    desc_html = data.get("description") or data.get("descriptionBody") or ""

    # Append structured lists (requirements, responsibilities, etc.)
    for lst in data.get("lists") or []:
        heading = lst.get("text", "")
        items_html = lst.get("content", "")
        if heading:
            desc_html += f"<p><strong>{heading}</strong></p>{items_html}"
        else:
            desc_html += items_html

    additional = data.get("additional") or ""
    if additional:
        desc_html += additional

    description = BeautifulSoup(desc_html, "html.parser").get_text(separator="\n").strip()

    return {
        "company": company,
        "job_title": title,
        "description": description,
    }

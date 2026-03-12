"""
src/tailor/job/greenhouse.py

Scraper for Greenhouse job board listings.

Greenhouse exposes a public REST API that returns structured JSON — no
browser rendering needed.

Supported URL pattern:
    https://job-boards.greenhouse.io/<board_token>/jobs/<job_id>
"""

import re

import requests
from bs4 import BeautifulSoup


_API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
_URL_RE = re.compile(
    r"greenhouse\.io/([^/]+)/jobs/(\d+)", re.IGNORECASE
)


def scrape_greenhouse(url: str) -> dict:
    """Scrape a Greenhouse job listing via the public API."""
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"Cannot parse Greenhouse URL: {url}")

    board_token, job_id = m.group(1), m.group(2)
    api_url = _API_BASE.format(board=board_token, job_id=job_id)

    response = requests.get(api_url, timeout=30)
    response.raise_for_status()
    data = response.json()

    company = data.get("company_name") or board_token
    job_title = data.get("title", "Unknown")

    content_html = data.get("content", "")
    description = BeautifulSoup(content_html, "html.parser").get_text(separator="\n").strip()

    return {
        "company": company,
        "job_title": job_title,
        "description": description,
    }

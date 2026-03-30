"""
src/tailor/job/greenhouse.py

Scraper for Greenhouse job board listings.

Greenhouse exposes a public REST API that returns structured JSON — no
browser rendering needed.

Supported URL patterns:
    https://job-boards.greenhouse.io/<board_token>/jobs/<job_id>
    https://<company-site>/careers?gh_jid=<job_id>   (embedded Greenhouse board)
"""

import re
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup


_API_BASE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"

# Direct Greenhouse URL: job-boards.greenhouse.io/<board>/jobs/<id>
_DIRECT_URL_RE = re.compile(
    r"greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.IGNORECASE
)

# Embedded board JS tag: boards.greenhouse.io/embed/job_board/js?for=<board>
_EMBED_BOARD_RE = re.compile(
    r"boards\.greenhouse\.io/embed/job_board/js\?for=([\w-]+)", re.IGNORECASE
)


def _board_and_id_from_embed(url: str):
    """Return (board_token, job_id) for an embedded Greenhouse URL.

    Fetches the page and extracts the board token from the embedded JS tag.
    The job ID comes from the ``gh_jid`` query parameter.
    """
    qs = parse_qs(urlparse(url).query)
    job_ids = qs.get("gh_jid", [])
    if not job_ids:
        return None, None
    job_id = job_ids[0]

    html = requests.get(url, timeout=30).text
    m = _EMBED_BOARD_RE.search(html)
    if not m:
        return None, None

    return m.group(1), job_id


def scrape_greenhouse(url: str) -> dict:
    """Scrape a Greenhouse job listing via the public API."""
    m = _DIRECT_URL_RE.search(url)
    if m:
        board_token, job_id = m.group(1), m.group(2)
    else:
        board_token, job_id = _board_and_id_from_embed(url)
        if not board_token:
            raise ValueError(f"Cannot parse Greenhouse URL: {url}")

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

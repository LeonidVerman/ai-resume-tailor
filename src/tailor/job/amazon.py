"""
src/tailor/job/amazon.py

Scraper for amazon.jobs job listings.

Amazon renders the job detail server-side — a plain HTTP GET is sufficient.
The job title is in <h1>, the full description in <div id="job-detail-body">.
"""

import requests
from bs4 import BeautifulSoup


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def scrape_amazon(url: str) -> dict:
    """Scrape a job listing from amazon.jobs and return a data dict."""
    response = requests.get(url, headers=_HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    h1 = soup.find("h1")
    job_title = h1.get_text(strip=True) if h1 else "Unknown"

    body = soup.find(id="job-detail-body")
    description = body.get_text(separator="\n").strip() if body else ""

    return {
        "company": "Amazon",
        "job_title": job_title,
        "description": description,
    }

"""Generic job-listing scraper.

Dispatches to site-specific scrapers when the hostname is recognised;
falls back to a generic Playwright render + JSON-LD extraction otherwise.

To add support for a new site:
  1. Write a scraper function in its own module: ``_scrape_<site>(url) -> dict``
  2. Add an entry to ``_SITE_SCRAPERS``
  3. Add a hostname check to ``_detect_site``
"""

import json
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from tailor.job import JobData
from tailor.job.amazon import scrape_amazon
from tailor.job.greenhouse import scrape_greenhouse
from tailor.job.linkedin import scrape_linkedin
from tailor.job.wellfound import scrape_wellfound


# ---------------------------------------------------------------------------
# Generic HTML utilities
# ---------------------------------------------------------------------------

def get_rendered_html(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")
        html = page.content()
        browser.close()
    return html


def parse_jobposting(data):
    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")

    description_html = data.get("description", "")
    description_soup = BeautifulSoup(description_html, "html.parser")
    description_text = description_soup.get_text(separator="\n")

    return {
        "company": company,
        "job_title": job_title,
        "description": description_text,
    }


def extract_job_data_from_html(html):
    """Extract job data from JSON-LD JobPosting schema embedded in HTML."""
    soup = BeautifulSoup(html, "html.parser")
    script_tags = soup.find_all("script", type="application/ld+json")

    for tag in script_tags:
        try:
            data = json.loads(tag.string)
            # Sometimes JSON-LD is a list
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "JobPosting":
                        return parse_jobposting(item)
            elif data.get("@type") == "JobPosting":
                return parse_jobposting(data)
        except Exception:
            continue

    return {"company": "Unknown", "job_title": "Unknown", "description": ""}


def extract_metadata_from_html(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    script_tag = soup.find("script", type="application/ld+json")
    if not script_tag:
        return {"company": "Unknown", "job_title": "Unknown"}

    data = json.loads(script_tag.string)
    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")
    return {"company": company, "job_title": job_title}


# ---------------------------------------------------------------------------
# Site registry + dispatcher
# ---------------------------------------------------------------------------

# Registry: map site identifier → scraper function.
# Add new entries here when support for additional job boards is needed.
_SITE_SCRAPERS = {
    "amazon": scrape_amazon,
    "greenhouse": scrape_greenhouse,
    "linkedin": scrape_linkedin,
    "wellfound": scrape_wellfound,
}


def _detect_site(url):
    """Return a site identifier for the given job URL, or 'generic'."""
    host = urlparse(url).netloc.lower()
    if "linkedin.com" in host:
        return "linkedin"
    if "wellfound.com" in host or "angel.co" in host:
        return "wellfound"
    if "amazon.jobs" in host:
        return "amazon"
    if "greenhouse.io" in host:
        return "greenhouse"
    # Extend here as new sites are added to _SITE_SCRAPERS
    return "generic"


def scrape_job_url(url) -> JobData:
    """Top-level entry point: scrape a job listing URL from any supported site.

    Dispatches to a site-specific scraper when the URL's hostname is
    recognised; falls back to the generic JSON-LD scraper otherwise.
    Returns a JobData instance.
    """
    site = _detect_site(url)
    scraper = _SITE_SCRAPERS.get(site)
    if scraper:
        data = scraper(url)
    else:
        # Generic path: Playwright render + JSON-LD extraction
        html = get_rendered_html(url)
        data = extract_job_data_from_html(html)

    return JobData(
        company=data.get("company", "Unknown"),
        job_title=data.get("job_title", "Unknown"),
        description=data.get("description", "").strip(),
        source_url=url,
    )

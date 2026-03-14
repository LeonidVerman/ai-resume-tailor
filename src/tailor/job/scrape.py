"""Generic job-listing scraper.

Dispatches to site-specific scrapers when the hostname is recognised;
falls back to a generic Playwright render + JSON-LD extraction otherwise.

To add support for a new site:
  1. Write a scraper function in its own module: ``_scrape_<site>(url) -> dict``
  2. Add an entry to ``_SITE_SCRAPERS``
  3. Add a hostname check to ``_detect_site``
"""

import json
import re
from functools import lru_cache
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
    from playwright_stealth import Stealth
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        Stealth().use_sync(page)
        page.goto(url, timeout=60000)
        try:
            page.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            # Cloudflare challenge or heavy SPA keeps network busy; settle for load state
            page.wait_for_load_state("load", timeout=60000)
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
# Protected-site helpers
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_protected_sites() -> list[dict]:
    """Load config/scrape_protected_sites.json (cached after first call)."""
    from tailor.config import CONFIG_DIR
    path = CONFIG_DIR / "scrape_protected_sites.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f).get("scrape_protected_sites", [])


def _normalize_hostname(url: str) -> str:
    """Return the lowercase hostname, stripping a leading 'www.' if present."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_scrape_protected_host(hostname: str) -> bool:
    """Return True when *hostname* matches a configured protected-site domain.

    Supports exact match and common-subdomain suffix match, e.g.
    ``www.wellfound.com`` matches ``wellfound.com``.
    """
    normalized = hostname.lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    for entry in _load_protected_sites():
        domain = entry.get("domain", "").lower()
        if normalized == domain or normalized.endswith("." + domain):
            return True
    return False


def _get_site_label(hostname: str) -> str | None:
    """Return the human-readable label for *hostname*, or None if not listed."""
    normalized = hostname.lower()
    if normalized.startswith("www."):
        normalized = normalized[4:]
    for entry in _load_protected_sites():
        domain = entry.get("domain", "").lower()
        if normalized == domain or normalized.endswith("." + domain):
            return entry.get("label") or domain
    return None


def get_scrape_failure_message(url: str) -> str:
    """Return the appropriate user-facing scrape-failure message for *url*.

    Returns a protected-site message when the host is in the configured list,
    otherwise a generic message.
    """
    hostname = _normalize_hostname(url)
    label = _get_site_label(hostname)
    if label:
        return (
            f"{label} uses bot protection, so the job description can't be "
            f"scraped from the URL. Please copy and paste the job description manually."
        )
    return (
        "Scraping the job description from the URL failed. "
        "Please copy and paste the job description manually."
    )


# ---------------------------------------------------------------------------
# hiring.cafe scraper (Next.js data API — bypasses Cloudflare)
# ---------------------------------------------------------------------------

def scrape_hiring_cafe(url: str) -> dict:
    """Scrape a hiring.cafe job via its Next.js data endpoint.

    Avoids Cloudflare bot protection by fetching the internal JSON API
    (``/_next/data/{buildId}/viewjob/{jobId}.json``) instead of the rendered
    page.  Uses curl_cffi with Chrome impersonation for TLS fingerprinting.
    """
    from curl_cffi import requests as cf

    job_id = urlparse(url).path.rstrip("/").split("/")[-1]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    # The /api 404 page is served by Next.js directly (not behind CF) and
    # embeds the current buildId we need to construct the data URL.
    probe = cf.get("https://hiring.cafe/api", impersonate="chrome", headers=headers, timeout=15)
    build_id_match = re.search(r'"buildId":"([^"]+)"', probe.text)
    if not build_id_match:
        raise ValueError("Could not determine hiring.cafe Next.js build ID")
    build_id = build_id_match.group(1)

    data_url = f"https://hiring.cafe/_next/data/{build_id}/viewjob/{job_id}.json"
    resp = cf.get(data_url, impersonate="chrome", headers=headers, timeout=15)
    resp.raise_for_status()

    job = resp.json()["pageProps"]["job"]
    ji = job["job_information"]

    title = ji.get("title") or ji.get("job_title_raw") or "Unknown"

    # enriched_company_data.name is cleanest; fall back to company_info.name
    ecd = job.get("enriched_company_data") or {}
    company = ecd.get("name") or (ji.get("company_info") or {}).get("name") or "Unknown"

    description_html = ji.get("description", "")
    description = BeautifulSoup(description_html, "html.parser").get_text(separator="\n")

    return {"company": company, "job_title": title, "description": description}


# ---------------------------------------------------------------------------
# Site registry + dispatcher
# ---------------------------------------------------------------------------

# Registry: map site identifier → scraper function.
# Add new entries here when support for additional job boards is needed.
_SITE_SCRAPERS = {
    "amazon": scrape_amazon,
    "greenhouse": scrape_greenhouse,
    "hiring_cafe": scrape_hiring_cafe,
    "linkedin": scrape_linkedin,
    "wellfound": scrape_wellfound,
}


def _detect_site(url):
    """Return a site identifier for the given job URL, or 'generic'."""
    host = urlparse(url).netloc.lower()
    if "hiring.cafe" in host:
        return "hiring_cafe"
    if "linkedin.com" in host:
        return "linkedin"
    if "wellfound.com" in host or "angel.co" in host:
        return "wellfound"
    if "amazon.jobs" in host:
        return "amazon"
    if "greenhouse.io" in host:
        return "greenhouse"
    # Embedded Greenhouse board: any site with ?gh_jid= query param
    if "gh_jid" in urlparse(url).query:
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

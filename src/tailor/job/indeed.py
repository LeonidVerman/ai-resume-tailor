"""Indeed job listing scraper.

Uses curl_cffi Chrome impersonation to bypass Indeed's bot protection.
Extracts job data from the JSON-LD JobPosting schema embedded in the page.
"""

import json

from bs4 import BeautifulSoup


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def scrape_indeed(url: str) -> dict:
    """Scrape an Indeed job listing via curl_cffi Chrome impersonation.

    Indeed's bot protection blocks plain requests and Playwright without
    proper TLS fingerprinting.  curl_cffi impersonates Chrome at the TLS
    layer, which bypasses the block and returns the full page containing
    a JSON-LD JobPosting block.
    """
    from curl_cffi import requests as cf

    resp = cf.get(url, impersonate="chrome", headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string)
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") == "JobPosting"), None)
            if data and data.get("@type") == "JobPosting":
                company = data.get("hiringOrganization", {}).get("name", "Unknown")
                job_title = data.get("title", "Unknown")
                description_html = data.get("description", "")
                description = BeautifulSoup(description_html, "html.parser").get_text(separator="\n")
                return {"company": company, "job_title": job_title, "description": description}
        except Exception:
            continue

    raise ValueError(f"Could not extract job data from Indeed page: {url}")

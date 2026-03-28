"""Job Bank Canada scraper (jobbank.gc.ca).

The site is static HTML with no JSON-LD.  The job description lives in
a <div id="job-posting-content"> element; title and company are in
known heading/paragraph elements.

Supported URL pattern:
    https://www.jobbank.gc.ca/jobsearch/jobposting/{id}
"""

import requests
from bs4 import BeautifulSoup

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-CA,en;q=0.9",
}


def scrape_jobbank(url: str) -> dict:
    """Scrape a Job Bank Canada posting via plain HTTP."""
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Job title
    job_title = "Unknown"
    h1 = soup.find("h1")
    if h1:
        job_title = h1.get_text(strip=True)

    # Company name — appears in a <span> or <p> near the top of the posting
    company = "Unknown"
    for selector in [
        {"class_": "business-title"},
        {"itemprop": "hiringOrganization"},
    ]:
        tag = soup.find(attrs=selector) or soup.find("span", selector)
        if tag:
            company = tag.get_text(strip=True)
            break
    if company == "Unknown":
        # Fallback: look for the employer section heading
        emp = soup.find("p", class_=lambda c: c and "employer" in c.lower())
        if emp:
            company = emp.get_text(strip=True)

    # Job description — primary content div
    desc_div = (
        soup.find("div", id="job-posting-content")
        or soup.find("article")
        or soup.find("main")
    )
    description = ""
    if desc_div:
        description = desc_div.get_text(separator="\n", strip=True)

    return {
        "company": company,
        "job_title": job_title,
        "description": description,
    }

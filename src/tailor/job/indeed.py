"""Indeed job listing scraper.

Uses curl_cffi Chrome impersonation to bypass Indeed's bot protection.
Extracts job data from the JSON-LD JobPosting schema embedded in the page.

Strategy
--------
1. Extract the ``jk`` job key and build a clean URL with no tracking params.
2. Open a curl_cffi Session (maintains cookies across requests) using a
   specific Chrome version fingerprint (chrome120).
3. Prime the session with a homepage visit so Indeed sets its session cookie.
4. Fetch the clean job URL and extract the JSON-LD JobPosting block.
"""

import json
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup


# Full browser headers Chrome 120 sends on a top-level navigation.
# Sec-Fetch-* and Sec-CH-UA headers are checked by Indeed's bot detection.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="120", "Google Chrome";v="120", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _clean_indeed_url(url: str) -> tuple[str, str]:
    """Return (clean_job_url, base_url) from any Indeed job URL.

    Strips all tracking parameters; keeps only ``jk``.
    Normalises the hostname to ``www.indeed.com`` to avoid regional
    redirect chains that can trigger extra bot checks.
    Raises ``ValueError`` if no ``jk`` parameter is found.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    jk_values = params.get("jk")
    if not jk_values:
        raise ValueError(f"No 'jk' parameter found in Indeed URL: {url}")
    jk = jk_values[0]
    # Keep the original regional hostname (ca.indeed.com, uk.indeed.com, etc.)
    # so the response is in the right locale; fall back to www if empty.
    hostname = parsed.netloc or "www.indeed.com"
    base_url = f"https://{hostname}/"
    clean_url = f"https://{hostname}/viewjob?jk={jk}"
    return clean_url, base_url


def scrape_indeed(url: str) -> dict:
    """Scrape an Indeed job listing via curl_cffi Chrome impersonation.

    Uses a persistent Session so cookies obtained from the homepage visit
    are carried to the job-page request.  This mimics real browser behaviour
    and satisfies Indeed's session-cookie check that causes 401 responses
    when hitting the job URL directly without cookies.
    """
    from curl_cffi import requests as cf

    clean_url, base_url = _clean_indeed_url(url)

    session = cf.Session(impersonate="chrome120")
    session.headers.update(_HEADERS)

    # Prime session cookies with a homepage visit before fetching the job page.
    try:
        session.get(base_url, timeout=15)
    except Exception:
        pass  # cookie priming is best-effort; proceed regardless

    resp = session.get(clean_url, timeout=30)
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

"""Indeed job listing scraper.

Uses curl_cffi Chrome impersonation to bypass Indeed's bot protection.

Strategy (tried in order)
--------------------------
0. ScraperAPI (when ``SCRAPER_API_KEY`` env var is set): routes the
   request through residential proxies that bypass Cloudflare WAF.
   Required on cloud servers (Render, AWS, etc.) where Indeed blocks
   data-centre IP ranges.  Sign up at https://scraperapi.com — the
   free tier gives 1,000 requests/month.
1. RSS feed (``/rss?q=jobkey:<jk>``): RSS readers require no JS or
   cookies, so this endpoint has lighter bot protection than the
   rendered viewjob page.
2. Rendered viewjob page via three-step session priming:
   homepage → search page → job page, accumulating the CTK session
   cookie that Indeed's bot-check requires on job-page requests.
   Multiple extraction strategies are tried: JSON-LD, HTML data
   attributes, OG meta tags.
"""

import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Headers for requests that originate from within the Indeed site.
_SAME_ORIGIN = {
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}


def _clean_indeed_url(url: str) -> tuple[str, str]:
    """Return (clean_job_url, base_url) from any Indeed job URL.

    Strips all tracking parameters; keeps only ``jk``.
    Preserves the regional hostname (ca.indeed.com, uk.indeed.com, etc.).
    Raises ``ValueError`` if no ``jk`` parameter is found.
    """
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    jk_values = params.get("jk")
    if not jk_values:
        raise ValueError(f"No 'jk' parameter found in Indeed URL: {url}")
    jk = jk_values[0]
    hostname = parsed.netloc or "www.indeed.com"
    base_url = f"https://{hostname}/"
    clean_url = f"https://{hostname}/viewjob?jk={jk}"
    return clean_url, base_url


def _extract_job_data(html: str, url: str) -> dict | None:
    """Try multiple extraction strategies from an Indeed page HTML.

    Returns a dict with keys ``company``, ``job_title``, ``description``
    or ``None`` if no usable data was found.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: JSON-LD JobPosting schema
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                data = next((d for d in data if d.get("@type") == "JobPosting"), None)
            if data and data.get("@type") == "JobPosting":
                company = data.get("hiringOrganization", {}).get("name", "")
                job_title = data.get("title", "")
                description_html = data.get("description", "")
                description = BeautifulSoup(description_html, "html.parser").get_text(separator="\n")
                if description.strip():
                    return {
                        "company": company or "Unknown",
                        "job_title": job_title or "Unknown",
                        "description": description,
                    }
        except Exception:
            continue

    # Strategy 2: HTML data attributes (Indeed's React/SPA markup)
    title_el = soup.find("h1", attrs={"data-testid": "jobsearch-JobInfoHeader-title"}) or \
               soup.find("h1", class_=re.compile(r"jobsearch-JobInfoHeader"))
    company_el = soup.find(attrs={"data-testid": "inlineHeader-companyName"}) or \
                 soup.find(attrs={"data-testid": "jobsearch-JobInfoHeader-companyName"})
    desc_el = soup.find("div", id="jobDescriptionText") or \
              soup.find("div", attrs={"data-testid": "jobsearch-jobDescriptionText"})

    if desc_el:
        description = desc_el.get_text(separator="\n", strip=True)
        if description.strip():
            return {
                "company": company_el.get_text(strip=True) if company_el else "Unknown",
                "job_title": title_el.get_text(strip=True) if title_el else "Unknown",
                "description": description,
            }

    # Strategy 3: OG / meta tags (last resort — description will be truncated)
    og_title = soup.find("meta", property="og:title")
    og_desc = soup.find("meta", property="og:description")
    if og_title and og_title.get("content"):
        return {
            "company": "Unknown",
            "job_title": og_title["content"],
            "description": og_desc["content"] if og_desc else f"See full job posting at {url}",
        }

    return None


def _try_rss_feed(hostname: str, jk: str) -> dict | None:
    """Fetch job data via Indeed's RSS feed.

    RSS feeds are consumed by simple HTTP clients with no JS or cookies,
    so Indeed serves them with much lighter bot-protection than the
    rendered viewjob page.  Returns None on any failure so the caller
    can fall back to the page-scraping strategy.
    """
    import httpx

    rss_url = f"https://{hostname}/rss?q=jobkey%3A{quote(jk)}&l="
    try:
        resp = httpx.get(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; RSS reader/2.0)",
                     "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            return None
        item = channel.find("item")
        if item is None:
            return None

        # Title is usually "Job Title - Company Name (City, Province)"
        raw_title = (item.findtext("title") or "").strip()
        job_title = raw_title
        company = (item.findtext("author") or "").strip()

        if not company and " - " in raw_title:
            parts = raw_title.split(" - ", 1)
            job_title = parts[0].strip()
            company_loc = parts[1].strip()
            # Strip trailing "(City, Province)" if present
            company = company_loc.split("(")[0].strip() if "(" in company_loc else company_loc

        desc_html = item.findtext("description") or ""
        description = BeautifulSoup(desc_html, "html.parser").get_text(separator="\n").strip()

        if description:
            return {
                "company": company or "Unknown",
                "job_title": job_title or "Unknown",
                "description": description,
            }
        return None
    except Exception:
        return None


def _try_scraperapi(clean_url: str, api_key: str) -> dict | None:
    """Fetch the Indeed job page via ScraperAPI with JavaScript rendering.

    ScraperAPI routes requests through residential proxies and handles
    Cloudflare challenges, making it reliable from cloud-server IPs.
    Returns ``None`` on any failure so the caller can fall back.
    """
    import httpx

    proxy_url = (
        f"https://api.scraperapi.com/"
        f"?api_key={api_key}&url={quote(clean_url, safe='')}&render=true"
    )
    logger.info("ScraperAPI: fetching %s", clean_url)
    try:
        resp = httpx.get(proxy_url, timeout=60, follow_redirects=True)
        logger.info("ScraperAPI: HTTP %d, html_len=%d", resp.status_code, len(resp.text))
        if resp.status_code != 200:
            logger.warning("ScraperAPI: non-200 response %d — falling back", resp.status_code)
            return None
        result = _extract_job_data(resp.text, clean_url)
        if result is None:
            logger.warning(
                "ScraperAPI: 200 OK but no job data extracted; html_preview=%r",
                resp.text[:300],
            )
        return result
    except Exception as exc:
        logger.warning("ScraperAPI: request failed: %s", exc)
        return None


def scrape_indeed(url: str) -> dict:
    """Scrape an Indeed job listing via curl_cffi Chrome impersonation.

    Builds a three-step session (homepage → search page → job page) to
    accumulate the cookies Indeed's bot-check requires on job-page requests.
    """
    from curl_cffi import requests as cf

    clean_url, base_url = _clean_indeed_url(url)
    hostname = urlparse(clean_url).netloc
    jk = parse_qs(urlparse(clean_url).query)["jk"][0]

    # Strategy 0: ScraperAPI — residential proxies bypass Cloudflare WAF
    scraper_api_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if scraper_api_key:
        logger.info("ScraperAPI key present — attempting Strategy 0")
        result = _try_scraperapi(clean_url, scraper_api_key)
        if result:
            return result
        logger.warning("ScraperAPI strategy failed — falling back to RSS and session priming")
    else:
        logger.debug("SCRAPER_API_KEY not set — skipping ScraperAPI strategy")

    # Strategy 1: RSS feed — lighter bot protection; no JS/cookies required
    result = _try_rss_feed(hostname, jk)
    if result:
        return result

    # Strategy 2: Rendered viewjob page with three-step session priming
    session = cf.Session(impersonate="chrome131")
    session.headers.update(_HEADERS)

    # Step 1: Homepage — sets initial session cookies
    try:
        session.get(base_url, timeout=15)
        time.sleep(1.0)
    except Exception:
        pass

    # Step 2: Search page — sets CTK cookie that Indeed checks on job pages
    search_url = f"https://{hostname}/jobs?q=&l="
    try:
        session.get(search_url, timeout=15, headers={**_SAME_ORIGIN, "Referer": base_url})
        time.sleep(0.5)
    except Exception:
        pass

    # Step 3: Job page — referrer is the search page (same-origin navigation)
    resp = session.get(
        clean_url,
        timeout=30,
        headers={**_SAME_ORIGIN, "Referer": search_url},
    )
    resp.raise_for_status()

    result = _extract_job_data(resp.text, clean_url)
    if result:
        return result

    raise ValueError(f"Could not extract job data from Indeed page: {url}")

"""Ashby ATS job scraper.

Ashby job pages are Next.js apps that embed the full job payload in
__NEXT_DATA__.  We fetch the raw HTML and extract that JSON blob —
no browser needed.

Supported URL pattern:
    https://jobs.ashbyhq.com/{company}/{job_id}
"""

import json
import re

import requests
from bs4 import BeautifulSoup

_NEXT_DATA_RE = re.compile(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_next_data(html: str) -> dict | None:
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def scrape_ashby(url: str) -> dict:
    """Scrape an Ashby job posting from its __NEXT_DATA__ JSON."""
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()

    next_data = _extract_next_data(resp.text)
    if next_data:
        try:
            props = next_data.get("props", {}).get("pageProps", {})
            # Ashby stores the job under jobPosting or job key
            job = props.get("jobPosting") or props.get("job") or {}
            if job:
                title = job.get("title") or job.get("jobTitle") or "Unknown"
                org = (
                    job.get("organization")
                    or job.get("company")
                    or props.get("organization")
                    or {}
                )
                company = (
                    org.get("name")
                    if isinstance(org, dict)
                    else (org or "Unknown")
                )
                desc_html = job.get("descriptionHtml") or job.get("description", "")
                description = BeautifulSoup(desc_html, "html.parser").get_text(separator="\n").strip()
                if description:
                    return {"company": company or "Unknown", "job_title": title, "description": description}
        except Exception:
            pass

    # Fallback: JSON-LD on the page
    from tailor.job.scrape import extract_job_data_from_html
    data = extract_job_data_from_html(resp.text)
    if data.get("description"):
        return data

    # Last resort: render with Playwright
    from tailor.job.wellfound import _get_rendered_html_stealth
    from tailor.job.scrape import extract_job_data_from_html as _extract
    html = _get_rendered_html_stealth(url)
    data = _extract(html)
    if data.get("description"):
        return data

    # Parse visible text from rendered HTML
    soup = BeautifulSoup(html, "html.parser")
    # Try OG tags for title/company
    og_title = soup.find("meta", property="og:title")
    title = og_title["content"] if og_title else "Unknown"
    description = soup.get_text(separator="\n", strip=True)

    return {"company": "Unknown", "job_title": title, "description": description}

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
from tailor.job.ashby import scrape_ashby
from tailor.job.greenhouse import scrape_greenhouse
from tailor.job.indeed import scrape_indeed
from tailor.job.jobbank import scrape_jobbank
from tailor.job.lever import scrape_lever
from tailor.job.linkedin import scrape_linkedin
from tailor.job.smartrecruiters import scrape_smartrecruiters
from tailor.job.wellfound import scrape_wellfound


# ---------------------------------------------------------------------------
# Generic HTML utilities
# ---------------------------------------------------------------------------

def get_rendered_html(url):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        # Playwright not installed — fall back to plain HTTP (used in Docker backend)
        response = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        response.raise_for_status()
        return response.text
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
            # Normalise to a flat list of objects to search
            candidates: list = []
            if isinstance(data, list):
                candidates = data
            elif isinstance(data, dict):
                if data.get("@type") == "JobPosting":
                    candidates = [data]
                elif "@graph" in data:
                    # JSON-LD @graph array (used by Built In, WordPress, etc.)
                    candidates = data["@graph"] if isinstance(data["@graph"], list) else []
            for item in candidates:
                if isinstance(item, dict) and item.get("@type") == "JobPosting":
                    return parse_jobposting(item)
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
# Workable scraper (apply.workable.com job pages)
# ---------------------------------------------------------------------------

def scrape_workable(url: str) -> dict:
    """Scrape a Workable job via the public v2 JSON API.

    Workable's apply pages are SPAs; the job data lives in an undocumented
    but stable v2 endpoint: ``/api/v2/accounts/{slug}/jobs/{shortcode}``.
    Extracts title, company (from the account slug), and assembles a plain-text
    description from the three HTML sections: description, requirements, benefits.
    """
    import httpx

    m = re.search(r'apply\.workable\.com/([^/]+)/j/([A-Za-z0-9]+)', url, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot parse Workable job URL: {url}")
    account_slug, shortcode = m.group(1), m.group(2)

    api_url = f"https://apply.workable.com/api/v2/accounts/{account_slug}/jobs/{shortcode}"
    resp = httpx.get(
        api_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
        timeout=20,
        follow_redirects=True,
    )
    resp.raise_for_status()
    data = resp.json()

    job_title = data.get("title") or None
    # Derive company from the account slug (e.g. "my-company" → "My Company")
    company = account_slug.replace("-", " ").title() if account_slug else None

    # Assemble full description from the three HTML sections
    sections = []
    description_html = data.get("description") or ""
    requirements_html = data.get("requirements") or ""
    benefits_html = data.get("benefits") or ""

    if description_html:
        sections.append(_workable_html_to_text(description_html))
    if requirements_html:
        text = _workable_html_to_text(requirements_html)
        if text:
            sections.append("REQUIREMENTS\n" + text)
    if benefits_html:
        text = _workable_html_to_text(benefits_html)
        if text:
            sections.append("WHY JOIN\n" + text)

    description = "\n\n".join(s for s in sections if s)
    return {"company": company, "job_title": job_title, "description": description}


def _workable_html_to_text(html: str) -> str:
    """Convert Workable HTML job content to plain text with bullet points."""
    # Convert list items to bullets before stripping tags
    html = re.sub(r'<li[^>]*>', '• ', html, flags=re.IGNORECASE)
    html = re.sub(r'</li>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'<[^>]+>', '', html)
    # Decode common HTML entities
    html = html.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    html = html.replace('&#39;', "'").replace('&quot;', '"')
    # Collapse runs of spaces; normalise blank lines
    html = re.sub(r'[ \t]+', ' ', html)
    html = re.sub(r'\n{3,}', '\n\n', html)
    return html.strip()


# ---------------------------------------------------------------------------
# Notion scraper (public notion.site / notion.so pages)
# ---------------------------------------------------------------------------

def scrape_notion(url: str) -> dict:
    """Scrape a public Notion page using the unofficial Notion block API.

    Works for any publicly shared page hosted on notion.site or notion.so.
    Extracts job title from the page title and company from the workspace
    subdomain (e.g. ``uptop.notion.site`` → "Uptop").  Content is assembled
    from the ordered block tree without requiring a browser or Playwright.
    """
    import httpx

    path = urlparse(url).path
    m = re.search(r'([0-9a-f]{32})(?:[/?#]|$)', path, re.IGNORECASE)
    if not m:
        raise ValueError(f"Cannot find Notion page ID in URL: {url}")
    raw_id = m.group(1).lower()
    page_id = f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"

    resp = httpx.post(
        "https://www.notion.so/api/v3/loadPageChunk",
        json={
            "pageId": page_id,
            "limit": 100,
            "cursor": {"stack": []},
            "chunkNumber": 0,
            "verticalColumns": False,
        },
        headers={
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "notion-client-version": "23.13.0",
        },
        timeout=20,
        follow_redirects=True,
    )
    resp.raise_for_status()

    block_map = resp.json().get("recordMap", {}).get("block", {})
    if not block_map:
        raise ValueError("Notion API returned no block data")

    root_v = (block_map.get(page_id, {}).get("value") or {}).get("value") or {}

    # Page title → job title
    title_rt = (root_v.get("properties") or {}).get("title", [])
    job_title = _notion_rich_text(title_rt).strip() or None

    # Workspace subdomain → company (e.g. "uptop.notion.site" → "Uptop")
    host = urlparse(url).netloc.lower()
    company: str | None = None
    if host.endswith(".notion.site"):
        sub = host[: -len(".notion.site")]
        if sub and sub != "www":
            company = sub.replace("-", " ").title()

    # Walk blocks in content order
    content_ids = root_v.get("content") or []
    lines = _notion_walk_blocks(content_ids, block_map, depth=0)
    description = "\n".join(lines).strip()

    return {"company": company, "job_title": job_title, "description": description}


def _notion_rich_text(segments: list) -> str:
    """Convert a Notion rich-text array to plain text (strips inline styles)."""
    parts = []
    for seg in segments or []:
        if isinstance(seg, list) and seg:
            parts.append(str(seg[0]))
    return "".join(parts)


def _notion_walk_blocks(ids: list, block_map: dict, depth: int) -> list[str]:
    """Recursively convert an ordered list of Notion block IDs to text lines."""
    HEADING_TYPES = {"header", "sub_header", "sub_sub_header"}
    LIST_TYPES = {"bulleted_list", "numbered_list"}
    PASSTHROUGH_TYPES = {"column_list", "column", "toggle"}
    SKIP_TYPES = {
        "page", "divider", "image", "video", "embed", "file",
        "bookmark", "collection_view", "table_of_contents",
    }

    lines: list[str] = []
    for bid in ids:
        v = (block_map.get(bid, {}).get("value") or {}).get("value") or {}
        btype = v.get("type", "")
        props = v.get("properties") or {}
        text = _notion_rich_text(props.get("title", [])).strip()
        children = v.get("content") or []

        if btype in SKIP_TYPES:
            pass
        elif btype in PASSTHROUGH_TYPES:
            if children:
                lines.extend(_notion_walk_blocks(children, block_map, depth))
            continue
        elif btype in HEADING_TYPES:
            if text:
                lines.append("")
                lines.append(text.upper())
        elif btype == "bulleted_list":
            indent = "  " * depth
            if text:
                lines.append(f"{indent}• {text}")
        elif btype == "numbered_list":
            indent = "  " * depth
            if text:
                lines.append(f"{indent}{text}")
        elif btype in {"to_do"}:
            if text:
                checked = (props.get("checked") or [[""]])[0][0]
                box = "☑" if checked == "Yes" else "☐"
                lines.append(f"  {box} {text}")
        elif btype == "quote":
            if text:
                lines.append(f"> {text}")
        elif btype in {"callout", "text", "paragraph"}:
            if text:
                lines.append(text)
        elif text:
            lines.append(text)

        if children and btype not in SKIP_TYPES:
            lines.extend(_notion_walk_blocks(children, block_map, depth + 1))

    return lines


# ---------------------------------------------------------------------------
# Site registry + dispatcher
# ---------------------------------------------------------------------------

# Registry: map site identifier → scraper function.
# Add new entries here when support for additional job boards is needed.
_SITE_SCRAPERS = {
    "amazon": scrape_amazon,
    "ashby": scrape_ashby,
    "greenhouse": scrape_greenhouse,
    "hiring_cafe": scrape_hiring_cafe,
    "indeed": scrape_indeed,
    "jobbank": scrape_jobbank,
    "lever": scrape_lever,
    "linkedin": scrape_linkedin,
    "notion": scrape_notion,
    "smartrecruiters": scrape_smartrecruiters,
    "wellfound": scrape_wellfound,
    "workable": scrape_workable,
}


def _detect_site(url):
    """Return a site identifier for the given job URL, or 'generic'."""
    host = urlparse(url).netloc.lower()
    if "hiring.cafe" in host:
        return "hiring_cafe"
    if "indeed.com" in host:
        return "indeed"
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
    if "jobs.lever.co" in host:
        return "lever"
    if "jobs.ashbyhq.com" in host:
        return "ashby"
    if "jobs.smartrecruiters.com" in host:
        return "smartrecruiters"
    if "jobbank.gc.ca" in host:
        return "jobbank"
    if "notion.site" in host or "notion.so" in host:
        return "notion"
    if "apply.workable.com" in host:
        return "workable"
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
        # Generic path: Playwright render + JSON-LD extraction.
        # If the initial render is bot-blocked (Cloudflare, DataDome), retry
        # with the stealth renderer used for Wellfound/LinkedIn.
        from tailor.job.wellfound import _get_rendered_html_stealth, _is_bot_blocked
        html = get_rendered_html(url)
        if _is_bot_blocked(html):
            html = _get_rendered_html_stealth(url)
        data = extract_job_data_from_html(html)

    return JobData(
        company=data.get("company", "Unknown"),
        job_title=data.get("job_title", "Unknown"),
        description=data.get("description", "").strip(),
        source_url=url,
    )

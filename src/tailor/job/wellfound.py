"""WellFound-specific job scraper.

Tries four extraction strategies in order of reliability:
  1. JSON-LD JobPosting schema  (via scrape.extract_job_data_from_html)
  2. Next.js __NEXT_DATA__ server-side props
  3. Apollo GraphQL __APOLLO_STATE__ cache
  4. DOM / Open Graph meta fallback
"""

import json
import re

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = window.chrome || {runtime: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
const _origPermQuery = navigator.permissions && navigator.permissions.query.bind(navigator.permissions);
if (_origPermQuery) {
  navigator.permissions.query = (p) =>
    p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _origPermQuery(p);
}
"""

_STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _get_rendered_html_stealth(url):
    """Render a page with lightweight anti-bot-detection measures.

    Overrides the most common JavaScript fingerprinting signals
    (navigator.webdriver, chrome object, plugins, permissions) and uses a
    realistic desktop user-agent.

    Channel priority:
      1. 'chrome' — the locally installed Google Chrome binary.  Chrome uses
         BoringSSL whose TLS fingerprint (JA3) matches real-user traffic more
         closely than the bundled Playwright Chromium, which helps against
         TLS-level bot detection (e.g. DataDome, Cloudflare).
      2. Default Playwright Chromium — fallback if Chrome is not installed.
    """
    def _launch(p, channel=None):
        launch_kwargs = dict(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        if channel:
            launch_kwargs["channel"] = channel
        browser = p.chromium.launch(**launch_kwargs)
        ctx = browser.new_context(
            user_agent=_STEALTH_UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        ctx.add_init_script(_STEALTH_INIT_SCRIPT)
        page = ctx.new_page()
        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")
        html = page.content()
        browser.close()
        return html

    with sync_playwright() as p:
        try:
            return _launch(p, channel="chrome")
        except Exception:
            # Chrome not installed or launch failed — fall back to bundled Chromium
            return _launch(p, channel=None)


def _is_bot_blocked(html):
    """Return True when the response looks like a bot-detection challenge page.

    Checks page size and known signals from Cloudflare and DataDome rather
    than the real job listing content.
    """
    if len(html) < 5000:
        return True
    lower = html.lower()
    signals = (
        "datadome",
        "geo.captcha-delivery.com",
        "cf-mitigated",
        "just a moment",           # Cloudflare JS challenge
        "enable javascript and cookies",
        "checking your browser",
    )
    return any(s in lower for s in signals)


def _is_valid_job_data(data):
    """Return True when a scraped result contains meaningful job data."""
    return bool(
        data
        and (
            data.get("job_title", "Unknown") != "Unknown"
            or len(data.get("description", "")) > 100
        )
    )


def _extract_next_data(html):
    """Extract job data from a Next.js __NEXT_DATA__ JSON block.

    WellFound (and other Next.js-based job boards) embed the full server-side
    rendered props in a <script id="__NEXT_DATA__"> tag.  Job info is typically
    found under props.pageProps.jobListing or props.pageProps.job.
    """
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        nd = json.loads(m.group(1))
    except Exception:
        return None

    page_props = nd.get("props", {}).get("pageProps", {})

    # WellFound: props.pageProps.jobListing  (or .job)
    jl = page_props.get("jobListing") or page_props.get("job")
    if isinstance(jl, dict):
        title = jl.get("title") or jl.get("name") or "Unknown"
        raw_desc = jl.get("description") or ""
        description = BeautifulSoup(raw_desc, "html.parser").get_text("\n")
        startup = jl.get("startup") or jl.get("company") or {}
        company = (
            startup.get("name") if isinstance(startup, dict) else str(startup)
        ) or "Unknown"
        return {"company": company, "job_title": title, "description": description}

    return None


def _extract_apollo_state(html):
    """Extract job data from a window.__APOLLO_STATE__ GraphQL cache block.

    Apollo Client stores its normalized cache in window.__APOLLO_STATE__.
    Entries for job listings typically have keys like 'JobListing:3288683'.
    Company references use __ref pointers into the same cache.
    """
    m = re.search(
        r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;",
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        state = json.loads(m.group(1))
    except Exception:
        return None

    for key, val in state.items():
        if not isinstance(val, dict):
            continue
        if not any(k in key for k in ("JobListing", "JobPosting", "Job:")):
            continue

        title = val.get("title") or val.get("name") or "Unknown"
        raw_desc = val.get("description") or ""
        description = BeautifulSoup(raw_desc, "html.parser").get_text("\n")

        # Company may be an inline dict or a __ref into the cache
        startup = val.get("startup") or val.get("company") or {}
        if isinstance(startup, dict):
            ref = startup.get("__ref")
            company = state.get(ref, {}).get("name") if ref else startup.get("name")
            company = company or "Unknown"
        else:
            company = "Unknown"

        if title != "Unknown" or description:
            return {"company": company, "job_title": title, "description": description}

    return None


def _extract_dom_wellfound(html):
    """Extract job data from WellFound DOM elements as a last-resort fallback.

    Tries Open Graph meta tags first (most reliable when present), then falls
    back to the page <title> and visible paragraph text for the description.
    """
    soup = BeautifulSoup(html, "html.parser")

    company = "Unknown"
    job_title = "Unknown"

    # og:title is typically "Job Title at Company" on WellFound
    og_title = soup.find("meta", property="og:title")
    if og_title:
        t = og_title.get("content", "")
        for sep in (" at ", " @ "):
            if sep in t:
                parts = t.split(sep, 1)
                job_title = parts[0].strip()
                company = parts[1].strip()
                break
        if job_title == "Unknown":
            job_title = t.strip()

    # Fall back to page <title>: "Job Title at Company | Wellfound"
    if job_title == "Unknown":
        title_tag = soup.find("title")
        if title_tag:
            t = title_tag.get_text(strip=True)
            for sep in (" at ", " @ ", " - "):
                if sep in t:
                    parts = t.split(sep, 1)
                    job_title = parts[0].strip()
                    company = parts[1].split("|")[0].strip()
                    break

    # h1 often has the cleanest job title
    h1 = soup.find("h1")
    if h1:
        job_title = h1.get_text(strip=True) or job_title

    # og:description is sometimes a truncated job summary
    og_desc = soup.find("meta", property="og:description")
    description = og_desc.get("content", "") if og_desc else ""

    # Try common description container selectors
    if not description:
        for selector in (
            {"attrs": {"data-test": "JobDescription"}},
            {"class_": lambda c: c and "description" in " ".join(c).lower()},
        ):
            cand = soup.find("div", **selector)
            if cand:
                text = cand.get_text("\n", strip=True)
                if len(text) > 100:
                    description = text
                    break

    # Last resort: long <p> tags
    if not description:
        description = "\n".join(
            p.get_text(" ", strip=True)
            for p in soup.find_all("p")
            if len(p.get_text(strip=True)) > 50
        )

    return {"company": company, "job_title": job_title, "description": description}


def scrape_wellfound(url):
    """Fetch and extract job data from a WellFound job listing page.

    Raises RuntimeError when bot-detection blocks the page, with a message
    that explains the cause and suggests the manual paste-text fallback.
    """
    # Local import avoids a circular dependency with scrape.py
    from tailor.job.scrape import extract_job_data_from_html

    html = _get_rendered_html_stealth(url)

    if _is_bot_blocked(html):
        raise RuntimeError(
            "WellFound blocked the automated request (Cloudflare / DataDome "
            "bot protection is active).\n\n"
            "Workarounds:\n"
            "  • Open the job listing in your browser, select all text on the\n"
            "    page, copy it, then re-run this tool and paste when prompted\n"
            "    (press Enter twice to finish).\n"
            "  • Install Google Chrome so that tailor can use its TLS\n"
            "    fingerprint instead of the bundled Playwright Chromium:\n"
            "    https://www.google.com/chrome/\n"
        )

    for extractor in (
        extract_job_data_from_html,
        _extract_next_data,
        _extract_apollo_state,
        _extract_dom_wellfound,
    ):
        try:
            result = extractor(html)
        except Exception:
            continue
        if _is_valid_job_data(result):
            return result

    return {"company": "Unknown", "job_title": "Unknown", "description": ""}

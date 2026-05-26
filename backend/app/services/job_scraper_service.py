"""
backend/app/services/job_scraper_service.py

Job scraper service — fetches a job posting URL and returns structured data.

Strategy
--------
1. Use the existing generator's scrape_job_url() when possible (it already
   handles LinkedIn, Wellfound, and generic JSON-LD extraction via Playwright).
2. Fall back to a plain HTTP fetch (httpx) when the generator scraper fails,
   e.g. because Playwright is not installed in the backend environment.
   The HTTP fallback tries, in order:
     a. JSON-LD JobPosting structured data
     b. Next.js __NEXT_DATA__ page props (covers HiringCafe and similar SPAs)
     c. Open Graph meta tags for title/company
     d. Full visible-text extraction
3. Fall back to the backend ScrapingClient if provided.

The existing generator's scrape.py is NOT modified.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


@dataclass
class JobScrapedData:
    """Raw scraping output before normalization."""
    url: str
    company: str | None
    job_title: str | None
    raw_text: str
    source: str  # "generator" | "http_fallback" | "scraping_client"


class JobScraperService:
    """
    Fetch a job posting URL and return raw structured data.

    Dependencies
    ------------
    scraping_client: optional ScrapingClient — used as last-resort fallback.
    """

    def __init__(self, scraping_client=None) -> None:
        self._scraping_client = scraping_client

    def scrape(self, url: str) -> JobScrapedData:
        """
        Scrape a job listing URL.

        Tries the existing generator's scrape_job_url() first (supports
        LinkedIn, Wellfound, generic JSON-LD via Playwright).  When that
        fails (e.g. Playwright not available in the backend container),
        falls back to a plain HTTP fetch with multi-strategy extraction.
        """
        logger.info("Scraping job URL: %s", url)

        # ── Primary: generator scraper (Playwright-based) ──────────────────
        try:
            from tailor.job.scrape import scrape_job_url
            job_data = scrape_job_url(url)
            logger.debug(
                "Generator scraper succeeded: company=%r title=%r",
                job_data.company, job_data.job_title,
            )
            if not job_data.description.strip():
                raise ValueError("Generator scraper returned empty description")
            return JobScrapedData(
                url=url,
                company=job_data.company if job_data.company != "Unknown" else None,
                job_title=job_data.job_title if job_data.job_title != "Unknown" else None,
                raw_text=job_data.description,
                source="generator",
            )
        except Exception as exc:
            logger.warning("Generator scraper failed for %s: %s", url, exc, exc_info=True)

        # ── Fallback 1: plain HTTP fetch (no Playwright required) ──────────
        try:
            result = _http_scrape(url)
            logger.info(
                "HTTP fallback succeeded for %s: company=%r title=%r text_len=%d",
                url, result.company, result.job_title, len(result.raw_text),
            )
            return result
        except Exception as exc:
            logger.warning("HTTP fallback failed for %s: %s", url, exc, exc_info=True)

        # ── Fallback 2: optional ScrapingClient ────────────────────────────
        if self._scraping_client is not None:
            result = self._scraping_client.fetch(url)
            if not result.ok:
                raise RuntimeError(
                    f"Failed to fetch job URL {url}: HTTP {result.status_code}"
                )
            logger.debug("ScrapingClient fallback succeeded, html_len=%d", len(result.html))
            raw_text = _extract_text_from_html(result.html)
            return JobScrapedData(
                url=result.url,
                company=None,
                job_title=None,
                raw_text=raw_text,
                source="scraping_client",
            )

        raise RuntimeError(
            f"Cannot scrape {url}: all scraping strategies failed"
        )


# ── HTTP fallback helpers ──────────────────────────────────────────────────


def _http_scrape(url: str) -> JobScrapedData:
    """
    Fetch ``url`` with a plain HTTP GET and extract job content.

    Extraction order:
      1. JSON-LD JobPosting structured data  (inline, no tailor dep)
      2. Next.js __NEXT_DATA__ page props    (HiringCafe and similar SSR SPAs)
      3. Open Graph / meta tags + full visible text (catch-all for SSR pages)
      4. ScraperAPI JS render (when SCRAPER_API_KEY is set and step 3 yielded
         thin text OR no company — handles React/Vue SPAs like HiBob and
         server-partial pages like Oracle Taleo ATS)
    """
    import os

    import httpx

    response = httpx.get(url, headers=_HTTP_HEADERS, follow_redirects=True, timeout=30)
    response.raise_for_status()
    html = response.text
    final_url = str(response.url)

    # 1. JSON-LD JobPosting — inline extraction, no tailor/bs4 dependency
    try:
        data = _extract_jsonld(html)
        if data.get("description"):
            return JobScrapedData(
                url=final_url,
                company=data["company"] if data["company"] != "Unknown" else None,
                job_title=data["job_title"] if data["job_title"] != "Unknown" else None,
                raw_text=data["description"],
                source="http_fallback",
            )
    except Exception:
        pass

    # 2. Next.js __NEXT_DATA__ — regex-based, no bs4 required
    try:
        m = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        if m:
            page_data = json.loads(m.group(1))
            company, job_title, description = _extract_from_next_data(page_data)
            if description:
                return JobScrapedData(
                    url=final_url,
                    company=company,
                    job_title=job_title,
                    raw_text=description,
                    source="http_fallback",
                )
    except Exception:
        pass

    # 3. Open Graph meta + full visible text (good for SSR pages without JSON-LD)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        og_title = _og_meta(soup, "og:title") or _meta(soup, "title")
        og_site = _og_meta(soup, "og:site_name")
        raw_text = _extract_text_from_html(html)
    except ImportError:
        og_title = og_site = None
        raw_text = re.sub(r"<[^>]+>", " ", html)

    # Return only when both text and company are present.  When company is
    # absent the page is likely a JS SPA whose content has not yet loaded;
    # fall through to ScraperAPI (step 4) to get the fully-rendered DOM.
    if len(raw_text.strip()) >= 300 and og_site:
        return JobScrapedData(
            url=final_url,
            company=og_site,
            job_title=og_title or None,
            raw_text=raw_text,
            source="http_fallback",
        )

    # 4. ScraperAPI JS render — triggered when step 3 produced thin text OR no
    #    company (e.g. Oracle Taleo shell has nav-chrome text but no og:site_name;
    #    HiBob shell is a pure SPA with minimal text and generic og:title).
    scraper_api_key = os.environ.get("SCRAPER_API_KEY", "").strip()
    if scraper_api_key:
        try:
            from urllib.parse import quote as _quote
            proxy_url = (
                f"https://api.scraperapi.com/"
                f"?api_key={scraper_api_key}&url={_quote(url, safe='')}&render=true"
            )
            logger.info("HTTP fallback: trying ScraperAPI render for SPA: %s", url)
            spa_resp = httpx.get(proxy_url, timeout=60, follow_redirects=True)
            if spa_resp.status_code == 200:
                rendered_html = spa_resp.text

                # Try JSON-LD from the fully-rendered page first.  Oracle Taleo and
                # similar ATSes inject JSON-LD only after JS execution.
                jld = _extract_jsonld(rendered_html)
                if jld.get("description"):
                    logger.info("ScraperAPI: JSON-LD found in rendered HTML")
                    return JobScrapedData(
                        url=final_url,
                        company=jld["company"] if jld["company"] != "Unknown" else None,
                        job_title=jld["job_title"] if jld["job_title"] != "Unknown" else None,
                        raw_text=jld["description"],
                        source="http_fallback",
                    )

                # Fall back to visible text + metadata re-extracted from the
                # rendered DOM.  JS hydration updates og:title and h1 with the
                # real job title (e.g. HiBob shell shows "Careers"; rendered
                # page shows the actual role name).
                spa_text = _extract_text_from_html(rendered_html)
                if len(spa_text.strip()) >= 300:
                    rendered_company, rendered_title = _extract_rendered_meta(
                        rendered_html, og_site, og_title
                    )
                    logger.info("ScraperAPI SPA render succeeded, text_len=%d", len(spa_text))
                    return JobScrapedData(
                        url=final_url,
                        company=rendered_company,
                        job_title=rendered_title,
                        raw_text=spa_text,
                        source="http_fallback",
                    )
        except Exception as exc:
            logger.warning("ScraperAPI SPA render failed for %s: %s", url, exc)

    # Fall through: return whatever step 3 produced (may be partial).
    if raw_text.strip():
        return JobScrapedData(
            url=final_url,
            company=og_site or None,
            job_title=og_title or None,
            raw_text=raw_text,
            source="http_fallback",
        )

    raise RuntimeError(f"No text content found at {url}")


def _h1_text(soup) -> str | None:
    """Return the text of the first non-empty <h1>, or None."""
    h1 = soup.find("h1")
    return h1.get_text(strip=True) if h1 else None


def _extract_rendered_meta(
    rendered_html: str,
    fallback_company: str | None,
    fallback_title: str | None,
) -> tuple[str | None, str | None]:
    """Extract company and title from a fully JS-rendered page.

    Checks og:site_name, og:title, and <h1> in the rendered DOM.
    Falls back to the plain-HTTP values when nothing better is found.
    """
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(rendered_html, "html.parser")
        company = _og_meta(soup, "og:site_name") or fallback_company
        title = (
            _og_meta(soup, "og:title")
            or _h1_text(soup)
            or _meta(soup, "title")
            or fallback_title
        )
        return company, title
    except Exception:
        return fallback_company, fallback_title


def _extract_jsonld(html: str) -> dict:
    """Extract job data from JSON-LD JobPosting tags (no external deps)."""
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") == "JobPosting":
                    desc_html = item.get("description", "")
                    desc = re.sub(r"<[^>]+>", " ", desc_html).strip()
                    company = (item.get("hiringOrganization") or {}).get("name", "Unknown")
                    return {"company": company, "job_title": item.get("title", "Unknown"), "description": desc}
        except Exception:
            continue
    return {"company": "Unknown", "job_title": "Unknown", "description": ""}


def _extract_from_next_data(page_data: dict) -> tuple[str | None, str | None, str]:
    """
    Recursively search Next.js __NEXT_DATA__ props for job fields.

    Returns (company, job_title, description).
    """
    # Common field names used by job boards
    TITLE_KEYS = {"title", "jobTitle", "job_title", "position", "role", "name"}
    COMPANY_KEYS = {"company", "companyName", "company_name", "employer", "organization"}
    DESC_KEYS = {
        "description", "jobDescription", "job_description", "body",
        "fullDescription", "full_description",
    }

    def _search(obj, depth=0):
        if depth > 10 or not isinstance(obj, (dict, list)):
            return None, None, ""
        if isinstance(obj, list):
            for item in obj:
                c, t, d = _search(item, depth + 1)
                if d:
                    return c, t, d
            return None, None, ""

        company = next((str(obj[k]) for k in COMPANY_KEYS if k in obj and obj[k]), None)
        job_title = next((str(obj[k]) for k in TITLE_KEYS if k in obj and obj[k]), None)
        description = next((str(obj[k]) for k in DESC_KEYS if k in obj and obj[k]), "")

        if description:
            # Strip HTML tags that may be embedded in the description
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s{3,}", "\n\n", description).strip()
            return company, job_title, description

        for v in obj.values():
            c, t, d = _search(v, depth + 1)
            if d:
                return c or company, t or job_title, d

        return None, None, ""

    props = page_data.get("props", page_data)
    return _search(props)


def _og_meta(soup, property: str) -> str | None:
    tag = soup.find("meta", property=property)
    return tag["content"].strip() if tag and tag.get("content") else None


def _meta(soup, name: str) -> str | None:
    tag = soup.find("meta", attrs={"name": name})
    if tag and tag.get("content"):
        return tag["content"].strip()
    tag = soup.find("title")
    return tag.get_text().strip() if tag else None


def _extract_text_from_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style noise
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(separator="\n")
    except ImportError:
        return re.sub(r"<[^>]+>", " ", html)

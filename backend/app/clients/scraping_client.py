"""
backend/app/clients/scraping_client.py

Generic HTTP / scraping client for job page retrieval.

Responsibilities
----------------
- fetch raw HTML from a URL using either httpx (lightweight) or
  Playwright (JavaScript-rendered pages)
- normalize fetch errors
- return a simple ScrapeResult with status, content, and metadata

Design notes
------------
The existing generator (src/tailor/job/scrape.py) already has working
Playwright-based fetching (get_rendered_html) and job data extraction.
This backend client is a lower-level transport layer:

  - get()       → simple HTTP fetch via httpx (no JS)
  - get_rendered() → Playwright headless fetch (JS-rendered SPA pages)

Board-specific parsing (JSON-LD, Next.js data, Apollo cache, etc.) belongs
in a future job_scraper_service, not here.

The existing generator's scrape.py remains unchanged and is not replaced
by this client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Default request headers for non-Playwright fetches
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ai-resume-tailor-bot/1.0; "
        "+https://github.com/LeonidVerman/ai-resume-tailor)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    url: str
    status_code: int
    html: str
    # True if the page was rendered via Playwright (JavaScript executed)
    js_rendered: bool = False
    # HTTP response headers (plain HTTP fetches only)
    response_headers: dict = field(default_factory=dict)
    # Any error message if the fetch partially failed
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.error is None

    @property
    def text(self) -> str:
        """Alias for html — convenience property."""
        return self.html


# ── Client ─────────────────────────────────────────────────────────────────

class ScrapingClient:
    """
    Low-level HTTP/Playwright scraping client.

    Parameters
    ----------
    timeout:
        Request timeout in seconds (plain HTTP).
    playwright_timeout:
        Playwright navigation timeout in milliseconds.
    extra_headers:
        Additional HTTP headers merged into the defaults.
    """

    def __init__(
        self,
        timeout: float = 20.0,
        playwright_timeout: int = 60_000,
        extra_headers: dict | None = None,
    ) -> None:
        self._timeout = timeout
        self._playwright_timeout = playwright_timeout
        self._headers = {**_DEFAULT_HEADERS, **(extra_headers or {})}

    # ── Plain HTTP (httpx) ─────────────────────────────────────────────────

    def get(self, url: str, *, follow_redirects: bool = True) -> ScrapeResult:
        """
        Fetch a URL via plain HTTP using httpx.

        Suitable for server-rendered pages or APIs.
        Falls back gracefully on network errors.
        """
        try:
            import httpx
        except ImportError:
            raise ImportError("httpx is required for plain HTTP fetches: pip install httpx")

        logger.debug("HTTP GET %s", url)
        try:
            with httpx.Client(
                headers=self._headers,
                timeout=self._timeout,
                follow_redirects=follow_redirects,
            ) as client:
                response = client.get(url)
                logger.debug("HTTP %d %s", response.status_code, url)
                return ScrapeResult(
                    url=str(response.url),
                    status_code=response.status_code,
                    html=response.text,
                    js_rendered=False,
                    response_headers=dict(response.headers),
                )
        except Exception as exc:
            logger.warning("HTTP GET failed for %s: %s", url, exc)
            return ScrapeResult(
                url=url,
                status_code=0,
                html="",
                error=str(exc),
            )

    # ── Playwright (JS-rendered) ───────────────────────────────────────────

    def get_rendered(
        self,
        url: str,
        *,
        wait_until: str = "networkidle",
        stealth: bool = False,
    ) -> ScrapeResult:
        """
        Fetch a URL via Playwright headless Chromium (JavaScript executed).

        Mirrors the pattern used by the existing generator's get_rendered_html().
        ``stealth`` injects basic anti-bot-detection overrides.

        Suitable for SPAs and job boards that require JavaScript.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise ImportError(
                "playwright is required for JS-rendered fetches: "
                "pip install playwright && playwright install chromium"
            )

        logger.debug("Playwright GET %s (stealth=%s)", url, stealth)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(extra_http_headers=self._headers)
                page = context.new_page()

                if stealth:
                    # Basic stealth overrides — mirrors wellfound.py _STEALTH_INIT_SCRIPT
                    page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        window.chrome = window.chrome || {runtime: {}};
                        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    """)

                page.goto(url, timeout=self._playwright_timeout)
                page.wait_for_load_state(wait_until, timeout=self._playwright_timeout)
                html = page.content()
                final_url = page.url
                browser.close()

                logger.debug("Playwright fetch complete, content length=%d", len(html))
                return ScrapeResult(
                    url=final_url,
                    status_code=200,
                    html=html,
                    js_rendered=True,
                )
        except Exception as exc:
            logger.warning("Playwright fetch failed for %s: %s", url, exc)
            return ScrapeResult(
                url=url,
                status_code=0,
                html="",
                js_rendered=True,
                error=str(exc),
            )

    # ── Convenience ────────────────────────────────────────────────────────

    def fetch(self, url: str, *, force_playwright: bool = False) -> ScrapeResult:
        """
        Smart fetch: tries plain HTTP first unless force_playwright is set.

        A 200 response from plain HTTP is returned directly.
        For non-200 or explicit JS requirement, falls back to Playwright.
        """
        if force_playwright:
            return self.get_rendered(url)

        result = self.get(url)
        if result.ok:
            return result

        logger.debug(
            "Plain HTTP returned %d for %s, retrying with Playwright",
            result.status_code, url,
        )
        return self.get_rendered(url)


# ── Factory ────────────────────────────────────────────────────────────────

def make_scraping_client(
    timeout: float = 20.0,
    playwright_timeout: int = 60_000,
) -> ScrapingClient:
    """Create a ScrapingClient with sensible defaults."""
    return ScrapingClient(timeout=timeout, playwright_timeout=playwright_timeout)

"""Integration tests for job-board scraping.

Run with:
    pytest tests/scraping/test_job_boards.py -v --timeout=90

Each test fetches a real job posting and verifies a meaningful description
was extracted.  Tests are marked ``scraping`` and ``network`` so they can
be excluded from the regular CI suite:
    pytest -m "not scraping"

Failures are automatically categorised by the error class raised to
populate the Supported Job Boards document.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from tailor.job.scrape import scrape_job_url

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIN_DESC_CHARS = 200  # a meaningful description is at least this long

_URLS_FILE = Path(__file__).parent / "urls.json"


def _load_entries() -> list[dict]:
    with open(_URLS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _is_skipped(entry: dict) -> bool:
    """Return True for entries that have no testable URL (deprecated, no URL found, etc.)."""
    return entry.get("url") is None


def _scrape_id(entry: dict) -> str:
    return entry["platform"].replace(" ", "_").replace("/", "_")


# ---------------------------------------------------------------------------
# Parametrize
# ---------------------------------------------------------------------------

_ALL_ENTRIES = _load_entries()
_TESTABLE = [e for e in _ALL_ENTRIES if not _is_skipped(e)]


@pytest.fixture(scope="session")
def results_tracker() -> dict[str, Any]:
    """Shared mutable dict that accumulates pass/fail/skip per platform."""
    return {}


# ---------------------------------------------------------------------------
# Per-platform tests
# ---------------------------------------------------------------------------

@pytest.mark.scraping
@pytest.mark.network
@pytest.mark.parametrize("entry", _TESTABLE, ids=[_scrape_id(e) for e in _TESTABLE])
def test_scrape_job_board(entry: dict, results_tracker: dict) -> None:
    """Fetch one real posting and assert a non-trivial description was extracted."""
    platform = entry["platform"]
    url = entry["url"]
    category = entry.get("category", "unknown")

    start = time.monotonic()
    try:
        job = scrape_job_url(url)
        elapsed = time.monotonic() - start

        desc_len = len(job.description or "")
        if desc_len < _MIN_DESC_CHARS:
            results_tracker[platform] = {
                "status": "FAIL",
                "reason": f"Description too short ({desc_len} chars)",
                "category": category,
                "elapsed": elapsed,
            }
            pytest.fail(
                f"{platform}: description only {desc_len} chars "
                f"(min {_MIN_DESC_CHARS}). title={job.job_title!r} company={job.company!r}"
            )

        results_tracker[platform] = {
            "status": "PASS",
            "title": job.job_title,
            "company": job.company,
            "desc_chars": desc_len,
            "category": category,
            "elapsed": elapsed,
        }

    except RuntimeError as exc:
        elapsed = time.monotonic() - start
        msg = str(exc)
        # Classify well-known failure modes
        if any(k in msg.lower() for k in ("sign-in", "authwall", "login", "bot")):
            reason = "anti-bot / auth wall"
        elif "timeout" in msg.lower():
            reason = "timeout"
        else:
            reason = f"RuntimeError: {msg[:120]}"

        results_tracker[platform] = {
            "status": "FAIL",
            "reason": reason,
            "category": category,
            "elapsed": elapsed,
        }
        pytest.fail(f"{platform}: {reason}")

    except Exception as exc:
        elapsed = time.monotonic() - start
        exc_type = type(exc).__name__
        msg = str(exc)

        if "403" in msg or "401" in msg or "blocked" in msg.lower():
            reason = f"HTTP blocked ({exc_type})"
        elif "timeout" in msg.lower() or "timed out" in msg.lower():
            reason = "timeout"
        elif "cannot parse" in msg.lower() or "cannot extract" in msg.lower():
            reason = f"URL parse error ({exc_type})"
        else:
            reason = f"{exc_type}: {msg[:120]}"

        results_tracker[platform] = {
            "status": "FAIL",
            "reason": reason,
            "category": category,
            "elapsed": elapsed,
        }
        pytest.fail(f"{platform}: {reason}")


# ---------------------------------------------------------------------------
# Summary fixture — prints after all scraping tests finish
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session", autouse=True)
def print_scraping_summary(results_tracker: dict) -> None:
    """Print a pass/fail summary table at the end of the session."""
    yield  # run all tests first

    if not results_tracker:
        return

    passed = {k: v for k, v in results_tracker.items() if v["status"] == "PASS"}
    failed = {k: v for k, v in results_tracker.items() if v["status"] == "FAIL"}

    print("\n" + "=" * 70)
    print("SCRAPING TEST SUMMARY")
    print("=" * 70)
    print(f"Passed: {len(passed)}  Failed: {len(failed)}")
    print()

    if passed:
        print("PASSED:")
        for platform, r in sorted(passed.items()):
            print(f"  [OK] {platform:<30} {r['desc_chars']:>6} chars  {r['elapsed']:.1f}s")
    if failed:
        print("\nFAILED:")
        for platform, r in sorted(failed.items()):
            print(f"  [FAIL] {platform:<30} [{r['category']}]  {r['reason']}")

    print("=" * 70)

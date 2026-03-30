# Supported Job Boards

This document lists every job board tested against the scraper (`src/tailor/job/scrape.py`).
Run the integration tests with:

```
pytest tests/scraping/test_job_boards.py -v -s
```

Test URLs and per-site metadata live in `tests/scraping/urls.json`.

---

## Fully Supported

These boards pass the integration test and return a meaningful job description (≥ 200 chars).

| Board | Category | Scraper strategy | Notes |
|---|---|---|---|
| **Indeed** | Major | Dedicated — `curl_cffi` Chrome impersonation + cookie priming | Most reliable |
| **Dice** | Tech | Generic — Playwright + JSON-LD extraction | JS-rendered |
| **Remote OK** | Remote | Generic — Playwright + JSON-LD extraction | |
| **Remotive** | Remote | Generic — Playwright + JSON-LD extraction | URL format: `/remote/jobs/...` |
| **SimplyHired** | Aggregator | Generic — Playwright + JSON-LD extraction | |
| **Built In** | Niche | Generic — Playwright + JSON-LD (@graph) extraction | JSON-LD wrapped in `@graph` |
| **Greenhouse** | ATS | Dedicated — public REST API | `boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}` |
| **Lever** | ATS | Dedicated — public REST API | `api.lever.co/v0/postings/{company}/{id}` |
| **Workday** | ATS | Generic — Playwright (JS SPA) | Company subdomain varies: `{company}.wd5.myworkdayjobs.com` |
| **SmartRecruiters** | ATS | Dedicated — public REST API | `api.smartrecruiters.com/v1/companies/{company}/postings/{id}` |
| **Ashby** | ATS | Dedicated — `__NEXT_DATA__` JSON parser | Falls back to JSON-LD then Playwright |
| **Job Bank Canada** | Canada | Dedicated — static HTML parser (`#job-posting-content`) | Government job board |

---

## Supported — Test URL Expired

The scraper is implemented and works correctly, but job postings on these boards expire
quickly (days to weeks). Re-run the test with a fresh posting URL to verify.

| Board | Category | Scraper strategy | Why test URL expires |
|---|---|---|---|
| **LinkedIn** | Major | Dedicated — LinkedIn guest API (`jobs-guest/jobs/api/jobPosting/{id}`) | Postings are deactivated when filled or closed |
| **CareerBuilder** | Aggregator | Generic — Playwright + JSON-LD extraction | Listings removed after job closes |
| **Adzuna Canada** | Aggregator | Generic — Playwright extraction | Aggregated listings expire and return 410 Gone |

**Refreshing the LinkedIn URL**: any URL of the form
`https://www.linkedin.com/jobs/view/NUMERIC_ID` or
`https://www.linkedin.com/jobs/view/title-slug-NUMERIC_ID` is supported.

---

## Not Supported — Bot / Anti-Scraping Protection

These boards actively block automated access. Manual copy-paste of the job description is required.

| Board | Category | Protection type | Details |
|---|---|---|---|
| **Glassdoor** | Major | CloudFlare + challenge pages | Returns challenge page on all approaches; no reliable bypass available |
| **Monster** | Major | Bot detection | Returns 403 on plain HTTP; Playwright renders but returns no structured data |
| **We Work Remotely** | Remote | Bot detection | Returns 403 on plain HTTP; Playwright blocked at IP level |
| **Wellfound** | Tech | DataDome | All three stealth tiers blocked (curl_cffi + Playwright Chrome + Playwright Chromium) |

**Workaround for all protected boards**: open the job page in your browser, select and copy the full job description text, then paste it into the app's "Paste job description" field.

---

## Not Supported — No Accessible Individual Posting

These platforms have no stable, publicly accessible individual job-posting URL —
either because they require login, redirect to the employer's own site, or have
no per-posting page structure.

| Board | Category | Reason |
|---|---|---|
| **ZipRecruiter** | Major | Individual posting deep-links require login to generate |
| **Hacker News Jobs** | Tech | Monthly "Who is Hiring" thread is a comment aggregator, not a per-posting page |
| **Working Nomads** | Remote | Current URLs (`/job/go/{id}/`) are redirects to the employer's own career site |
| **Jobspresso** | Remote | Full posting details require membership/login |
| **BC Jobs** | Canada | No stable individual posting URL pattern |
| **Eluta** | Canada | Aggregator; redirects to employer site |
| **Jooble** | Aggregator | Aggregator; posting pages redirect to source or expire immediately |
| **StepStone** | International | No stable individual posting URL found |
| **Seek** | International | Strong bot protection; individual URLs not reliably accessible |
| **Naukri** | International | No stable individual posting URL found |
| **TechCrunch Jobs / CrunchBoard** | Niche | No stable individual posting URL found |
| **Authentic Jobs** | Niche | No stable individual posting URL found for testing |

---

## Deprecated Platforms

These platforms are no longer active as independent job boards.

| Board | Status | Details |
|---|---|---|
| **Stack Overflow Jobs** | Shut down March 2022 | No longer exists |
| **GitHub Jobs** | Shut down May 2021 | No longer exists |
| **Workopolis** | Acquired by Indeed 2018 | Now redirects to Indeed Canada |
| **AngelList Jobs** | Rebranded 2022 | Now operates as **Wellfound** (wellfound.com) |

---

## ATS Platforms (Embedded Boards)

Several companies embed Greenhouse or Lever boards directly in their own career pages.
These are detected automatically:

- **Embedded Greenhouse** — any URL containing `?gh_jid=NUMERIC_ID` is detected and routed to the Greenhouse scraper
- **Embedded Lever** — not auto-detected; use the `jobs.lever.co/{company}/{id}` URL directly

---

## Adding Support for a New Board

1. Write a scraper in `src/tailor/job/{name}.py` with signature `scrape_{name}(url: str) -> dict`
   returning `{"company": ..., "job_title": ..., "description": ...}`
2. Register it in `_SITE_SCRAPERS` and add a hostname check in `_detect_site` in `scrape.py`
3. Add an entry to `tests/scraping/urls.json` with a real posting URL
4. Run `pytest tests/scraping/test_job_boards.py` — it must pass with ≥ 200 description chars
5. Add the board to the **Fully Supported** table above

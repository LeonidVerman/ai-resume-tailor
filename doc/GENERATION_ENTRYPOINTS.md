# Generation Entrypoints

Reference for the two CLI entrypoints, their required inputs, environment knobs, and expected outputs.

---

## 1. `tailor` — Generate resume + cover letter for one position

### Invocation

```bash
# From a job board URL (scrapes via Playwright)
tailor --position-url <URL>

# From a local text file containing the job description
tailor --position-desc <FILE>

# Flags
tailor --position-desc jobs/acme.txt --force   # overwrite without prompting
tailor --position-desc jobs/acme.txt --debug   # write debug JSON only, skip docx/pdf
```

Or via the module entry point:

```bash
python -m tailor [same flags]
```

### Required files

| Path | Description |
|---|---|
| `templates/Leonid_Verman_Resume_Template.docx` | Master resume `.docx` template |
| `templates/Leonid_Verman_Cover_Letter_Template.docx` | Cover letter `.docx` template |
| `profile/candidate_profile.json` | Candidate profile injected into every LLM prompt |
| `prompts/tailor.txt` | Single-pass system prompt |
| `prompts/extract_metadata.txt` | Metadata extraction prompt (company/role from JD text) |
| `prompts/role.txt` | Role-level instruction block |
| `prompts/task.txt` | Task block injected into prompts |
| `prompts/candidate.txt` | Candidate context block |
| `.env` | API keys and config overrides (see Environment Variables below) |

### Pipeline

```
tailor_documents()  →  TailorResult(resume: str, cover_letter: str)
  └─ save_doc_from_template()  →  output/<Name>_Resume_<Company>.docx + .pdf
  └─ save_doc_from_template()  →  output/<Name>_Cover_Letter_<Company>.docx + .pdf
  └─ save_debug_data()         →  tmp/<Company>-<Role>-<timestamp>.json
```

### Expected outputs

| Path | Description |
|---|---|
| `output/<Name>_Resume_<Company>.docx` | Tailored resume document |
| `output/<Name>_Resume_<Company>.pdf` | PDF rendition |
| `output/<Name>_Cover_Letter_<Company>.docx` | Tailored cover letter document |
| `output/<Name>_Cover_Letter_<Company>.pdf` | PDF rendition |
| `tmp/<Company>-<Role>-<timestamp>.json` | Debug artefact (always written) |

Company name is sanitised to alphanumeric + `_-` for safe filenames.

---

## 2. `tailor assess` — Batch score tailored documents

### Invocation

```bash
tailor assess --positions positions.txt [OPTIONS]

Options:
  --positions FILE       Required. One job URL per line.
  --out DIR              Base reports directory (default: reports). A timestamped subfolder is created.
  --model MODEL          Assessment LLM model (default: ASSESS_MODEL env var).
  --temperature T        Sampling temperature (default: ASSESS_TEMPERATURE env var).
  --max_positions N      Cap number of positions processed.
  --cache_dir DIR        Directory for caching raw LLM assessment results.
  --workers N            Parallel threads (default: min(positions, 20)).
  --calibrate            Score the unmodified master resume/cover letter instead of generated output.
  --calibrate-data DIR   Score pre-generated docx files from DIR (naming: <Name>_Resume_<Company>.docx).
```

### Positions file format

Plain text, one URL per line. Lines starting with `#` are treated as comments and skipped.

```
https://wellfound.com/jobs/...
https://www.linkedin.com/jobs/...
# this line is skipped
```

### Scoring dimensions

Each position receives a score (1–10) on eight dimensions, then a weighted overall:

| Dimension | Weight |
|---|---|
| `truthfulness` | 20% |
| `role_fit` | 20% |
| `constraint_compliance` | 15% |
| `clarity_impact` | 15% |
| `seniority_positioning` | 10% |
| `mechanism_quality` | 10% |
| `cover_letter_effectiveness` | 5% |
| `overall_readiness` | 5% |

### Expected outputs

| Path | Description |
|---|---|
| `reports/<timestamp>/summary.json` | Per-position scores + weighted totals |
| `reports/<timestamp>/summary.csv` | Same data in CSV |
| `reports/<timestamp>/<company>_detail.json` | Full LLM response per position |

---

## Environment variables

All variables are optional with the defaults shown. Set them in `.env` or export before running.

### API

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | _(required)_ | OpenAI API key |

### Generation

| Variable | Default | Description |
|---|---|---|
| `SIMPLE_MODEL` | `gpt-5.2` | Model used by `tailor_documents()` |
| `SIMPLE_TEMPERATURE` | `0.3` | Temperature for generation |

### Assessment

| Variable | Default | Description |
|---|---|---|
| `ASSESS_MODEL` | `gpt-4o-mini` | Model for `tailor assess` |
| `ASSESS_TEMPERATURE` | `0.2` | Temperature for `tailor assess` |

### PDF conversion

| Variable | Default | Description |
|---|---|---|
| `DOCKER_IMAGE` | `minidocks/libreoffice` | Docker image used by `docx_to_pdf` (Docker strategy) |

---

## Key source modules

| Module | Responsibility |
|---|---|
| `tailor.cli` | Argument parsing and top-level orchestration |
| `tailor.config` | All path constants and env-var config |
| `tailor.core_generation.llm` | LLM client, `tailor_documents`, `extract_metadata_ai` |
| `tailor.cover_letter` | `parse_cover_letter` |
| `tailor.prompts` | `_load_prompt`, `_load_candidate_profile` |
| `tailor.debug` | `save_debug_data` — writes `tmp/` artefacts |
| `tailor.docx.template_fill` | `save_doc_from_template`, `read_docx`, `normalize_cover_letter` |
| `tailor.docx.pdf` | `docx_to_pdf` (Docker or local xhtml2pdf) |
| `tailor.job.scrape` | `scrape_job_url` → `JobData` |
| `tailor.assess` | `run_assess_pipeline` |

import json
import os
from datetime import datetime
from pathlib import Path

from tailor.config import TMP_DIR


def save_debug_data(
    company: str,
    job_title: str,
    llm_response: dict,
    llm_request=None,
    diff=None,
    output_dir: Path | str | None = None,
    extra: dict | None = None,
):
    """Save debug artefacts for a tailoring run.

    Parameters
    ----------
    company, job_title:
        Used to build the filename.
    llm_response:
        The final parsed LLM output (``{"resume": ..., "cover_letter": ...}``).
    llm_request:
        Message list sent to the LLM.
    diff:
        Resume section diffs produced by :func:`tailor.diff.diff_resume`.
    """
    out = Path(output_dir) if output_dir is not None else TMP_DIR
    os.makedirs(out, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_company = "".join(c if c.isalnum() or c in "_-" else "_" for c in company).strip("_")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in job_title).strip("_")
    filename = out / f"{safe_company}-{safe_title}-{timestamp}.json"

    data: dict = {
        "company": company,
        "position": job_title,
        "llm_request": llm_request,
        "llm_response": llm_response,
        "diff": diff,
    }

    if extra:
        data.update(extra)

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Debug data saved to {filename}")

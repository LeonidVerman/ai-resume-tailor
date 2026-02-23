import json
import os
from datetime import datetime

from tailor.config import TMP_DIR


def save_debug_data(
    company: str,
    job_title: str,
    llm_response: dict,
    llm_request=None,
    diff=None,
    phase1: dict | None = None,
    phase2: dict | None = None,
):
    """Save debug artefacts for a tailoring run.

    Parameters
    ----------
    company, job_title:
        Used to build the filename.
    llm_response:
        The final parsed LLM output (``{"resume": ..., "cover_letter": ...}``).
    llm_request:
        Message list sent to the LLM.  For two-phase runs this is the Phase 2
        request; pass ``None`` to omit it from the legacy key.
    diff:
        Resume section diffs produced by :func:`tailor.diff.diff_resume`.
    phase1:
        Optional dict with Phase 1 debug data::

            {
                "llm_request":    <list of messages>,
                "llm_response_raw": <raw JSON string>,
                "plan_json":      <validated plan dict>,
                "model":          <model name string>,
                "usage":          <token counts dict>,
                "validation_errors": [<strings>],   # empty on success
            }

    phase2:
        Optional dict with Phase 2 debug data::

            {
                "llm_request":    <list of messages>,
                "llm_response_raw": <raw JSON string>,
                "model":          <model name string>,
                "usage":          <token counts dict>,
            }
    """
    os.makedirs(TMP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_company = "".join(c if c.isalnum() or c in "_-" else "_" for c in company).strip("_")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in job_title).strip("_")
    filename = TMP_DIR / f"{safe_company}-{safe_title}-{timestamp}.json"

    data: dict = {
        "company": company,
        "position": job_title,
        "llm_request": llm_request,
        "llm_response": llm_response,
        "diff": diff,
    }

    if phase1 is not None:
        data["phase1"] = phase1

    if phase2 is not None:
        data["phase2"] = phase2

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Debug data saved to {filename}")

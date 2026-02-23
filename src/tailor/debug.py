import json
import os
from datetime import datetime

from tailor.config import TMP_DIR


def save_debug_data(company, job_title, llm_response, llm_request=None, diff=None):
    os.makedirs(TMP_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_company = "".join(c if c.isalnum() or c in "_-" else "_" for c in company).strip("_")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in job_title).strip("_")
    filename = TMP_DIR / f"{safe_company}-{safe_title}-{timestamp}.json"

    data = {
        "company": company,
        "position": job_title,
        "llm_request": llm_request,
        "llm_response": llm_response,
        "diff": diff,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Debug data saved to {filename}")

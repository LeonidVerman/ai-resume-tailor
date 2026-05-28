"""
tests/generate_run_data.py

Generate debug run-data JSON files for resume samples.

For each sample the script:
  1. Logs in as the configured regular user
  2. Uploads the sample resume DOCX
  3. Generates a candidate profile draft via LLM autofill
  4. Saves the draft as the user's profile (PUT)
  5. Polls until classification_jsonb is filled (every 5 s, timeout 600 s)
  6. Runs generation against the configured job description
  7. Logs in as admin, downloads the run-data JSON
  8. Saves it to tests/samples/generation/ with the sample-number prefix,
     replacing any previous file for the same sample+job combination

Usage
-----
  python generate_run_data.py             # all samples 1-40
  python generate_run_data.py 4           # sample 4 only
  python generate_run_data.py 4 16 18     # samples 4, 16, 18

Configuration (environment variables, all optional)
---------------------------------------------------
  SERVICE_URL        default: http://localhost:8000
  USER_LOGIN         default: lidiamary@sharklasers.com
  USER_PASSWORD      default: 12345678
  ADMIN_LOGIN        default: leonidverman@cvrocket.io
  ADMIN_PASSWORD     default: 87654321
  JOB_DESCRIPTION_ID default: 97
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
DOCX_DIR = SCRIPT_DIR / "samples" / "resume" / "docx"
GENERATION_DIR = SCRIPT_DIR / "samples" / "generation"
CLASSIFIER_DIR = REPO_ROOT / "tmp" / "artefacts" / "generate_run_test"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SERVICE_URL = os.environ.get("SERVICE_URL", "http://localhost:8000").rstrip("/")
USER_LOGIN = os.environ.get("USER_LOGIN", "lidiamary@sharklasers.com")
USER_PASSWORD = os.environ.get("USER_PASSWORD", "12345678")
ADMIN_LOGIN = os.environ.get("ADMIN_LOGIN", "leonidverman@cvrocket.io")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "87654321")
JOB_DESCRIPTION_ID = int(os.environ.get("JOB_DESCRIPTION_ID", "97"))

CLASSIFICATION_POLL_INTERVAL = 5   # seconds
CLASSIFICATION_TIMEOUT = 600       # seconds

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

BASE = f"{SERVICE_URL}/api/v1"


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def login(email: str, password: str) -> tuple[str, str]:
    """Return (access_token, user_id)."""
    resp = requests.post(
        f"{BASE}/auth/login",
        json={"email": email, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed for {email}: HTTP {resp.status_code} — {resp.text}")
    data = resp.json()
    return data["session"]["access_token"], data["user"]["id"]


def upload_resume(token: str, docx_path: Path) -> int:
    """Upload a DOCX resume; return resume_id."""
    with open(docx_path, "rb") as fh:
        resp = requests.post(
            f"{BASE}/resumes/upload",
            headers=_auth_headers(token),
            files={"file": (docx_path.name, fh, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            timeout=60,
        )
    if resp.status_code != 201:
        raise RuntimeError(f"Resume upload failed: HTTP {resp.status_code} — {resp.text}")
    return resp.json()["id"]


def generate_autofill_draft(token: str, resume_id: int) -> dict:
    """Generate LLM profile draft; return the draft dict."""
    resp = requests.post(
        f"{BASE}/candidate-profile/autofill/generate",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"resume_id": resume_id},
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Autofill generate failed: HTTP {resp.status_code} — {resp.text}")
    return resp.json()["draft"]


def save_profile(token: str, draft: dict) -> bool:
    """PUT the draft as the user profile; return onboarding_completed."""
    resp = requests.put(
        f"{BASE}/candidate-profile",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"profile": draft, "profile_version": "1"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Profile save failed: HTTP {resp.status_code} — {resp.text}")
    return resp.json()["onboarding_completed"]


def poll_classification(admin_token: str, resume_id: int) -> dict:
    """Poll until classification_jsonb is present; return the envelope; hard-fail after CLASSIFICATION_TIMEOUT s."""
    deadline = time.time() + CLASSIFICATION_TIMEOUT
    while True:
        resp = requests.get(
            f"{BASE}/admin/resumes/{resume_id}/classification",
            headers=_auth_headers(admin_token),
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code != 404:
            raise RuntimeError(
                f"Classification check failed: HTTP {resp.status_code} — {resp.text}"
            )
        if time.time() >= deadline:
            raise RuntimeError(
                f"Classification not available after {CLASSIFICATION_TIMEOUT}s for resume {resume_id}"
            )
        time.sleep(CLASSIFICATION_POLL_INTERVAL)


def save_classifier_file(sample_num: int, api_filename: str, run_id: int, classification_envelope: dict) -> Path:
    """Save classifier input+output to CLASSIFIER_DIR as <stem>-classifier.json."""
    # Derive the same stem used for the generation file: everything before -{run_id}-
    gen_name = f"{sample_num}-{api_filename}"
    stem = _prefix_before_run_id(gen_name, run_id)
    dest_name = f"{stem}-{run_id}-classifier.json"

    CLASSIFIER_DIR.mkdir(parents=True, exist_ok=True)
    dest = CLASSIFIER_DIR / dest_name
    dest.write_text(
        json.dumps(classification_envelope, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return dest


def run_generation(token: str, resume_id: int) -> int:
    """Trigger generation; return run_id."""
    resp = requests.post(
        f"{BASE}/generations",
        headers={**_auth_headers(token), "Content-Type": "application/json"},
        json={"job_description_id": JOB_DESCRIPTION_ID, "structured_resume_id": resume_id},
        timeout=300,
    )
    if resp.status_code != 201:
        raise RuntimeError(f"Generation failed: HTTP {resp.status_code} — {resp.text}")
    data = resp.json()
    if data["status"] != "succeeded":
        raise RuntimeError(f"Generation did not succeed: status={data['status']} — {data.get('message')}")
    return data["run_id"]


def download_run_data(admin_token: str, run_id: int) -> tuple[str, bytes]:
    """Download run-data JSON; return (api_filename, raw_bytes)."""
    resp = requests.get(
        f"{BASE}/admin/run-data/download/{run_id}",
        headers=_auth_headers(admin_token),
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Run-data download failed: HTTP {resp.status_code} — {resp.text}")

    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    if not m:
        raise RuntimeError(f"No filename in Content-Disposition: {cd!r}")
    return m.group(1), resp.content


def _prefix_before_run_id(filename: str, run_id: int) -> str:
    """Return the part of filename that precedes -{run_id}-."""
    marker = f"-{run_id}-"
    idx = filename.find(marker)
    if idx == -1:
        raise RuntimeError(f"run_id {run_id} not found in filename {filename!r}")
    return filename[:idx]


def save_generation_file(sample_num: int, run_id: int, api_filename: str, data: bytes) -> Path:
    """
    Prepend sample number to the API filename, delete any prior file for the
    same sample+job (matching prefix up to but excluding the run_id segment),
    then write the new file.  Returns the saved path.
    """
    final_name = f"{sample_num}-{api_filename}"
    prefix = _prefix_before_run_id(final_name, run_id)

    GENERATION_DIR.mkdir(parents=True, exist_ok=True)

    # Delete any previous file for the same sample+job
    for existing in GENERATION_DIR.glob(f"{prefix}-*.json"):
        if existing.name != final_name:
            print(f"    Deleting old file: {existing.name}")
            existing.unlink()

    dest = GENERATION_DIR / final_name
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------------------
# Per-sample logic
# ---------------------------------------------------------------------------

def find_resume_file(sample_num: int) -> Path:
    matches = sorted(DOCX_DIR.glob(f"{sample_num}-*.docx"))
    if not matches:
        raise FileNotFoundError(f"No resume file found for sample {sample_num} in {DOCX_DIR}")
    return matches[0]


def process_sample(sample_num: int, user_token: str, admin_token: str) -> None:
    print(f"\n[Sample {sample_num}]")

    resume_path = find_resume_file(sample_num)
    print(f"  Resume: {resume_path.name}")

    print("  Uploading resume...")
    resume_id = upload_resume(user_token, resume_path)
    print(f"  resume_id={resume_id}")

    print("  Generating autofill draft (LLM)...")
    draft = generate_autofill_draft(user_token, resume_id)
    print(f"  Draft candidate: {draft.get('candidate', {}).get('name', '?')}")

    print("  Saving profile...")
    onboarding_ok = save_profile(user_token, draft)
    if not onboarding_ok:
        raise RuntimeError("onboarding_completed=False — profile setup is incomplete for this user")

    print("  Waiting for classification...")
    classification_envelope = poll_classification(admin_token, resume_id)
    print("  Classification ready")

    print("  Running generation...")
    run_id = run_generation(user_token, resume_id)
    print(f"  run_id={run_id}")

    print("  Downloading run data...")
    api_filename, raw = download_run_data(admin_token, run_id)

    dest = save_generation_file(sample_num, run_id, api_filename, raw)
    print(f"  Saved: {dest.name}")

    cls_dest = save_classifier_file(sample_num, api_filename, run_id, classification_envelope)
    print(f"  Classifier: {cls_dest.name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = sys.argv[1:]
    if args:
        try:
            sample_numbers = [int(a) for a in args]
        except ValueError:
            print(f"Error: arguments must be sample numbers (integers). Got: {args}", file=sys.stderr)
            sys.exit(1)
    else:
        sample_numbers = list(range(1, 41))

    print(f"Service URL : {SERVICE_URL}")
    print(f"User        : {USER_LOGIN}")
    print(f"Admin       : {ADMIN_LOGIN}")
    print(f"JD ID       : {JOB_DESCRIPTION_ID}")
    print(f"Samples     : {sample_numbers}")

    print("\nLogging in...")
    user_token, user_id = login(USER_LOGIN, USER_PASSWORD)
    print(f"  User  logged in: id={user_id}")
    admin_token, admin_id = login(ADMIN_LOGIN, ADMIN_PASSWORD)
    print(f"  Admin logged in: id={admin_id}")

    failed: list[tuple[int, str]] = []
    for num in sample_numbers:
        try:
            process_sample(num, user_token, admin_token)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            failed.append((num, str(exc)))

    print("\n" + "=" * 60)
    if failed:
        print(f"Completed with {len(failed)} failure(s):")
        for num, msg in failed:
            print(f"  Sample {num}: {msg}")
        sys.exit(1)
    else:
        print(f"All {len(sample_numbers)} sample(s) completed successfully.")


if __name__ == "__main__":
    main()

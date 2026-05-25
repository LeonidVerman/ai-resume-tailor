#!/usr/bin/env bash
# generate_run_data.sh
#
# Generate debug run-data JSONs for resume samples by calling the service API.
#
# Usage:
#   ./generate_run_data.sh              # regenerate all samples (1-40)
#   ./generate_run_data.sh 4            # sample 4 only
#   ./generate_run_data.sh 4 16 18      # samples 4, 16, 18
#
# Optional env vars (all have defaults):
#   SERVICE_URL        -- backend base URL  (default: http://localhost:8000)
#   USER_LOGIN         -- regular user email
#   USER_PASSWORD      -- regular user password
#   ADMIN_LOGIN        -- admin email
#   ADMIN_PASSWORD     -- admin password
#   JOB_DESCRIPTION_ID -- target job description ID (default: 97)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/generate_run_data.py" "$@"

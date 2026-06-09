#!/usr/bin/env bash
#
# Renew the Garmin (garth) session token used by the import-garmin download phase.
#
# This is a thin, pure-shell wrapper: it makes sure the `garth` dependency is
# available and then runs `renew_garmin_token.py`, which performs the actual
# Garmin login. The login itself (OAuth SSO + MFA + token minting) requires the
# `garth` Python library and cannot be done in pure shell.
#
# It logs in interactively (prompts for email, password, and an MFA code if your
# account has 2FA) and writes a fresh token in the single-file `garth_session`
# format this job's pinned `garmindb==3.7.0` expects.
#
# Usage (from the job directory jobs/import-garmin):
#   ./scripts/renew_garmin_token.sh                                  # writes ~/.GarminDb/garth_session
#   GARMIN_TOKEN_FILE=/path/to/garth_session ./scripts/renew_garmin_token.sh
#   GARMIN_EMAIL=you@example.com ./scripts/renew_garmin_token.sh     # skip the email prompt
#   GARMIN_DOMAIN=garmin.cn ./scripts/renew_garmin_token.sh          # China region
#
# After it prints "Fresh token saved", re-run the job:
#   docker compose up --build --abort-on-container-exit
#
# This file is excluded from the Docker image (see .dockerignore).

set -euo pipefail

# This script lives in jobs/import-garmin/scripts/. The uv project (pyproject.toml)
# is in the parent job directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
JOB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Pick a runner for renew_garmin_token.py: prefer the job's uv project (it has
# garth via the 'download' extra), otherwise fall back to a plain `python3`.
if command -v uv >/dev/null 2>&1; then
    echo "Ensuring download dependencies (garth) are installed via uv..."
    uv sync --project "$JOB_DIR" --extra download >/dev/null
    exec uv run --project "$JOB_DIR" python "$SCRIPT_DIR/renew_garmin_token.py" "$@"
elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/renew_garmin_token.py" "$@"
else
    echo "ERROR: neither 'uv' nor 'python3' found on PATH." >&2
    exit 1
fi

#!/bin/sh
# Poll the Polar AccessLink API until it responds with a non-503 status.
# Requires: curl, and either jq or python3/python for JSON parsing.
#
# Token source (checked in order):
#   1. ACCESS_TOKEN environment variable
#   2. POLAR_TOKENS_FILE env var (path to JSON file)
#   3. ./notebooks/tokens_polar.json  (repo default)

set -e

POLL_INTERVAL=60
API_BASE="${POLAR_API_BASE:-https://www.polaraccesslink.com/v3}"
ENDPOINT="/users/self"

# --- resolve access token ---------------------------------------------------
resolve_token() {
    if [ -n "$ACCESS_TOKEN" ]; then
        echo "$ACCESS_TOKEN"
        return
    fi

    tokens_file="${POLAR_TOKENS_FILE:-}"
    if [ -z "$tokens_file" ]; then
        # default: look relative to this script's repo root
        script_dir="$(cd "$(dirname "$0")" && pwd)"
        tokens_file="$script_dir/../notebooks/tokens_polar.json"
    fi

    if [ ! -f "$tokens_file" ]; then
        echo "ERROR: token file not found: $tokens_file" >&2
        echo "Set ACCESS_TOKEN or POLAR_TOKENS_FILE." >&2
        exit 1
    fi

    # prefer jq, fall back to grep+sed
    if command -v jq >/dev/null 2>&1; then
        jq -r '.access_token' "$tokens_file"
    else
        grep '"access_token"' "$tokens_file" | sed 's/.*: *"\([^"]*\)".*/\1/'
    fi
}

TOKEN="$(resolve_token)"
if [ -z "$TOKEN" ]; then
    echo "ERROR: could not determine access token." >&2
    exit 1
fi

# --- poll loop ---------------------------------------------------------------
attempt=0
while true; do
    attempt=$((attempt + 1))
    ts="$(date '+%Y-%m-%d %H:%M:%S')"

    status=$(curl -s -o /dev/null -w '%{http_code}' \
        --max-time 30 \
        -H "Authorization: Bearer $TOKEN" \
        -H "Accept: application/json" \
        "${API_BASE}${ENDPOINT}") || status="000"

    echo "[$ts] Attempt $attempt: HTTP $status"

    if [ "$status" != "503" ] && [ "$status" != "000" ]; then
        echo ""
        echo "✅ Polar API is available (HTTP $status). Exiting."
        exit 0
    fi

    echo "    Retrying in ${POLL_INTERVAL}s …"
    sleep "$POLL_INTERVAL"
done

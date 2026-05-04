#!/usr/bin/env bash
# config_from_env.sh — Populate Pulumi config from the import job's .env file
#
# Run this once to bootstrap Pulumi config from the existing .env:
#   cd jobs/import/infra
#   ./config_from_env.sh
#
# After this, deploy.sh uses Pulumi config (not .env) for all values.
set -euo pipefail

# Pulumi local backend — empty passphrase for local dev
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"
TOKENS_FILE="$SCRIPT_DIR/../tokens_polar.json"
STACK_NAME="${1:-prod}"

if [ ! -f "$ENV_FILE" ]; then
    echo "❌ .env file not found at $ENV_FILE"
    echo "   Copy .env.example and fill in your values first."
    exit 1
fi

echo "Loading config from: $ENV_FILE"
echo "Target stack: $STACK_NAME"
echo

# Source the .env file (handles KEY=value and KEY='value')
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$SCRIPT_DIR"

# Ensure stack exists
pulumi stack select "$STACK_NAME" 2>/dev/null || pulumi stack init "$STACK_NAME"

# ── Plain config values ─────────────────────────────────────────
echo "Setting plain config values..."
pulumi config set polar-client-id "$POLAR_CLIENT_ID" --stack "$STACK_NAME"
pulumi config set polar-redirect-port "$POLAR_REDIRECT_PORT" --stack "$STACK_NAME"
pulumi config set polar-member-id "$POLAR_MEMBER_ID" --stack "$STACK_NAME"
pulumi config set database-type "$DATABASE_TYPE" --stack "$STACK_NAME"
pulumi config set postgres-host "$POSTGRES_HOST" --stack "$STACK_NAME"
pulumi config set postgres-port "$POSTGRES_PORT" --stack "$STACK_NAME"
pulumi config set postgres-database "$POSTGRES_DATABASE" --stack "$STACK_NAME"
pulumi config set postgres-user "$POSTGRES_USER" --stack "$STACK_NAME"
pulumi config set azure-storage-account "$AZURE_STORAGE_ACCOUNT_NAME" --stack "$STACK_NAME"
pulumi config set azure-storage-container "$AZURE_STORAGE_CONTAINER_NAME" --stack "$STACK_NAME"

# ── Secret config values ────────────────────────────────────────
echo "Setting secret config values..."
pulumi config set --secret polar-client-secret "$POLAR_CLIENT_SECRET" --stack "$STACK_NAME"
pulumi config set --secret postgres-password "$POSTGRES_PASSWORD" --stack "$STACK_NAME"

# ── Tokens from tokens_polar.json ───────────────────────────────
if [ -f "$TOKENS_FILE" ]; then
    echo "Loading tokens from: $TOKENS_FILE"

    # Extract values using python (available since we're in a Python project)
    ACCESS_TOKEN_VAL=$(python3 -c "import json; print(json.load(open('$TOKENS_FILE'))['access_token'])")
    REFRESH_TOKEN_VAL=$(python3 -c "import json; print(json.load(open('$TOKENS_FILE'))['refresh_token'])")
    TOKEN_TYPE_VAL=$(python3 -c "import json; print(json.load(open('$TOKENS_FILE')).get('token_type', 'bearer'))")

    pulumi config set --secret access-token "$ACCESS_TOKEN_VAL" --stack "$STACK_NAME"
    pulumi config set --secret refresh-token "$REFRESH_TOKEN_VAL" --stack "$STACK_NAME"
    pulumi config set token-type "$TOKEN_TYPE_VAL" --stack "$STACK_NAME"
else
    echo "⚠️  tokens_polar.json not found. Set access-token manually:"
    echo "   pulumi config set --secret access-token <value> --stack $STACK_NAME"
fi

# ── Set a placeholder image tag (deploy.sh will override) ───────
IMAGE_TAG=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
pulumi config set image-tag "$IMAGE_TAG" --stack "$STACK_NAME"

echo
echo "✅ Pulumi config populated from .env"
echo "   Run ./deploy.sh to build and deploy."

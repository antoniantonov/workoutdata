#!/usr/bin/env bash
# deploy.sh — Build, push, and deploy the Polar import job
#
# Usage:
#   ./deploy.sh              # Full deploy: build + push + pulumi up
#   ./deploy.sh --infra-only # Only run pulumi up (skip build/push)
#   ./deploy.sh --build-only # Only build and push image (skip pulumi)
set -euo pipefail

# Pulumi local backend — empty passphrase for local dev
export PULUMI_CONFIG_PASSPHRASE="${PULUMI_CONFIG_PASSPHRASE:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
INFRA_DIR="$SCRIPT_DIR"

ACR_NAME="humandcoded2"
ACR_LOGIN_SERVER="humandcoded2.azurecr.io"
IMAGE_NAME="polar-import-job"
STACK_NAME="prod"

# Use git short SHA as image tag for immutable deployments
IMAGE_TAG=$(git -C "$REPO_ROOT" rev-parse --short HEAD)

INFRA_ONLY=false
BUILD_ONLY=false

for arg in "$@"; do
    case $arg in
        --infra-only) INFRA_ONLY=true ;;
        --build-only) BUILD_ONLY=true ;;
        *) echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

echo "============================================================"
echo "Polar Import Job — Deployment"
echo "============================================================"
echo "  ACR:       $ACR_LOGIN_SERVER"
echo "  Image:     $IMAGE_NAME:$IMAGE_TAG"
echo "  Stack:     $STACK_NAME"
echo "  Repo root: $REPO_ROOT"
echo "============================================================"
echo

# ── Step 1: Build and push Docker image ─────────────────────────
if [ "$INFRA_ONLY" = false ]; then
    echo "Step 1: Logging into ACR..."
    az acr login --name "$ACR_NAME"
    echo

    echo "Step 2: Building Docker image..."
    docker build \
        -t "$ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG" \
        -t "$ACR_LOGIN_SERVER/$IMAGE_NAME:latest" \
        -f "$REPO_ROOT/jobs/import/Dockerfile" \
        "$REPO_ROOT"
    echo

    echo "Step 3: Pushing image to ACR..."
    docker push "$ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG"
    docker push "$ACR_LOGIN_SERVER/$IMAGE_NAME:latest"
    echo
fi

# ── Step 2: Run Pulumi ──────────────────────────────────────────
if [ "$BUILD_ONLY" = false ]; then
    echo "Step 4: Setting image tag in Pulumi config..."
    cd "$INFRA_DIR"
    pulumi config set image-tag "$IMAGE_TAG" --stack "$STACK_NAME"
    echo

    echo "Step 5: Running pulumi up..."
    pulumi up --stack "$STACK_NAME" --yes
    echo
fi

echo "============================================================"
echo "✅ Deployment complete!"
echo "   Image: $ACR_LOGIN_SERVER/$IMAGE_NAME:$IMAGE_TAG"
echo "============================================================"

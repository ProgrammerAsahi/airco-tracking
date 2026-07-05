#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="airco-tracker:local"

command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
docker build --tag "$IMAGE" "$PROJECT_DIR"
docker run --rm --env-file "$PROJECT_DIR/.env" "$IMAGE" check --dry-run

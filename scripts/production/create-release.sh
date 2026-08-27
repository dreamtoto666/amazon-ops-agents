#!/usr/bin/env bash
set -euo pipefail

# Build a source-only release archive. It deliberately excludes all local secrets and build output.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="${1:-$PROJECT_DIR/dist}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
archive="$OUTPUT_DIR/amazon-ops-release-$timestamp.tar.gz"

mkdir -p "$OUTPUT_DIR"
tar \
  --exclude='./.git' \
  --exclude='./.env' \
  --exclude='./.env.local' \
  --exclude='./.env.production.local' \
  --exclude='./frontend/.env' \
  --exclude='./frontend/.env.local' \
  --exclude='./frontend/.env.production.local' \
  --exclude='./.venv' \
  --exclude='./.pytest_cache' \
  --exclude='./__pycache__' \
  --exclude='./dist' \
  --exclude='./frontend/node_modules' \
  --exclude='./frontend/.next' \
  -C "$PROJECT_DIR" \
  -czf "$archive" \
  .

printf '%s\n' "$archive"

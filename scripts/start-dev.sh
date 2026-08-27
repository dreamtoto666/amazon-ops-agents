#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_HOST="${AMAZON_OPS_API_HOST:-127.0.0.1}"
API_PORT="${AMAZON_OPS_API_PORT:-8000}"

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR/src" uv run uvicorn amazon_ops.api:app \
  --host "$API_HOST" \
  --port "$API_PORT" &
API_PROCESS_ID=$!

cleanup() {
  kill "$API_PROCESS_ID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd "$PROJECT_DIR/frontend"
npm run dev

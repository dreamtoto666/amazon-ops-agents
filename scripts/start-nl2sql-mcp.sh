#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$PROJECT_DIR"
PYTHONPATH="$PROJECT_DIR/src" uv run python -m amazon_ops.nl2sql_mcp

#!/usr/bin/env bash
set -euo pipefail

# Build server-compatible images locally, then create a source release that records
# their immutable tags. The server only loads these images; it never builds them.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUTPUT_DIR="${1:-$PROJECT_DIR/dist}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
tag="release-$timestamp"
api_image="amazon-ops-api:$tag"
frontend_image="amazon-ops-frontend:$tag"
source_archive="$OUTPUT_DIR/amazon-ops-release-$timestamp.tar.gz"
image_archive="$OUTPUT_DIR/amazon-ops-images-$timestamp.tar.gz"
staging_dir="$(mktemp -d)"

cleanup() {
  rm -rf "$staging_dir"
}
trap cleanup EXIT

command -v docker >/dev/null || {
  printf '未找到 Docker；请先启动 Docker Desktop。\n' >&2
  exit 1
}

mkdir -p "$OUTPUT_DIR" "$staging_dir/source"

# The Aliyun Ubuntu server is x86_64. --platform avoids producing an Apple Silicon
# image that would fail there with "exec format error".
docker buildx build --platform linux/amd64 --load --tag "$api_image" "$PROJECT_DIR"
docker buildx build --platform linux/amd64 --load --tag "$frontend_image" "$PROJECT_DIR/frontend"

"$PROJECT_DIR/scripts/production/create-release.sh" "$staging_dir" >/dev/null
generated_source_archive="$(find "$staging_dir" -maxdepth 1 -name 'amazon-ops-release-*.tar.gz' -print -quit)"
[[ -n "$generated_source_archive" ]] || {
  printf '未能生成源码发布包。\n' >&2
  exit 1
}

tar -xzf "$generated_source_archive" -C "$staging_dir/source"
printf 'AMAZON_OPS_API_IMAGE=%s\nAMAZON_OPS_FRONTEND_IMAGE=%s\n' \
  "$api_image" "$frontend_image" > "$staging_dir/source/.image-release.env"
tar -C "$staging_dir/source" -czf "$source_archive" .
docker save "$api_image" "$frontend_image" | gzip > "$image_archive"

printf '源码发布包：%s\n镜像发布包：%s\n' "$source_archive" "$image_archive"

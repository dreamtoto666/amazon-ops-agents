#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-/opt/amazon-ops/shared/.env}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.prod.yml"

fail() {
  printf '备份失败：%s\n' "$*" >&2
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail "找不到生产环境文件：$ENV_FILE"
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

required_vars=(POSTGRES_DB POSTGRES_USER S3_ENDPOINT_URL S3_BUCKET S3_REGION S3_PREFIX AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY)
for name in "${required_vars[@]}"; do
  [[ -n "${!name:-}" ]] || fail "必须设置 $name"
done
command -v aws >/dev/null || fail "未安装 AWS CLI"

backup_dir="${BACKUP_LOCAL_DIR:-/opt/amazon-ops/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
[[ "$retention_days" =~ ^[1-9][0-9]*$ ]] || fail "BACKUP_RETENTION_DAYS 必须是正整数"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
filename="amazon-ops-postgres-$timestamp.sql.gz"
local_file="$backup_dir/$filename"
remote_key="${S3_PREFIX%/}/$filename"
trap 'rm -f "$local_file"' EXIT

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T postgres \
  pg_dump --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-owner --no-privileges | gzip -9 > "$local_file"
[[ -s "$local_file" ]] || fail "pg_dump 未生成备份内容"

export AWS_DEFAULT_REGION="$S3_REGION"
export AWS_EC2_METADATA_DISABLED=true
aws --endpoint-url "$S3_ENDPOINT_URL" s3 cp "$local_file" "s3://$S3_BUCKET/$remote_key" --only-show-errors
aws --endpoint-url "$S3_ENDPOINT_URL" s3api head-object --bucket "$S3_BUCKET" --key "$remote_key" >/dev/null

find "$backup_dir" -type f -name 'amazon-ops-postgres-*.sql.gz' -mtime +"$retention_days" -delete
cutoff="$(date -u -d "$retention_days days ago" +%Y-%m-%dT%H:%M:%SZ)"
old_keys="$(aws --endpoint-url "$S3_ENDPOINT_URL" s3api list-objects-v2 --bucket "$S3_BUCKET" --prefix "${S3_PREFIX%/}/" --query "Contents[?LastModified<=\`$cutoff\`].Key" --output text || true)"
for key in $old_keys; do
  aws --endpoint-url "$S3_ENDPOINT_URL" s3api delete-object --bucket "$S3_BUCKET" --key "$key"
done

trap - EXIT
printf '备份成功：s3://%s/%s\n' "$S3_BUCKET" "$remote_key"

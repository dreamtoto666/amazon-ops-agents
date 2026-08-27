#!/usr/bin/env bash
set -euo pipefail

# Run from an extracted release directory. Secrets live outside the release tree.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-/opt/amazon-ops/shared/.env}"
COMPOSE_FILE="$PROJECT_DIR/docker-compose.prod.yml"

fail() {
  printf '部署校验失败：%s\n' "$*" >&2
  exit 1
}

[[ -f "$ENV_FILE" ]] || fail "找不到生产环境文件：$ENV_FILE"
[[ -f "$COMPOSE_FILE" ]] || fail "找不到生产 Compose 文件。"

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

required_vars=(APP_DOMAIN APP_BASE_URL POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DEEPSEEK_API_KEY LINGXING_MCP_SECRET)
for name in "${required_vars[@]}"; do
  value="${!name:-}"
  [[ -n "$value" ]] || fail "必须设置 $name"
  [[ "$value" != REPLACE_WITH_* ]] || fail "$name 仍是示例占位值"
  [[ "$value" != *[[:space:]]* ]] || fail "$name 不能包含空白字符"
done

[[ "$APP_BASE_URL" == "https://$APP_DOMAIN" ]] || fail "APP_BASE_URL 必须为 https://$APP_DOMAIN"
[[ ${#POSTGRES_PASSWORD} -ge 24 ]] || fail "POSTGRES_PASSWORD 至少需要 24 个字符"
[[ "$POSTGRES_PASSWORD" =~ ^[A-Za-z0-9._-]+$ ]] || fail "POSTGRES_PASSWORD 仅允许字母、数字、点、下划线和连字符"

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" build
docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans

frontend_port="${FRONTEND_BIND_PORT:-3001}"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "http://127.0.0.1:${frontend_port}/api/agent/health" >/dev/null; then
    printf '部署完成：前端健康检查已通过。\n'
    exit 0
  fi
  sleep 2
done

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps >&2
fail "前端健康检查在 60 秒内未通过；旧数据卷和环境文件均未删除。"

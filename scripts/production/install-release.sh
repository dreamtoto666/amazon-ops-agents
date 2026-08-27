#!/usr/bin/env bash
set -euo pipefail

# Extract an uploaded release, retain its secrets separately, and promote it only after health checks pass.
ARCHIVE_PATH="${1:?用法：install-release.sh /path/to/amazon-ops-release.tar.gz}"
APP_ROOT="${APP_ROOT:-/opt/amazon-ops}"
ENV_FILE="$APP_ROOT/shared/.env"

fail() {
  printf '安装失败：%s\n' "$*" >&2
  exit 1
}

[[ -f "$ARCHIVE_PATH" ]] || fail "找不到发布包：$ARCHIVE_PATH"
[[ -f "$ENV_FILE" ]] || fail "请先创建 $ENV_FILE"

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
release_dir="$APP_ROOT/releases/$release_id"
mkdir -p "$APP_ROOT/releases" "$APP_ROOT/shared"
mkdir "$release_dir"
tar -xzf "$ARCHIVE_PATH" -C "$release_dir" 2>/dev/null || {
  rm -rf "$release_dir"
  fail "发布包解压失败"
}

[[ -x "$release_dir/scripts/production/deploy-server.sh" ]] || {
  rm -rf "$release_dir"
  fail "发布包不包含生产部署脚本"
}

if "$release_dir/scripts/production/deploy-server.sh" "$ENV_FILE"; then
  ln -sfn "$release_dir" "$APP_ROOT/current"
  printf '已切换当前版本至 %s\n' "$release_id"
else
  printf '新版本未通过健康检查，当前版本和 Docker 数据卷保持不变。\n' >&2
  exit 1
fi

#!/usr/bin/env bash
set -euo pipefail

# Requires nginx and certbot to be installed. Run as root or with sudo.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${1:-/opt/amazon-ops/shared/.env}"
EMAIL="${2:?用法：configure-nginx.sh /opt/amazon-ops/shared/.env admin@example.com}"
SITES_AVAILABLE=/etc/nginx/sites-available/amazon-ops
SITES_ENABLED=/etc/nginx/sites-enabled/amazon-ops

[[ $EUID -eq 0 ]] || { echo '请使用 sudo 运行此脚本。' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "找不到 $ENV_FILE" >&2; exit 1; }
command -v nginx >/dev/null || { echo '未安装 nginx。' >&2; exit 1; }
command -v certbot >/dev/null || { echo '未安装 certbot。' >&2; exit 1; }

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
[[ -n "${APP_DOMAIN:-}" && -n "${FRONTEND_BIND_PORT:-}" ]] || { echo 'APP_DOMAIN 和 FRONTEND_BIND_PORT 必须设置。' >&2; exit 1; }
[[ "$APP_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || { echo 'APP_DOMAIN 格式不合法。' >&2; exit 1; }

render() {
  sed -e "s|__APP_DOMAIN__|$APP_DOMAIN|g" -e "s|__FRONTEND_BIND_PORT__|$FRONTEND_BIND_PORT|g" "$1" > "$SITES_AVAILABLE"
}

mkdir -p /var/www/amazon-ops-certbot
render "$PROJECT_DIR/deploy/nginx/amazon-ops.http.conf.template"
ln -sfn "$SITES_AVAILABLE" "$SITES_ENABLED"
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

certbot certonly --webroot -w /var/www/amazon-ops-certbot --email "$EMAIL" --agree-tos --non-interactive -d "$APP_DOMAIN"
render "$PROJECT_DIR/deploy/nginx/amazon-ops.https.conf.template"
nginx -t
systemctl reload nginx
systemctl enable --now certbot.timer
printf 'Nginx 与 HTTPS 证书已配置完成：https://%s\n' "$APP_DOMAIN"

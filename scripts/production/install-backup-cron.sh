#!/usr/bin/env bash
set -euo pipefail

# Installs a daily, non-overlapping host cron job. Run as root after deployment.
[[ $EUID -eq 0 ]] || { echo '请使用 sudo 运行此脚本。' >&2; exit 1; }
APP_ROOT="${APP_ROOT:-/opt/amazon-ops}"
CURRENT_DIR="$APP_ROOT/current"
ENV_FILE="$APP_ROOT/shared/.env"
CRON_FILE=/etc/cron.d/amazon-ops-postgres-backup
LOG_FILE=/var/log/amazon-ops-postgres-backup.log

[[ -x "$CURRENT_DIR/scripts/production/backup-postgres.sh" ]] || { echo '当前版本未准备好。' >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo '找不到生产环境文件。' >&2; exit 1; }
touch "$LOG_FILE"
chmod 640 "$LOG_FILE"

cat > "$CRON_FILE" <<EOF
# Managed by Amazon Ops. Runs daily at 02:20 UTC and prevents overlapping backups.
20 2 * * * root flock -n /var/lock/amazon-ops-postgres-backup.lock $CURRENT_DIR/scripts/production/backup-postgres.sh $ENV_FILE >> $LOG_FILE 2>&1
EOF
chmod 644 "$CRON_FILE"
printf '已安装每日备份任务：%s\n' "$CRON_FILE"

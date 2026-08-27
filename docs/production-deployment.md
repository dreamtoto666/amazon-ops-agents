# Amazon Ops 生产部署（Ubuntu）

本指南适用于一台 Ubuntu 22.04 或 24.04 服务器。架构为 Nginx（公网 HTTPS）→ Next.js
前端（仅 `127.0.0.1`）→ API/PostgreSQL（仅 Docker 私有网络）。浏览器始终通过前端的同源
BFF 调用 API，因此不需要也不得公开 API 或数据库端口。

## 前置条件

- 域名的 A/AAAA 记录已指向服务器公网 IP；DNS 生效后再申请证书。
- 防火墙仅放行 SSH 管理端口及 TCP `80`、`443`；不要放行 `3001`、`8000`、`5432`。
- 部署用户仅拥有项目目录权限和 Docker 使用权限；使用 `sudo` 单独管理 Nginx、Certbot、cron。
- 服务器安装 Docker Engine、Compose 插件、Nginx、Certbot、AWS CLI 与 `curl`：

  ```bash
  sudo apt update
  sudo apt install -y nginx certbot awscli curl
  # Docker Engine 与 Compose 插件按 Docker 官方 Ubuntu 安装说明安装。
  ```

  AWS CLI 仅用于备份上传；应给它的访问密钥配置为只能读写指定备份 Bucket/Prefix 的最小权限。

## 首次部署

1. 创建服务器目录：

   ```bash
   sudo install -d -m 750 -o "$USER" -g "$USER" /opt/amazon-ops/{releases,shared,backups}
   ```

2. 在本地为服务器构建镜像并上传两个发布包。此步骤在本机完成，服务器不会执行前端或 API 构建，特别适合小内存实例。脚本强制生成 `linux/amd64` 镜像，以兼容阿里云 Ubuntu 服务器：

  ```bash
   ./scripts/production/create-image-release.sh
   scp dist/amazon-ops-release-*.tar.gz dist/amazon-ops-images-*.tar.gz deploy@server:/tmp/
  ```

3. 在服务器解压发布包，创建仅服务器保存的环境文件，加载本机构建的镜像并启动。健康检查失败时，发布脚本不会
   删除数据卷、服务器环境文件或原当前版本：

   ```bash
   mkdir -p /tmp/amazon-ops-bootstrap
   tar -xzf /tmp/amazon-ops-release-*.tar.gz -C /tmp/amazon-ops-bootstrap
   cp /tmp/amazon-ops-bootstrap/.env.production.example /opt/amazon-ops/shared/.env
   chmod 600 /opt/amazon-ops/shared/.env
   editor /opt/amazon-ops/shared/.env
   /tmp/amazon-ops-bootstrap/scripts/production/install-release.sh \
     /tmp/amazon-ops-release-*.tar.gz \
     /tmp/amazon-ops-images-*.tar.gz
   ```

   脚本会将版本放入 `/opt/amazon-ops/releases/`，成功后更新
   `/opt/amazon-ops/current`。

   填写 `APP_DOMAIN`、对应的 `APP_BASE_URL`、随机的 `POSTGRES_PASSWORD`、DeepSeek/领星密钥及
   S3 备份参数。`POSTGRES_PASSWORD` 至少 24 个字符，且仅使用字母、数字、`.`、`_`、`-`，以保证
   数据库连接 URL 正确。不要把这个文件放入发布包或提交到代码库。

4. 配置 Nginx 与首次 HTTPS 证书，再创建管理员：

   ```bash
   sudo /opt/amazon-ops/current/scripts/production/configure-nginx.sh \
     /opt/amazon-ops/shared/.env admin@example.com
   docker compose --env-file /opt/amazon-ops/shared/.env \
     -f /opt/amazon-ops/current/docker-compose.prod.yml exec api \
     python -m amazon_ops.auth create-admin
   sudo /opt/amazon-ops/current/scripts/production/install-backup-cron.sh
   ```

   管理员密码只会在终端交互输入；不要通过命令行参数传递。

## 验证与日常更新

部署后依次确认：

```bash
curl -I http://your-domain.example          # 301 到 HTTPS
curl -fsS https://your-domain.example/api/agent/health
docker compose --env-file /opt/amazon-ops/shared/.env \
  -f /opt/amazon-ops/current/docker-compose.prod.yml ps
sudo ss -ltnp | grep -E ':(3001|8000|5432)'
```

最后一条中，`3001` 必须只绑定 `127.0.0.1`；`8000` 和 `5432` 不应出现监听。登录后创建一个最小
Agent 运行，确认页面收到 SSE 阶段更新。后续更新重复“本机构建镜像和发布包 → 上传两个压缩包 → 运行
`install-release.sh`”；数据库卷和共享 `.env` 不会被替换。

## 备份与恢复

每日 02:20 UTC 的 cron 会执行 `pg_dump`、gzip、上传 S3 兼容对象存储并用 `head-object` 确认文件
存在。它会同时清理本机和对象存储中超过 `BACKUP_RETENTION_DAYS`（默认 14 天）的备份。首次请手动执行：

```bash
/opt/amazon-ops/current/scripts/production/backup-postgres.sh /opt/amazon-ops/shared/.env
```

恢复必须先在隔离环境或维护窗口进行。下载备份后，停止 API/前端，使用同一 Compose 与环境文件将 SQL
导入 PostgreSQL；导入操作会覆盖目标数据库内容，执行前必须单独保留现有数据库备份。不要在未验证备份的
情况下删除 Docker 数据卷。

## 回滚与排障

- 代码回滚：将 `/opt/amazon-ops/current` 软链接切回上一个已知可用的发布目录，再从该目录执行
  `deploy-server.sh /opt/amazon-ops/shared/.env`。该操作不修改数据卷。
- 查看应用状态：`docker compose ... ps`；查看日志：`docker compose ... logs --tail=200 api frontend`。
- 证书失败：确认 DNS 指向、80 端口可达、`nginx -t` 通过，之后重新运行 `configure-nginx.sh`。
- 备份失败：检查 `/var/log/amazon-ops-postgres-backup.log`、对象存储最小权限、Bucket/endpoint 及
  `aws --endpoint-url ... s3 ls`。密钥不得粘贴到工单或日志中。

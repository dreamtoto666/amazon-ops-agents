# 账号与认证

Amazon Ops 采用邀请制账号。首位管理员在受控终端运行：

```bash
uv run python scripts/create-admin.py create-admin
```

该命令交互式读取邮箱和密码，不会把初始密码写入环境变量。管理员随后可在“账号管理”中邀请管理员或运营者。所有邀请与密码重置链接只可使用一次，并在 24 小时后失效。

生产环境必须设置 `APP_BASE_URL` 和 SMTP 配置：`SMTP_HOST`、`SMTP_PORT`、`SMTP_STARTTLS`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM`。不要在日志、工单或截图中记录邀请/重置链接或 SMTP 密码。

浏览器会话存储在同源 `HttpOnly` Cookie 中；Python API 仅接收 BFF 转发的 Bearer 会话令牌。密码至少需要 6 个字符；会话默认 12 小时有效；选择“保持登录”时有效期为 30 天。除 `/api/health` 和认证入口外，所有 API 均需认证。

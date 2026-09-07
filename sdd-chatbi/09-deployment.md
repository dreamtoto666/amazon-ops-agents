# 09. 部署与配置

## 配置项

| 配置 | 用途 |
|---|---|
| LLM_API_KEY | 模型密钥 |
| LLM_BASE_URL | 模型地址 |
| LLM_MODEL | 模型名 |
| DATABASE_READONLY_URL | 只读数据库连接 |
| MCP_ROUTER_URL | 分流服务 |
| MCP_DATABASE_URL | 数据库服务 |
| MCP_PYTHON_URL | Python 服务 |
| CHART_STORAGE_BUCKET | 图表存储 |
| MAX_TOOL_ROUNDS | 最大工具轮数 |

## 服务

ChatBI API、Router MCP 可无状态水平扩展；Database MCP 使用连接池和只读权限；Python MCP 使用任务队列或独立沙箱 Worker。

## 发布顺序

1. 创建只读账号、白名单视图与元数据。
2. 部署 MCP 服务并做健康检查。
3. 部署 ChatBI API。
4. 接入前端灰度开关。
5. 监控质量、延迟与错误。

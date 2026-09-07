# 06. MCP 服务设计

## Router MCP

工具 classify_intent。输入用户问题和会话摘要；输出仅为 general_chat、python_task、business_analysis。

## Database MCP

| 工具 | 输入 | 输出 |
|---|---|---|
| list_tables_tool | 数据源标识 | 白名单表与字段元数据 |
| db_sql_tool | 只读 SQL | 列、行、截断信息 |

不得接受任意连接串或白名单外表名。

## Python MCP

工具 run_python_script_tool。输入经审查的代码与 JSON 数据；输出数值、表格摘要或图表引用。代码不能自行连接数据库。

## 传输

开发环境使用 localhost；生产使用带服务间认证的 Streamable HTTP，不允许将 MCP 服务裸露到公网。

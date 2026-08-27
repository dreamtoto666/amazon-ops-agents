# LangSmith 观测

广告诊断工作流已接入 LangSmith。启用后，LangGraph 的工作流节点、DeepSeek 结构化调用，以及每一次 MCP 调用都会记录到同一个 LangSmith trace 中。

## 配置

在 `.env` 中设置：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_your_key
LANGSMITH_PROJECT=amazon-ops-local
# 可选：仅多工作区 API Key 需要
# LANGSMITH_WORKSPACE_ID=your_workspace_id
# 可选：EU 或私有部署时指定
# LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com
```

未设置 `LANGSMITH_TRACING=true` 或 API Key 时，应用照常运行，不会发送追踪数据。

## 追踪结构与筛选字段

广告诊断的根运行名称为 `amazon_ops.advertising_diagnostic`。其子运行包含数据巡检、问题归因、策略建议、复核代办、DeepSeek 结构化调用和 `mcp_tool_call` 工具调用。

根运行会写入以下标签和元数据：

- 标签：`amazon-ops`、`advertising-diagnostic`、触发类型（`manual` 或 `scheduled`）
- 元数据：`trace_id`、`run_id`、`profile_ids`、经营目标、是否启用基准周期

DeepSeek 子运行附带 `ls_provider=deepseek`、模型名及输出 Schema，便于按模型和 Agent 阶段筛选。

每个 `mcp_tool_call` 是 `run_type=tool` 的 Span，可在 LangSmith 以运行名称筛选。它记录供应商、工具名、已脱敏的请求参数、耗时、成功状态、错误码、`call_id`，以及只包含返回记录数、服务端总数和字段名的摘要。广告诊断调用还会关联业务 `run_id` 与 `trace_id`；通用对话调用关联其 `run_id`。

问题归因节点的输出还包含 `detail_call_quotas`：每个 `round + tool` 的已发起次数、失败次数和上限。该账本随 LangGraph 工作流输出和运行历史保存，用于核对每类报告每轮 30 次、两轮最多 240 次的调用边界；它不作为前端配额界面展示。

## 数据边界

LangSmith 会接收工作流输入输出、提示词、结构化模型输出与上述元数据。MCP 工具追踪不会上传完整返回负载，只上传字段摘要和计数；请求中的 `token`、`password`、`authorization`、`cookie` 等敏感字段会被替换为 `[REDACTED]`。不要将 API Key、领星 MCP Key 或其他密钥写入 trace 输入、输出或元数据。

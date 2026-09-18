# Langfuse 观测

Langfuse 是可选观测能力，与既有 LangSmith 追踪可同时启用。未配置时不会影响业务运行。

在运行 Python API 的机器本地 `.env` 配置：

```env
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_BASE_URL=http://<langfuse-host>:3000
```

`LANGFUSE_BASE_URL` 必须是 API 所在机器可访问的地址，不能在跨机器部署时填写 `localhost`。

当前会记录广告诊断的根运行，以及 DeepSeek 结构化调用的耗时、模型名和输出结构类型。为降低数据泄露风险，Langfuse 不上传业务提示词、图片、原始模型输出、MCP 原始返回内容或密钥。

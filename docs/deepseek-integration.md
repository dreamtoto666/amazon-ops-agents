# DeepSeek 统一 LLM 接入

## 选型

项目所有需要大模型的角色统一使用 DeepSeek。确定性路由、关键词评分、规则质检和 MCP 数据转换仍由代码完成，不会为了“全用 LLM”而改成概率式逻辑。

默认模型为 `deepseek-v4-flash`，默认关闭思考模式，用于降低普通结构化任务的延迟和成本。可通过 `.env` 覆盖模型，但生产代码不为每个 Agent 配置不同供应商。

## 已接入的角色

| 角色 | 实现 | 结构化输出 |
| --- | --- | --- |
| 总控请求理解 | `DeepSeekRequestInterpreter` | `UnderstandRequestResult` |
| 总控结果汇总 | `DeepSeekResultAggregator` | `FinalResponse` |
| Listing 初稿 | `DeepSeekListingCopywriter.generate` | `ListingDraft` |
| Listing 修订 | `DeepSeekListingCopywriter.revise` | `ListingDraft` |

未来的销售利润、广告、库存和市场风险 Agent 需要模型时，也必须使用 `DeepSeekModelRoles.llm`。

## 环境变量

```dotenv
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_BASE_URL=https://api.deepseek.com
```

只有 `DEEPSEEK_API_KEY` 必须手动填写。密钥不进入 LangGraph State、SSE 事件、日志或模型上下文。

## 生产组装

```python
roles = build_deepseek_model_roles(env_file=".env")

controller = build_controller_graph(
    interpreter=roles.request_interpreter,
    specialists=specialists,
    aggregator=roles.result_aggregator,
    stages=stages,
)

listing_services = ListingWorkflowServices(
    researcher=build_keyword_research_gateway(env_file=".env"),
    copywriter=roles.listing_copywriter,
    validator=validator,
)
```

Web 应用退出时调用 `roles.llm.close()` 释放连接池。

## 结构化输出与异常

所有角色都使用 DeepSeek JSON Output，并把 Pydantic JSON Schema 一起传入。即使 API 返回了合法 JSON，也必须再通过本地 Schema 验证才能进入 State。

统一错误代码包括：

- `DEEPSEEK_API_KEY_MISSING`
- `DEEPSEEK_AUTH_FAILED`
- `DEEPSEEK_RATE_LIMITED`
- `DEEPSEEK_CONNECTION_FAILED`
- `DEEPSEEK_OUTPUT_TRUNCATED`
- `DEEPSEEK_EMPTY_OUTPUT`
- `DEEPSEEK_INVALID_OUTPUT`

错误中不包含 API 密钥或服务端原始响应正文。

## 实现位置

- `src/amazon_ops/llm.py`
- `src/amazon_ops/deepseek_runtime.py`
- `src/amazon_ops/interfaces.py`
- `src/amazon_ops/listing/copywriter.py`
- `src/amazon_ops/listing/prompts.py`
- `tests/test_deepseek_llm.py`

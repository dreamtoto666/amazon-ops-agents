# DeepSeek 统一 LLM 接入

## 选型

项目所有需要大模型的角色统一使用 DeepSeek。总控的专家选择由 LLM 基于运行时能力清单完成；关键词评分、规则质检和 MCP 数据转换仍由代码完成。

默认模型为 `deepseek-v4-flash`，默认关闭思考模式（`reasoning_effort=off`），用于降低普通结构化任务的延迟和成本。聊天输入框可为单次任务选择模型（`deepseek-v4-flash`、`deepseek-v4-pro`、`deepseek-v4-flash-vision-exp`）**和推理等级**（`off`/`low`/`high`/`max`，默认 `off`）；选择会贯穿总控与广告只读专家（它们共享同一 `DeepSeekModelRoles`），且不会改变其他任务的模型。未选择时使用 `.env` 中的默认模型。生产代码不为每个 Agent 配置不同供应商。

总控各角色用结构化 JSON 输出（`response_format: json_object`），可自由开启思考；广告只读专家（ReAct）靠绑定只读工具查询报表，**不强制 `tool_choice`**（DeepSeek 拒绝 thinking 与强制工具选择并存），依赖 prompt 要求“必须先调用一次工具”，并在模型未调用工具时显式失败（不编造数据）。

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
# 可选推理等级：off / low / high / max；留空则跟随 thinking_enabled。前端请求可逐次覆盖。
DEEPSEEK_REASONING_EFFORT=
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

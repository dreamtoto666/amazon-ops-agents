# Amazon Operations Multi-Agent

第一版实现主控 Agent 的最小闭环：

1. 将用户请求解析成结构化意图与查询范围；
2. 使用确定性规则选择专家 Agent；
3. 执行销售利润、广告、库存和市场风险专家；
4. 根据专家提出的待验证假设，最多追加一轮调查；
5. 汇总事实、假设和建议。

模型、领星 MCP 和专家实现均放在稳定接口之后。Listing Agent 已通过官方 MCP SDK
接入卖家精灵和 Sif；没有真实密钥时仍可测试路由、状态流转和关键词证据映射。

设计文档：

- [总控 Agent 职责说明](docs/controller-agent-responsibilities.md)
- [Listing 文案 Agent 设计](docs/listing-content-agent-design.md)
- [Listing MCP 接入接口](docs/listing-mcp-integration.md)
- [DeepSeek 统一 LLM 接入](docs/deepseek-integration.md)
- [广告异常诊断与运营代办多 Agent 设计](docs/advertising-anomaly-multi-agent-design.md)
- [报关单填写自动化](docs/customs-declaration-automation.md)
- [任务创建幂等键](docs/idempotency.md)

## 本地验证

```bash
uv sync --extra dev --no-editable
uv run pytest
```

## Docker 部署

```bash
docker compose up --build -d
```

Docker Compose 会启动 PostgreSQL、Agent API 和前端。任务创建幂等记录及运营助手会话记忆都保存在 PostgreSQL 数据卷中，API 重启后仍可继续同一会话。详细规则见[任务创建幂等键](docs/idempotency.md)。

已导入广告报表的只读查询也可作为内部 MCP 工具运行。使用 `./scripts/start-nl2sql-mcp.sh` 启动 stdio Server；它查询团队共享的广告数据，仍要求 `DATABASE_READONLY_URL`。

生产服务器部署请使用 [生产部署指南](docs/production-deployment.md)。该方案通过 Nginx + Certbot
提供 HTTPS；PostgreSQL 和 API 不开放公网端口，发布包不包含密钥。

## 启动真实对话系统

```bash
./scripts/start-dev.sh
```

该命令同时启动 Python Agent API（`127.0.0.1:8000`）和 Next.js 前端（`localhost:3001`）。
前端通过同源代理创建真实 LangGraph 任务并订阅阶段 SSE，不再使用模拟任务数据。
如果 `.env` 中没有 `DEEPSEEK_API_KEY`，页面会显示真实未配置状态，不会生成假结果。

广告异常诊断的核心报表可配置领星 Open API：在本机 `.env` 填写 `LINGXING_OPEN_API_BASE_URL`、`LINGXING_OPEN_API_APP_ID` 和 `LINGXING_OPEN_API_APP_SECRET`。负责人、店铺和父 ASIN 的选择目录由内部 MCP 的固定只读白名单获取，不能通过环境变量替换上游工具。这些凭证不得提交或粘贴到聊天、日志和文档中。

启用模型前，在 `.env` 中填写 `DEEPSEEK_API_KEY`。总控请求理解、总控结果汇总和
Listing 文案角色共享同一个 DeepSeek 客户端，默认模型为 `deepseek-v4-flash`。

## 核心目录

```text
src/amazon_ops/
├── customs_declaration/ # 确定性报关解析、商品目录与OOXML模板生成
├── listing/       # Listing Agent、MCP Transport、关键词证据网关与条件边子图
├── llm.py         # DeepSeek 共享客户端、JSON 输出与错误边界
├── deepseek_runtime.py # 总控和专业 Agent 的统一模型角色组装
├── graph.py       # LangGraph 主控流程
├── events.py      # 阶段事件协议、状态机、存储与实时订阅
├── interfaces.py  # 请求理解、专家和汇总接口
├── models.py      # 统一输入输出协议
├── prompts.py     # 版本化的主控系统提示词与运行时上下文
├── presenter.py   # JSON → 紧凑 Outline / 前端树形 JSON
├── routing.py     # 确定性路由规则
├── sse.py         # SSE编码、心跳和Last-Event-ID重放
└── state.py       # 共享 State
```

## 主控提示词

主控包含两个模型角色，对应两份独立系统提示词：

- `REQUEST_INTERPRETER_SYSTEM_PROMPT`：只理解意图、范围、日期和风险，不做业务分析；
- `RESULT_AGGREGATOR_SYSTEM_PROMPT`：只汇总专家证据，严格区分事实、假设和建议。

系统提示词保持稳定；当前时间、用户默认配置、店铺目录和专家结果通过上下文构造函数注入。

## JSON Outline

`DataPresenter` 不改变或替代原始领星 JSON，只生成两个展示副本：

- 紧凑 Outline：供专家模型阅读大型报表；
- 树形 JSON：供前端展示折叠层级。

```python
from amazon_ops import DataPresenter, OutlineOptions

rows = [
    {"asin": "A", "date": "2026-08-01", "sales": 100, "profit": 20},
    {"asin": "A", "date": "2026-08-02", "sales": 120, "profit": 22},
]

result = DataPresenter().present(
    rows,
    OutlineOptions(
        root_label="产品表现",
        group_by=["asin", "date"],
        field_order=["sales", "profit"],
    ),
)

print(result.outline)
tree_json = result.tree.model_dump(mode="json")
```

组件默认限制记录数、层级、子节点数量和字段长度，并屏蔽密钥、令牌、密码等敏感字段。将内容传给模型时使用 `build_untrusted_outline_context`，明确标记 MCP 内容为不可信数据。

## 基于阶段的 SSE

前端消费的是稳定的业务阶段，不直接依赖 LangGraph 节点：

```text
understanding → planning → analysis → verification? → synthesis
                      └──────────────→ waiting_input
                                      waiting_approval
```

公共事件类型保持精简：

- `run.started`
- `stage.started`
- `stage.progress`
- `stage.completed`
- `stage.waiting`
- `stage.failed`
- `run.completed`
- `run.failed`
- `heartbeat`（仅传输层，不持久化）

专家、领星网关和模型可通过临时注入的 `StageReporter` 在节点内部实时发进度：

```python
from amazon_ops import get_stage_reporter

def invoke(self, task, scope, state):
    reporter = get_stage_reporter(state)
    if reporter:
        reporter.emit(
            "tool.progress",
            tool="query_product_performance_asin_lists",
            records_received=300,
        )
    # 继续分析并返回 SpecialistResult
```

框架无关的 SSE 生成器支持事件重放、心跳和等待状态断流：

```python
from amazon_ops import SSE_RESPONSE_HEADERS, stage_sse_stream

async def body():
    async for chunk in stage_sse_stream(
        event_hub,
        run_id,
        last_event_id=request.headers.get("Last-Event-ID"),
        heartbeat_seconds=15,
    ):
        yield chunk

# 在 FastAPI / Starlette 中可返回：
# StreamingResponse(body(), headers=SSE_RESPONSE_HEADERS)
```

每个事件使用 `run_id:sequence` 作为 SSE `id`。客户端重连时携带 `Last-Event-ID`，服务端先重放遗漏事件，再切换实时订阅。进入 `waiting_input` 或 `waiting_approval` 后发送 `stage.waiting` 并结束当前连接；用户补充或审批后使用同一 `run_id` 恢复。

`InMemoryEventHub` 仅用于本地开发和单进程测试。生产环境应按同一接口替换为 Redis 实时发布和 Postgres 事件持久化，避免进程重启后丢失重放数据。

## 前端控制台

`frontend/` 基于 Next.js、shadcn/ui 和 TypeScript，已经包含：

- 亚马逊经营总览；
- Agent 阶段运行详情；
- 分析历史；
- 与后端阶段协议一致的 SSE 客户端；
- 用于联调界面的本地模拟 SSE 接口。

```bash
cd frontend
npm install
npm run dev
```

打开 `http://localhost:3000/dashboard/overview`。真实接口接入前，可访问
`/dashboard/agent-runs/demo` 查看完整的阶段流转效果。

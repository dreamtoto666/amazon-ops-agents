# 计划：修复代码审查 P1 问题（竞品对标 / 团队知识库 / 可观测性）

## Context

上一轮已完成 P0 修复（断点续跑路由判断与检查点状态重建），新增
`tests/test_chat_checkpoints.py` 6 个用例，全量 218 个测试通过。P0 修完留下的是
「不致命但会误导用户或运维」的一批问题：错误被伪装成正常状态、默认时间窗指向未来、
可观测性客户端泄漏、恢复流程写入重复数据。

本轮目标是**消除误导性状态与资源泄漏**。不新增接口、不改 SSE 事件名、不改阶段顺序。

已用真实请求 / 真实代码验证的现状（非推测）：

| 现象 | 证据 |
|---|---|
| 损坏 ZIP 返回 503 而非 422 | 实测：完全非 ZIP → `TeamKnowledgeError`(422)；头部合法但数据损坏 → `TeamKnowledgeUnavailable`(503) |
| 默认周窗口指向未来 | `_latest_week_start()` 在 2026-09-10（周四）返回 2026-09-06，`inspect_campaign` 加 6 天得 2026-09-12，超出今天 2 天 |
| 知识库组件静默吞错误 | `team-knowledge-upload.tsx` 两处 `.catch(() => undefined)` |
| 恢复会重复写入助手消息 | `_execute` 每次 `memory.append(role="assistant")`；`conversation_messages` 无 `(owner_id, conversation_id, turn_id, role)` 唯一约束 |
| 本地验证环境就绪 | compose `agent-postgres-1` 健康、5432 可连；`.env` 已配 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` |

## 已确认决策

| # | 决策 | 取值 |
|---|---|---|
| Q1 | 范围 | **只做 5 项**（下表 1–5）；P2 清单移入「不在本轮范围」 |
| Q2 | 默认时间窗语义 | **最近 7 个完整天**：`[今天-7, 今天-1]`，绝不包含今天 |
| Q2b | `compare_traffic_keywords` 的 `endDay` | **一并收敛**到同一辅助函数（见下方「行为变更」） |
| Q2c | 时区 | **保持现状**（服务器本地 `date.today()`），作为已知限制记录，不在本轮引入时区决策 |
| Q3 | 恢复重复消息 | **方案 C**：仅代码层幂等，不建唯一索引、不改动任何历史数据 |
| Q4 | Langfuse | 有真实实例，做真机联调验证 |

### ⚠️ 行为变更（需要 review 时确认）

`compare_traffic_keywords` 现在把 `endDay = 今天-7` 传给 Sif；收敛到统一窗口后
变成 `endDay = 今天-1`，即**关键词明细的取数区间会向后挪 6 天**（覆盖最近一周而不是
一周前那天）。这是本轮唯一会改变真实取数结果的改动，其余都是修 bug 或资源管理。
如果你希望这处保持原样，把它单独排除即可（其余四项不受影响）。

## Approach

### 1. 团队知识库前端：把「失败」和「没有」区分开

现状：接口失败 → `status` 保持 `undefined` → 页面只显示「团队知识库」四个字，与
「尚未上传」无法区分；`/api/auth/me` 失败还会让真管理员的上传入口静默消失。

- 两处 `fetch` 改为显式错误状态，失败写入可读中文提示。
- 按后端 `KnowledgeStatus.status` 完整分支渲染，把已返回但前端从不使用的 `error`
  字段显示出来：`empty` / `indexing` / `active` / `failed` / `unavailable`。
- 管理员身份无法确认时给出提示，而不是当作「非管理员」静默隐藏上传入口。

### 2. 损坏 ZIP 归属为客户端错误（422）

`_parse_vault` 只在构造 `ZipFile` 时捕获 `BadZipFile`；`archive.read(info)` 抛出的
落到 `upload()` 的通用 `except Exception` → `TeamKnowledgeUnavailable` → 503。
在 `_parse_vault` 内补齐捕获并转成 `TeamKnowledgeError`：

- `zipfile.BadZipFile`（CRC 不符 / 截断）—— 包裹 `archive.read(info)`
- **加密 ZIP**：不宽泛捕获 `RuntimeError`（会吞掉无关的运行时错误），改为在读取前
  检查 `info.flag_bits & 0x1`，命中则直接抛 `TeamKnowledgeError("ZIP 包含加密笔记，无法读取")`
  （已实测：Python 对加密条目抛 `RuntimeError: File ... is encrypted`）

**已实测确认不需要加固**：ZIP 解压炸弹由 Python 自身 CRC 校验挡住 —— 篡改声明大小会
导致 CRC 不匹配而报错，且读取被声明大小截断，`MAX_UNCOMPRESSED_BYTES` 守卫足够。
只加一行注释记录这个结论，不新增校验代码。

### 3. 默认时间窗：最近 7 个完整天

- 用 `_recent_complete_window(today: date | None = None) -> tuple[date, date]` 取代
  `_latest_week_start()`，返回 `(today - 7, today - 1)`，返回 `date` 对象而非字符串，
  由调用方按各自参数名格式化（`start_date`/`end_date`/`endDay`/`date`）。
- `today` 可注入，使 7 个星期几全部可测。
- `inspect_campaign`：`start_date = today-7`、`end_date = today-1`。
- `inspect_ad_group` 的单个 `date`：取窗口结束日 `today-1`（最近一个完整天）。
- `compare_traffic_keywords` 的 `endDay`：收敛为同一窗口的结束日。
- 断言 `start <= end < today`，从结构上保证不再产生未来日期。

### 4. Langfuse 客户端单例化 + 退出时 flush

`_langfuse_client()` 每次调用都新建 `Langfuse` + `httpx.Client`，而
`langfuse_observation` 的调用点之一是 `llm.py` 的每一次结构化 LLM 调用；一次聊天会
创建十几个客户端，且从无 flush / close。

- `observability.py` 改为模块级缓存单客户端（加锁）。
- **best-effort 语义不变**：未配置凭据仍返回 `None`；任何异常都不得影响业务。
- 新增 `shutdown_observability()`（调用 `client.shutdown()`），由 `api.py` 注册到既有
  shutdown 事件链，保证进程退出前 flush 已缓冲的观测。
- 保持既有数据边界：不上传业务提示词、图片、原始模型输出、MCP 原始返回内容或密钥。

### 5. 恢复期重复写入助手消息（方案 C：代码层幂等）

`conversation_messages.owner_id` 是 `NOT NULL`（`_scope()` 把 `None` 归一为
`"anonymous"`）。`append` 在 `turn_id` 非空时按 `(scope, role, turn_id)` 幂等：

- `InMemoryConversationStore`：写入前查重。
- `PostgresConversationStore`：改为
  `INSERT ... SELECT ... WHERE NOT EXISTS (SELECT 1 FROM conversation_messages WHERE owner_id=%s AND conversation_id=%s AND turn_id=%s AND role=%s)`。
- `turn_id` 为空时不查重（无可识别身份），行为与现状一致。
- 冲突时**保留先写入的一条**（用户已经看到的那条），恢复产生的重试结果被丢弃。

**不建唯一索引、不做任何历史数据清理** —— 因此无需迁移，也不存在「历史已有重复导致
建索引失败、启动报错」的风险。代价是并发下非严格，但恢复由租约（`claim_stale`）串行化，
同一 turn 内的并发写入实际不会发生。

## Files to modify

| 文件 | 改动 |
|---|---|
| `frontend/src/features/operations-chat/components/team-knowledge-upload.tsx` | 错误状态 + 全状态分支渲染 |
| `src/amazon_ops/team_knowledge.py` | `_parse_vault` 捕获 `BadZipFile` / `RuntimeError` |
| `src/amazon_ops/competitor_research.py` | `_recent_complete_window` 取代 `_latest_week_start`；3 个调用点收敛 |
| `src/amazon_ops/observability.py` | 客户端单例 + `shutdown_observability()` |
| `src/amazon_ops/api.py` | 注册 shutdown flush（单行） |
| `src/amazon_ops/memory.py` | `append` 按 `(scope, role, turn_id)` 幂等（两个实现） |
| `tests/test_observability.py`（新建） | 客户端只建一次 / shutdown 清理 / 未配置不建 |
| `tests/test_team_knowledge.py` | 损坏 ZIP → `TeamKnowledgeError` |
| `tests/test_api.py` | 管理员上传损坏 ZIP → 422（保留既有 403 用例） |
| `tests/test_competitor_research.py` | 7 个星期几参数化 + 无未来日期断言 |
| `tests/test_memory.py` | 幂等断言（同 turn/role 一条、不同 role 各一条、turn_id 空不查重） |
| `docs/competitor-advertising-agent-design.md` | 「输入与边界」补一句默认时间窗 |

**顺带（同一文件 1 行，可删）**：`competitor_research.py` 的 `PRIVATE_METRICS`
是只定义未使用的死代码（设计文档明确「除非 MCP 实际返回否则标记不可得」，所以不是漏了
过滤）。我本来已把它排进改动，但它属于 P2 清理、不在你指定的 5 项内 —— **请 review 时
确认要不要一起删**，不确认就保留。

## Reuse

- `KnowledgeStatus`（`src/amazon_ops/team_knowledge.py`）已含 `error` 字段，前端只需消费
- 无需新增错误类型：`TeamKnowledgeError` → 422 的映射已存在于
  `api.py::upload_team_knowledge_vault`，本轮只是让 `_parse_vault` 抛出它
- 前端沿用同目录 `advertising-report-import.tsx` 的 `message` + `text-destructive` 模式，
  不引入 service 层（该功能目录本就没有 `api/`）
- `tests/test_team_knowledge.py` 已有 `vault_zip()` 构造器
- `tests/test_competitor_research.py` 已有 `FakeTransport` / `_service()`
- `tests/test_memory.py` 已有 `_add_completed_turns()` 与既有多轮构造模式
- 后端生命周期沿用 `api.py` 既有 `app.router.add_event_handler("shutdown", ...)` 链

## Steps

- [ ] 1. `observability.py`：模块级单例客户端（加锁）+ `shutdown_observability()`；
      `langfuse_observation` 改用单例，best-effort 语义不变
- [ ] 2. `api.py`：把 `shutdown_observability` 注册进 shutdown 链
- [ ] 3. `tests/test_observability.py`：mock `observability.Langfuse` 计数 —— 连续多次
      观测只构造一次；shutdown 调用 flush 且清空缓存；无凭据时不构造；构造抛异常时
      观测仍让业务继续
- [ ] 4. `team_knowledge.py::_parse_vault`：包裹 `archive.read(info)` 捕获 `BadZipFile`
      → `TeamKnowledgeError`；读前用 `info.flag_bits & 0x1` 拒绝加密条目（不用宽泛的
      `except RuntimeError`）；补注释记录 CRC 即解压炸弹守卫
- [ ] 5. 测试：损坏 ZIP → `TeamKnowledgeError`；管理员 `POST` → 422
- [ ] 6. `competitor_research.py`：新增 `_recent_complete_window(today=None)`，删除
      `_latest_week_start`；`inspect_campaign` / `inspect_ad_group` /
      `compare_traffic_keywords` 三个调用点收敛
- [ ] 7. `tests/test_competitor_research.py`：7 个星期几参数化，断言
      `start <= end < today` 且三个调用点使用同一窗口
- [ ] 8. `memory.py`：`append` 幂等（InMemory 查重 + Postgres `WHERE NOT EXISTS`）
- [ ] 9. `tests/test_memory.py`：幂等三条断言
- [ ] 10. 前端 `team-knowledge-upload.tsx`：显式错误状态 + 全状态分支 + 管理员身份
      不可确认时的提示
- [ ] 11. `uv run pytest`、`cd frontend && npm run typecheck`
- [ ] 12. 真机验证（见 Verification）
- [ ] 13. `docs/competitor-advertising-agent-design.md` 补默认时间窗说明

## Verification

1. **自动化**：`uv run pytest`（当前 218 通过，预期新增用例后全绿）；
   `cd frontend && npm run typecheck`
2. **本地接口**（AGENTS.md 要求涉及 API 时用真实请求确认状态码与最小字段）：
   - 管理员 `POST /api/team-knowledge/vault` 传损坏 ZIP → **422**，`detail` 为中文可读原因
   - 非管理员同一请求 → **403**（既有行为不得回归）
   - `GET /api/team-knowledge/vault` → 200，字段含 `status` / `error`
   - 损坏 ZIP 用「本地生成 + 篡改 `PK\x03\x04` 后 size 字段」构造，与我在 review 阶段
     复现 503 时用的同一手法，确保打的是同一条代码路径
3. **真库验证 `append` 幂等**（compose postgres 已在跑）：
   同一 `(owner_id, conversation_id, turn_id, role)` 连续 `append` 两次 → 表内只有一行；
   跑一次真实聊天后确认没有重复助手消息
4. **Langfuse 真机联调**（`.env` 已配置，我不读取、不打印、不提交任何 key）：
   - 跑一次聊天，确认整个过程只创建一个客户端实例
   - 在 Langfuse 看到唯一 trace 根 + 嵌套观测
   - 触发进程优雅退出，确认 shutdown flush 后无数据丢失
   - 复核上传内容仍不含提示词 / 图片 / 原始模型输出 / MCP 原始返回内容
5. **前端手工**：四种状态各验证一次（空库 / 索引中 / 已就绪 / 失败）；**中断后端进程**，
   确认显示「无法读取」而不是空白
6. **时间窗**：对 7 个星期几各跑一次断言，确认 `start <= end < today`

## 明确不在本轮范围

- `_as_specialist_result` 部分失败时 summary 只列成功工具、`confidence=1.0` 且
  `evidence_refs` 可能为空
- `chat_runs.finish()` 不校验租约归属（被接管后原 worker 迟到写入会覆盖新结果）
- `QueryScope.competitor_asins` 上限 10 / specialist 上限 5 / prompt 说 1–5 三处不一致
- `httpx[socks]` 无任何 SOCKS/proxy 代码路径（回退 `pyproject.toml` + `uv.lock`）
- BFF 代理在 `Content-Type` 缺失时写入空串（`frontend/src/app/api/team-knowledge/vault/route.ts`）
- `advertising-report-import.tsx` 本次被删掉的输入说明文字（`text-xs text-muted-foreground`）
- `chat_runs.py` 的单行 `if ...: return` 风格与仓库其余部分不一致
- Campaign / 广告组归属建模（`_campaign_result` 恒取 `scope.own_asin`，无法表达
  「这个 campaign 属于某个竞品」）—— 需要 API 形态决策，建议单独一轮
- 用 LangGraph 自带 checkpointer 替换手工检查点（上一轮的「彻底方案」）
- 时区：默认时间窗仍按服务器本地日期计算

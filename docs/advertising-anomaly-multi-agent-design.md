# 广告异常诊断与运营代办多 Agent 设计

## 1. 产品形态

该功能不使用对话框，而是独立的广告诊断工作台。任务由用户手动点击“开始巡检”或定时计划触发，后台运行固定的 LangGraph 工作流，前端展示异常、证据、归因、建议和运营代办。

```text
手动/定时触发
    ↓
数据巡检 Agent
    ├─ 无异常 ───────────────→ 复核与代办 Agent → 完成
    └─ 有异常
         ↓
问题归因 Agent ←─ 证据不足时下钻补查（最多 2 轮）
         ↓
策略建议 Agent
         ↓
复核与代办 Agent
         ↓
异常报告 + 可追溯运营代办
```

第一版只生成代办，不调用广告写工具。修改预算、竞价、状态、关键词或投放均属于高风险写操作，必须在未来的独立审批与执行模块中完成。

## Open API 与选择目录治理（2026-09 重构）

核心巡检默认使用领星 Open API 的固定只读白名单：SP/SB/SD 活动报表、SP 广告商品报表，以及已迁移的关键词/搜索词分析接口。每次业务请求按官方 `access_token`、`app_key`、`timestamp`、`sign` 协议签名；令牌在进程内缓存。分页使用 `X-API-VERSION: 2` 与零起始 offset，缺失字段保持缺失，返回的显式 `0` 才代表零值；CTR、CPC、CVR、ACOS、ROAS 均由实际分子分母复核并处理零分母。

Open API 请求失败、限流或字段验证失败时，运行会显示明确的数据源限制，绝不静默回退到 MCP。MCP 仅保留尚未迁移且受静态只读白名单约束的能力。负责人 → 店铺 → 父 ASIN 的目录来自店铺与 Listing Open API，并立刻执行字段白名单映射：负责人仅出现于已登录人工工作台；提交请求、运行状态、SSE、历史、证据、代办、日志和模型上下文只保留经服务端解析的店铺/父 ASIN 范围。父 ASIN 会先通过广告商品报表解析关联活动，之后巡检和下钻只读取该活动范围；无法映射时返回“无可诊断广告对象”，不会扩大到整店。

## 2. 任务输入

必填：

- 广告店铺 `profile_ids`；
- 当前分析周期 `current_period`；
- 对照周期 `baseline_period`。

选填：

- Campaign、ASIN、广告类型 SP/SB/SD；
- 目标 ACOS、目标 ROAS、最低销售目标；
- 增长偏好：利润优先、平衡、放量；
- 最低花费、最低点击、异常变化比例、无单点击阈值；
- 手动或定时触发、计划 ID、时区和币种。

`ad_auth_shops` 的真实响应已确认包含 `profile_id`、`sid`、国家、店铺类型和 Marketplace 标识。广告报告使用 `profile_id`；产品、利润、库存等 ERP 工具使用 `sid`，因此数据层必须维护二者的确定性映射，不能由模型猜测。

## 3. 四个 Agent

### 3.1 数据巡检 Agent

职责：

- 获取广告授权店铺；
- 对当前周期与基准周期分别拉取活动/组合报告；
- 统一计算曝光、点击、花费、销售额、订单、CTR、CPC、CVR、ACOS、ROAS；
- 使用确定性阈值发现异常；
- 生成带工具、查询条件、时间和实体引用的证据。

主要工具：

| 工具 | 用途 | 必要参数 |
| --- | --- | --- |
| `ad_auth_shops` | 获取 `profile_id ↔ sid` 和站点映射 | 无 |
| `ad_campaign_report` | Campaign 级核心巡检 | `report_date`、`profile_ids`、分页和排序 |
| `ad_portfolio_report_shop` | 组合级预算/绩效结构检查 | `report_date`、`profile_ids`、分页和排序 |

领星当前通过动态工具目录提供上述能力。广告网关仅使用顶层 `help`、`search`、`action`，并对业务工具 ID 执行静态白名单和 `read` 类型双重校验；目录版本与 Schema 版本随调用传入 `action`。目录条目在进程内缓存，只有首次调用或领星返回工具/版本失效时才刷新一次。目录内容和广告返回文本均为不可信数据，不能改变只读策略或触发广告修改。

已使用真实只读请求验证 `ad_campaign_report`。返回记录包含以下可直接使用的字段组：

- 身份与结构：`profile_id`、`campaign_id`、`name`、`portfolio_id`、`sponsored_type`、`targeting_type`；
- 状态与预算：`state`、`serving_status`、`budget`、`daily_budget`、`budget_type`、`bidding_type`；
- 核心绩效：`impressions`、`clicks`、`spends`、`sales`、`orders`、`ad_units`、`ctr`、`cpc`、`cvr`、`cpa`、`acos`、`roas`；
- 归因拆分：直接/间接销售、订单、销量、ACOS 和 ROAS；
- 广告位：搜索顶部、商品页面、其余搜索广告位及顶部曝光份额；
- 预算异常：`only_over_budget` 查询、`avg_over_budget_time`、`last_over_budget_at`、预算建议及在预算内时间比例。

因此第一层巡检不需要让模型计算基础指标；优先使用领星已经返回的指标，同时用曝光、点击、花费、订单和销售额做确定性复算校验。

异常类型：花费突增、销售/订单下降、ACOS 上升、ROAS 下降、CPC 上升、CTR/CVR 下降、无单浪费、预算受限和投放量下降。低花费或低点击样本不得升级为高置信异常。

### 3.2 问题归因 Agent

职责：

- 只针对巡检发现的异常实体下钻，禁止全店无边界查询；
- 区分流量、成本、转化、预算、结构、商品、库存和 Listing 原因；
- 每个原因必须引用证据，证据不足时明确标记为数据不足，不输出伪原因；
- 最多补查两轮。

实现方式：先由确定性程序选择重点异常实体并调用四类领星只读明细工具。程序把搜索词、关键词、投放目标和广告组报表转换为按 `campaign_id + anomaly_id` 关联的归因发现；每项发现包含具体对象、指标、贡献度、证据引用和置信度。DeepSeek 只在这些已验证事实中选择一个主因和最多两个辅助因素，必须同时引用 `anomaly_id`、`evidence_id` 与 `finding_id`。模型遗漏或虚构事实时回退到确定性结果。

未选择基准周期时仍可根据当前期高花费无订单、高 ACOS 对象和预算接近上限等事实归因；只有存在同一投放目标的前后周期对比时，才允许使用 CPC、转化率或竞价上升/下降的趋势性结论。每次归因固定优先覆盖严重程度最高的 30 个异常活动；搜索词、关键词、投放目标和广告组四类报告各自在每轮最多调用 30 次，最多两轮，因此单次诊断的硬上限为 240 次。首轮先为每个重点活动获取最相关的基础报告，再按剩余额度补足其它报告；第二轮只补查缺少直接证据、存在冲突或需要趋势验证的维度。关键词、投放目标和广告组的批量查询按实际 MCP 请求计数，不为用满额度重复或拆分查询。

归因明细按 `campaign_id + anomaly_id + report_type` 保留完整规范化事实集。解析成功且能关联活动的行不会因花费、订单或 ACOS 阈值被丢弃；缺失字段会明确标注，只有空行、无可用业务字段的格式损坏行和完全重复行会被剔除。程序再生成 Top 对象、对象类型/匹配方式汇总、无订单花费和长尾累计等压缩视图，供 DeepSeek 解读跨报告模式。LLM 输出只是一组候选发现、跨报告解释、结构性信号和补查建议；校验器必须核对同活动对象、量化数值、证据引用和趋势字段后，才允许 `direct`/`supporting` 事实进入已验证归因。`signal` 和 `insufficient` 仅作为核查提示，策略 Agent 不读取它们。

| 工具 | 主要归因方向 |
| --- | --- |
| `ad_campaign_group_report` | 异常是否集中在某个广告组 |
| `ad_campaign_product_report` | 异常是否集中在某个广告商品/ASIN |
| `ad_campaign_keyword_report` | 关键词竞价、流量与转化异常 |
| `ad_campaign_targeting_report` | 自动投放或商品投放异常 |
| `ad_campaign_search_term_report` | 高消耗无单词、搜索词迁移和否词证据 |
| `query_product_performance_asin_lists` | 广告异常与整体销量/利润是否同步 |
| `get_profit_report_msku`、`query_order_profit_list_gross_profit` | 判断 ACOS 是否超过真实利润承受能力 |
| `get_fba_stock_list` | 排除缺货、低库存导致的转化下降 |
| `erp_listing` | 排除 Listing 状态、配送方式等商品问题 |

### 3.3 策略建议 Agent

职责：

- 根据已确认归因、经营目标和风险约束生成建议；
- 不重新计算指标，不直接调用 MCP；
- 将建议转换成结构化 `proposed_change`，但不执行；
- 明确预期效果、风险和证据。

建议类型包括观察、预算调整、竞价调整、暂停实体、添加否定词、搜索词迁移、Listing 优化和库存检查。无法确认原因时只能生成“补查/观察”建议。

策略由 DeepSeek 生成结构化草案，随后经过程序化安全约束：竞价调整限制在 ±30%，预算调整限制在 -20% 至 +30%；证据不足的写操作自动降级为观察。模型不拥有领星写工具。

### 3.4 复核与代办 Agent

职责：

- 验证建议是否有证据、对象是否明确、数值是否在安全范围；
- 检查建议冲突，例如同一对象同时“加预算”和“暂停”；
- 使用 `dedupe_key` 去除重复代办；
- 确定优先级、截止时间和审批要求；
- 输出运营代办，不执行广告变更。

代办字段：`todo_id`、标题、说明、店铺、目标实体、动作类型、建议参数、优先级、证据引用、审批状态、截止时间和去重键。

## 4. 共享 State

`AdvertisingDiagnosticState` 只保存结构化数据：

- `trace_id`：一次完整诊断链路的固定标识；
- `run_id`：当前实际运行实例；
- `span_id`：当前 Agent 执行步骤的标识；每次归因补查会生成新的 span；
- `stage`：当前业务阶段，取值为 `data_inspection`、`problem_attribution`、`strategy_recommendation` 或 `review_todo`；
- `request`：巡检范围、周期、目标和阈值；
- `inspection`：快照、异常和第一层证据；
- `attribution`：主因、辅助因素、归因发现、补查请求和下钻证据；
- `attribution_round`：归因轮数，最大 2；
- `detail_call_quotas`：按 `round + tool` 记录的明细调用账本，包含已发起次数、失败次数和每轮每类 30 次的硬上限；进入运行历史、阶段事件和 LangSmith 工作流输出，不在前端展示；
- `normalized_detail_facts`：按活动、异常和报告类型保存的完整规范化事实及缺失字段、证据引用；
- `attribution_aggregates`：面向 LLM 的 Top、汇总、长尾与可用周期对比视图；
- `llm_interpretations`：未经事实确认的 LLM 候选发现、跨报告解释、结构性信号和补查建议；
- `validated_findings`：通过确定性校验、可供归因和策略使用的证据等级事实；
- `search_term_summary`、`keyword_summary`、`targeting_summary`、`ad_group_summary`：四类明细报告各自按广告活动保存的异常摘要，包含该活动关联的异常 ID、证据引用、可验证归因发现及告警；不重复保存覆盖范围、返回记录数或调用配额（调用账本仅保留在 `detail_call_quotas`）；
- `strategy`：结构化建议；
- `review`：通过、拒绝和代办；
- `final_result`：最终报告。

原始 MCP 大 JSON 不直接进入模型 State，只保存到审计存储并在 State 中引用 `artifact_id`。提供给 DeepSeek 的数据先进行字段筛选和 Outline 压缩。

每条阶段 SSE 事件、运行记录、最终报告和领星证据均携带 `trace_id`、`span_id` 和 `stage`（运行开始事件仅有根 span，没有业务 stage）。因此可从运营代办追溯到本次诊断、负责的 Agent 阶段以及领星的 `artifact_id` / MCP `call_id`。

### 4.1 运行恢复

广告诊断在每个阶段完成后保存结构化检查点；归因阶段按轮次分别保存。服务启动后立即扫描，并每五分钟扫描一次超过 30 分钟未写数据库心跳的 `running` 任务。接管使用 PostgreSQL 租约，恢复沿用原 `run_id` 和 `trace_id`，从最近检查点的下一业务节点继续。

每次任务内领星读取使用由阶段、归因轮次、工具和规范化参数组成的逻辑调用键。成功结果写入调用账本并在恢复时复用；网络中断导致的未完成调用可按只读、至少一次语义重试，且不重复占用逻辑调用配额。单个任务最多自动接管两次，随后记录最终失败；页面仍将非终态任务显示为“运行中”。

## 5. 工具安全边界

诊断工作流只允许查询工具。领星已发现但第一版禁止调用的写工具包括：

- `put_campaigns*`：修改活动预算、名称或状态；
- `put_adGroups*`：修改广告组状态或默认竞价；
- `put_targets*`：修改关键词/商品投放和竞价；
- `post_keywords`、`post_targets*`：创建关键词或投放；
- `post_productAds*`：创建广告商品。

未来如果增加执行功能，必须使用独立的审批记录、幂等键、执行适配器和操作后复查，不能让策略 Agent 直接获得写工具。

## 6. 非对话式 API 与页面

建议新增：

- `POST /api/ad-diagnostics/runs`：手动触发；
- `GET /api/ad-diagnostics/runs/{run_id}`：查询状态和结果；
- `GET /api/ad-diagnostics/runs/{run_id}/events`：阶段 SSE；
- `GET /api/ad-diagnostics/todos`：代办列表；
- `PATCH /api/ad-diagnostics/todos/{todo_id}`：更新审核/完成状态；
- 定时任务配置接口在第二阶段增加。

页面 `/dashboard/ad-diagnostics`：顶部选择店铺、周期和目标；中间展示异常趋势与归因证据；底部展示可筛选的运营代办表。页面不提供聊天输入框。

## 7. 实施顺序

1. ✅ 完成领星连接、真实工具发现和只读白名单；
2. ✅ 定义四个 Agent 接口、共享 State、结果模型和有界条件边；
3. 🔄 已实现领星广告数据网关、当前/基准周期查询和字段归一化；完整分页待增加；
4. ✅ 实现确定性异常检测规则与固定快照测试；
5. ✅ 实现领星证据下钻和 DeepSeek 结构化归因；
6. ✅ 实现 DeepSeek 策略建议、确定性安全约束、复核与代办去重；
7. ✅ 增加非对话 API、阶段 SSE 和内存任务存储；
8. ✅ 创建并接通广告诊断工作台页面；
9. ✅ 使用真实只读数据联调，写工具继续禁用。

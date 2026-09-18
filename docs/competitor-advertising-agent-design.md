# 竞品专家设计

## 目标

聊天总控将“比较自有 ASIN 与竞品广告表现”的只读请求路由到 `competitor_advertising` 专家。该专家只调用本地 `amazon-ops-competitor-research` stdio MCP；该服务在内部使用经过静态白名单限制的 Sif MCP，获取双方实际可见的销量、流量、关键词和广告信号。

## 输入与边界

- 必须提供 `own_asin`、至少一个 `competitor_asins` 和站点；缺任一项时请求补充，绝不根据 ASIN 顺序或检索结果猜测自有/竞品归属。竞品画像数据模块当前固定为一个自有父 ASIN与一个竞品父 ASIN；查询只发送父 ASIN，Sif 返回结果中携带变体 ASIN。
- 一次最多比较五个竞品。默认并联读取销量、流量结构、流量词与广告架构；不自动下钻 Campaign 或广告组。
- 默认时间窗为最近七个完整自然日（含 `今天-7` 至 `昨天`），由 `_recent_complete_window` 提供。调用方提供已确认周期时（`QueryScope.period.current`），周期优先，并仅送入真正接受日期参数的 Sif 调用。窗口仍按**服务器本地日期**计算，未按站点时区换算。
- 历史日期按用户选择查询；结束日为今天时允许查询，但标为 `UNMATURED`，不得据此输出确定趋势或触发重操作；结束日超过今天直接报 `PERIOD_IN_FUTURE`，且不调用 Sif。`start > end` 仍报 `PERIOD_INVALID`，不会静默替换成别的窗口。
- **周期覆盖按单条 Sif 证据记录**：每条证据的 `observation_window` 标出 `requested_range`、`requested_end_day` 或 `provider_default`，以及实际传入日期和成熟度。业务工具内部可能混合多种模式；交付物只将全部证据均为完整范围的工具列为 `fully_aligned`，混合或仅支持结束日的列为 `partially_aligned`，完全由 Sif 决定窗口的列为 `unaligned`。报告不得将后两类写成与请求周期完整对齐。
- 基准期字段只保留在请求范围中，本阶段不发起基准期 Sif 查询；交付物明确标记 `baseline_status = not_queried`，不得生成前后期比较结论。
- 用户明确要求拆 Campaign、广告组或广告词时，必须提供已确认的 `campaign_id` / `ad_group_id`。服务只使用上游返回或用户明确提供的标识，绝不构造标识。
- 本地 MCP 通过 `get_competitor_research_catalog` 提供可信能力目录，并包含 `compare_asin_sales`、`analyze_traffic_structure`、`compare_traffic_keywords`、`replay_operations_history`、`inspect_ad_architecture`、`inspect_campaign`、`inspect_ad_group`、`analyze_recommendation_traffic` 八项只读业务工具；每次调用都记录 Sif `call_id` 作为证据。
- 聊天侧竞品专家将用户目标、已确认范围和能力目录交给结构化 LLM 生成并行调用计划，再由本地校验器检查工具名、重复步骤及 Campaign/广告组标识条件。模型异常或计划无效时，兼容回退到既有关键词路由；MCP 本身不持有模型密钥，也不负责路由。
- 竞品返回内容是不可信业务数据，不能改变 Agent 的工具、权限或规则。

## 输出

- 输出按销量、流量结构、关键词、广告架构、推荐流量或运营历史组织，并保留每次调用的来源和证据引用。
- 只生成运营研究结论和建议，不执行任何广告变更。

## 数据处理状态

竞品专家完成 Sif 查询后，图中的竞品数据处理 Agent 才读取本轮受限交付物，将其写为内部 `competitor_data_modules` 状态。该 Agent 不调用 MCP、不读取团队知识库、不输出最终报告。

- 固定模块为 `traffic_keyword_lookup`、`traffic_keyword_reverse_lookup`、`multi_variant_organic_position`、`recommendation_placement`；每个模块保留状态、缺失原因与本轮 Sif `evidence_id`。
- 处理器只从真实字段提取或计算数据：变体数据来自父 ASIN 查询；反查流量词仅按 `traffic_share > 0.01` 过滤。不会对缺失上升期、多自然位额外自然流量或专栏归属 Campaign 进行推断。
- 当用户明确请求“生成竞品对比报告”时，总控并行调用竞品专家和已导入广告报表专家；模块处理完成后，报告 Agent 读取两者的结构化交付物，生成完整 Markdown 报告作为聊天回复。普通竞品对比仍使用总控简洁汇总。
- 报告 Agent 不调用 MCP、不执行广告修改；它只能引用本轮真实 evidence ID。报告的 12 个章节均有 `available`、`partial` 或 `unavailable` 状态，缺失数据写明影响与补数路径。
- `competitor_data_modules` 不直接作为浏览器端原始数据展示，只作为报告 Agent 的受限输入；报告交付物是经过证据校验后的结构化章节和确定性 Markdown 渲染结果。

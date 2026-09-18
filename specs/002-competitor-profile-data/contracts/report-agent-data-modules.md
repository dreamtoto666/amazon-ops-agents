# Report Agent Contract: Competitor Data Modules

报告 Agent 输入状态中的 `competitor_data_modules` 必须是 JSON 对象，且仅由本轮竞品研究的实际 Sif 证据生成。

## Invariants

- 根对象必须包含一个自有父 ASIN、一个竞品父 ASIN 和站点。
- 四个模块名固定；模块不可用时仍保留模块对象与 `unavailable` 状态。
- 每条业务记录必须引用至少一个本轮 `evidence_id`。
- 所有 `>1%` 判断使用来源返回的同一占比单位；单位无法识别时模块不可用。
- 报告 Agent 不得恢复被确定性处理器排除的记录，也不得据缺失值计算或补写数据。
- 专栏 `campaign_count` 仅来自同记录的直接归属 `campaign_ids`；禁止使用 ASIN 总 Campaign 数。

## Replacement

`competitor_profiles` 从图状态、汇总上下文、报告上下文、提示词和报告证据收集器中移除。任何仍读取旧字段的调用必须在同一变更中改读 `competitor_data_modules`。

# 竞品广告报告：四模块模板设计

**Status**: Implemented  
**Scope**: 仅定义报告 Agent 如何消费 `competitor_data_modules`；不改变数据获取条件，不新增前端内容。

## 设计目标

报告模板应与数据模块一一对应。每个模块独立成段，并将实际返回记录转换为固定列的 Markdown 表格；前端直接渲染报告正文中的表格。每个表格只使用该模块明确返回的记录与 `evidence_ids`；模块为 `partial` 或 `unavailable` 时，模板只说明真实缺口及影响，不能推算、补零或迁移其他模块的数据。

## 模块与最终报告映射

| 数据模块 | 最终报告模块 | 固定展示对象 | 不可用时的固定处理 |
| --- | --- | --- | --- | --- |
| `traffic_keyword_lookup`（查流量词） | `traffic_keyword_lookup` | 自有父 ASIN、竞品父 ASIN、Listing 自然/广告分布、广告渠道分布、返回变体流量分布 | 写明未返回的父 ASIN 或变体字段；不以子 ASIN 或其他 Listing 替代。 |
| `traffic_keyword_reverse_lookup`（反查流量词） | `traffic_keyword_reverse_lookup` | 双方 `total_traffic_ratio > 1%` 的关键词、总/自然/广告流量占比；自有 ASIN 额外展示明确返回的上升期变化 | 上升期未明确返回时，只说明“上升期变化未返回”，不以普通环比替代。 |
| `multi_variant_organic_position`（查多变体自然位） | `multi_variant_organic_position` | 双方自然贡献占比 `>1%` 的词、自然流量、自然流量占比、多自然位额外自然流量及各变体自然位明细 | 缺少 Listing 该词自然流量或变体自然流量占比时，整段标为不可用；不得根据排名或变体数估算。 |
| `recommendation_placement`（查推荐专栏） | `recommendation_placement` | 自有当前周期及过去得分最高 3 个周期；竞品当前周期；专栏名、流量占比、直接归属广告活动数 | 缺少专栏直接 `campaign_ids` 时，整段标为不可用；不得使用 ASIN 总 Campaign 数。 |

## 单模块报告模板契约

每个模块模板按以下顺序生成，不得自行追加筛选、排序或业务结论。

```text
### 模块：查流量词 | 反查流量词 | 查多变体自然位 | 查推荐专栏
数据表：使用该模块规定的固定列；一条 records 记录对应一行，变体记录对应一行
**数据分析**：仅对该表中实际返回的可比记录作事实性比较
数据限制：仅列出模块 missing_reasons
证据引用：仅列出模块或记录内 evidence_ids
```

`available`：可基于记录进行事实性对比。  
`partial`：可陈述已返回的事实，但必须在同段说明缺失项，且不得把缺失项写成比较结论。  
`unavailable`：仍输出该模块固定表头；没有记录时输出一行“未返回 / 不可得”，不输出数字对比或建议，只说明缺失字段、影响和需要的 Sif 原始字段。

## 数据分析规则

### 自有 ASIN 与竞品 ASIN（四模块通用）

- 仅在模块为 `available` 且双方同口径数据完整时比较；`partial` 或 `unavailable` 不能把记录缺失表述为“我方没有”。
- 对可匹配的关键词或推荐专栏名称，可指出“竞品有、我方未返回”的记录。
- 对同一明确指标，可指出竞品数值高于我方的记录，必须同时写明指标名和双方数值。
- 不将不同 ASIN 变体直接配对；不把单项数值更高扩展成整体“效果更好”、因果关系或经营质量结论。

### 自有 ASIN 当前与历史周期（仅 `recommendation_placement`）

- 只有“查推荐专栏”可做自身历史对比；其余三个模块不得自行构造前后期比较。
- 对同一推荐专栏名称，当前与历史记录均可比时，可指出“过去周期有、当前未返回”以及“当前流量占比低于历史”的情况，并列出周期和对应数值。
- 当前或历史覆盖不完整时，只说明已返回的数据与缺口，不得根据缺失记录下结论。

表格通用规则：

- 仅输出字段白名单内的列；字段未返回时表格单元格写 `未返回`，不可填 `0` 或估算值。
- 比例按来源实际单位展示；处理器明确为 0–1 比例时，报告展示为百分比。单位不明确时不生成该表。
- 不增加排序、Top N、去重或筛选；数据处理器已经执行的 `>1%` 条件保持原样。
- 每行在最后一列列出该行的 `evidence_ids`，以便前端与报告证据校验一致。

## 各模板的字段白名单

### `traffic_distribution`

- `asin_role`、`parent_asin`
- `listing_natural_traffic.{score,ratio}`、`listing_ad_traffic.{score,ratio}`
- `advertising_traffic_distribution.sp`、`sp_recommend`、`sb`、`sbv` 的 `{score,ratio}`
- `variants[].variant_asin`、`variant_attributes`、`total_traffic_ratio`、`natural_traffic_ratio`、`ad_traffic_ratio`、`sp_ratio`、`sp_recommend_ratio`、`sb_ratio`、`sbv_ratio`

固定正文顺序：父 ASIN 自然-广告流量中文概述 → 父 ASIN 广告流量对比表 → 子 ASIN 流量分布表 → 数据分析。

父 ASIN 广告流量对比表：

| ASIN 角色 | 父 ASIN | SP（常规）广告流量得分 | SP（常规）流量占比 | SP（推荐）广告流量得分 | SP（推荐）流量占比 | SB（常规）广告流量得分 | SB（常规）流量占比 | SBV 广告流量得分 | SBV 流量占比 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

子 ASIN 流量分布表：

| # | ASIN 角色 | 父 ASIN | 变体 ASIN | 总流量占比 | 自然-广告流量分布 | 自然流量占比 | SP（常规）流量占比 | SP（推荐）流量占比 | SB（常规）流量占比 | SBV 流量占比 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

### `keyword_traffic_comparison`

- `asin_role`、`parent_asin`、`keyword`
- `total_traffic_ratio`、`natural_traffic_ratio`、`ad_traffic_ratio`
- 自有 ASIN 的 `growth_period_change`（仅非空时）
- `evidence_ids`

| 对象 | 父 ASIN | 流量词 | 全部流量占比 | 自然流量占比 | 广告流量占比 | 相比上升期变化（仅自有） | 证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |

### `multi_variant_organic`

- `asin_role`、`parent_asin`、`keyword`
- `natural_traffic`、`natural_traffic_ratio`、`multi_organic_extra_natural_traffic`
- `evidence_ids`

截图确认的计算口径：

```text
多自然位额外自然流量
= Listing 该关键词的自然流量
×（1 − 自然流量占比最高的变体的未四舍五入占比）
```

等价地，使用未四舍五入的变体占比计算：

```text
多自然位额外自然流量
= Listing 该关键词的自然流量
× 其余获得自然位变体的自然流量占比之和
```

例如截图中 `garage door seal top and sides`：Listing 自然流量为 `2,137`；主变体自然流量占比显示为 `90%`，其余变体显示为 `9.3%`、`0.87%`。处理器必须以原始未四舍五入占比计算，再按页面数值规则展示为 `+218`，不能以展示后的百分比重算。

| 对象 | 父 ASIN | 关键词 | 给 Listing 的自然流量 | 自然流量占比 | 多自然位额外自然流量 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |

变体自然位明细表（每个关键词嵌套一张表）：

| 变体 ASIN | 变体属性 | 自然流量占比 | 获得自然位的天数 | 平均自然排名 | 近 30 天销量 | 自然位趋势 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |

### `recommendation_placement`

- `asin_role`、`parent_asin`、`period`、`placement_name`
- `traffic_ratio`、`campaign_count`
- `evidence_ids`

| 对象 | 父 ASIN | 周期 | 推荐专栏名称 | 流量占比 | 广告活动数量 | 证据 |
| --- | --- | --- | --- | --- | --- | --- |

`campaign_ids` 仅用于处理器内部验证 `campaign_count`，不在报告正文输出。

## 最终报告范围

- 最终报告只生成上述四个模块，不再生成旧的决策、广告结构、商品投放、预算或执行清单章节。
- 每个模块独立输出“模块标题 → 对应表格 → 数据分析 → 系统渲染的数据状态、限制和证据”。

## 非目标

- 不改变前端界面或 SSE 事件。
- 不增加关键词、变体或专栏的额外过滤条件。
- 不把广告 Agent 的建议或未验证推断写入数据模块模板。

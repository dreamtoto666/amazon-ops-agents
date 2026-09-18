# Research: 竞品画像数据获取

## 已确认的 Sif 能力

- `ops_get_listing_traffic_overview` 返回自然/广告流量、SP、SP 推荐、SB、SBV 和推荐专栏的 `score`、`ratio`。
- `ops_get_listing_traffic_structure` 以父 ASIN 查询时返回变体维度；每个维度项包含可识别的变体 ASIN，以及自然、广告、SP、SP 推荐、SB、SBV 的分布数组。
- `ops_get_listing_keyword_distribution` 以父 ASIN 查询时返回各变体的 ASIN、总流量、自然/广告流量及 SP、SP 推荐、SB、SBV 占比。
- `ops_get_asin_traffic_trend` 返回日期序列和 `totalScore`，可确定历史得分最高的三个过去周期。
- `ops_get_asin_traffic_trend_detail` 返回词级当前/前值/变动字段；上升期仅在数据源明确标记时可写入模块。
- `ads_get_asin_campaign_contribution_overview` 返回 ASIN 级 Campaign 列表，不含推荐专栏归属。

## 外部契约门禁

### Decision: 父 ASIN 作为唯一请求输入

**Rationale**: 调用方只传父 ASIN；Sif 的 Listing 流量结构会返回变体维度与变体 ASIN。系统不要求调用方传子 ASIN，也不推断父体；为计算多自然位额外流量，系统仅会对该父体响应中实际返回的子 ASIN 发起同周期只读自然位明细查询。

### Decision: 专栏 Campaign 数必须直接返回

**Rationale**: 用户明确拒绝用 ASIN 总 Campaign 数替代推荐专栏归属数量。新增/开通的只读 Sif 工具必须为每个推荐专栏返回 `campaign_ids`；未提供时模块必须为 `unavailable`。

### Required provider fields

- 变体流量：`parent_asin`、`variant_asin`、变体属性、总/自然/广告/SP/SP 推荐/SB/SBV 占比、周期。
- 关键词：关键词、总/自然/广告流量占比；数据源标记的上升期与变化；自然贡献、自然贡献占比、多自然位额外自然流量。
- 推荐专栏：父 ASIN、周期、专栏名称、流量占比、直接归属 `campaign_ids`。

## Rejected alternatives

- 要求调用方同时传递子 ASIN：父 ASIN 查询已返回所需变体范围，增加子 ASIN 会制造不必要的输入分支。
- 用 ASIN 总 Campaign 数填充每个推荐专栏：不满足“专栏归属数”口径。
- 从位置变动或历史得分推导多自然位额外流量：属于未返回数据的估算，违反规格；仅可使用同周期子变体自然流量原始得分按已记录公式计算。

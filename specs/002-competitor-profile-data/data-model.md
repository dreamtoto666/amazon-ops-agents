# Data Model: 竞品画像四模块数据包

## 根对象：`CompetitorDataModules`

| Field | Description |
| --- | --- |
| `own_parent_asin` | 自有父 ASIN |
| `competitor_parent_asin` | 竞品父 ASIN |
| `marketplace` | 站点 |
| `traffic_keyword_lookup` | 查流量词模块 |
| `traffic_keyword_reverse_lookup` | 反查流量词模块 |
| `multi_variant_organic_position` | 查多变体自然位模块 |
| `recommendation_placement` | 查推荐专栏模块 |

所有模块都有 `status`（`available`、`partial`、`unavailable`）、`evidence_ids` 和 `missing_reasons`。`partial` 只表示来源实际缺失或调用失败，不能表示新加的业务过滤。

## 模块记录

- **查流量词父 ASIN 记录**：`listing_natural_traffic`、`listing_ad_traffic` 均为 `{score, ratio}`；`advertising_traffic_distribution` 包含 `sp`、`sp_recommend`、`sb`、`sbv`，每项均为 `{score, ratio}`。
- **变体流量记录**：`parent_asin`、`variant_asin`、变体属性、`total_traffic_ratio`、`natural_traffic_ratio`、`ad_traffic_ratio`、`sp_ratio`、`sp_recommend_ratio`、`sb_ratio`、`sbv_ratio`、`period`、`evidence_ids`。
- **反查流量词记录**：`asin_role`、`keyword`、`total_traffic_ratio`、`natural_traffic_ratio`、`ad_traffic_ratio`、自有记录可选 `growth_period_change`、`evidence_ids`。处理器仅对 `total_traffic_ratio > 1%` 保留记录。
- **多自然位记录**：`asin_role`、`keyword`、`natural_traffic`、`natural_traffic_ratio`、`multi_organic_extra_natural_traffic`、`evidence_ids`。处理器仅对 `natural_traffic_ratio > 1%` 保留记录。
- **推荐专栏记录**：`asin_role`、`period`、`placement_name`、`traffic_ratio`、`campaign_ids`、`campaign_count`、`evidence_ids`。处理器仅对 `traffic_ratio > 1%` 保留记录，`campaign_count` 必须等于去重后的直接归属 `campaign_ids` 数量。

## 生命周期

`Sif 原始证据 → 受白名单限制的业务交付物 → 确定性四模块解析/校验 → competitor_data_modules 图状态 → 报告 Agent 上下文 → 报告证据校验`。

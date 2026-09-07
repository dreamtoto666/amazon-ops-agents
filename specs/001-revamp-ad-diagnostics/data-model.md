# Data Model: 广告异常诊断重构

## SelectionDirectory

仅供已授权人工工作台使用的短生命周期选择目录。它不是诊断证据，也不进入模型、SSE、运行记录或代办。

| Field | Description | Rules |
|---|---|---|
| `version` | 目录快照版本 | 用于检测过期选择。 |
| `stores` | 可访问店铺 | 仅包含当前用户授权范围。 |
| `shop_ref` | 不透明店铺范围引用 | 浏览器和提交请求使用；服务端解析其真实店铺标识。 |
| `label` | 店铺显示名 | 仅供人工界面。 |
| `responsibles` | 该店铺下可筛选负责人 | 仅供人工界面；不进入运行请求。 |
| `responsible_ref` | 不透明负责人引用 | 仅用于目录内级联与服务端授权校验。 |
| `label` | 负责人显示名 | 仅供当前有权限的人工用户显示。 |
| `products` | 可诊断父 ASIN | 限于该负责人和店铺的有效关联。 |
| `parent_asin` | 父 ASIN | 可作为产品范围的业务标识。 |
| `product_ref` | 不透明产品范围引用 | 用于防止跨目录组合提交。 |

禁止字段：人员电话、邮箱、完整用户 ID、组织结构、备注、权限详情和未映射的上游字段。

## DiagnosticScope

一次运行的身份无关有效范围。

| Field | Description | Validation |
|---|---|---|
| `shop_ref` / resolved profile IDs | 已授权广告店铺范围 | 必须来自未过期选择目录。 |
| `product_refs` / resolved parent ASINs | 已选产品范围 | 必须属于已选店铺及负责人关联范围。 |
| `resolved_child_asins` or ad-object scope | 实际执行过滤范围 | 必须在巡检和所有下钻中一致生效。 |
| `current_period` | 当前诊断周期 | 起止日期有效。 |
| `baseline_period` | 可选对照周期 | 仅在可比时支持趋势结论。 |
| `goal`, `thresholds`, `trigger` | 业务目标与运行参数 | 沿用当前验证约束。 |

负责人姓名和 `responsible_ref` 不属于 `DiagnosticScope`，不进入 Agent State 或历史记录。

## AdvertisingReportFact

由 Open API 或受控 MCP 适配器输出的统一广告报表事实。

| Field group | Required behavior |
|---|---|
| Identity | 广告类型、店铺、活动、广告组、关键词/投放/搜索词及关联产品范围。 |
| Performance | 曝光、点击、花费、销售、订单及可由实际分子分母验证的指标。 |
| Period | 当前或基准期；保留数据完整性和归因口径。 |
| Provenance | 端点/工具、安全查询摘要、抓取时间、记录数、证据引用。 |
| Missingness | 缺失与零值必须区分；不能以缺失代替零或反之。 |

## DiagnosticRun and derived entities

`DiagnosticRun` 维持现有阶段状态：`data_inspection → problem_attribution → strategy_recommendation → review_todo`。它关联身份无关 `DiagnosticScope`、调用账本和阶段事件。

`Anomaly` 关联一个或多个 `Evidence`；`AttributionFinding` 只能引用已验证事实；`Recommendation` 只描述人工动作；`Todo` 以稳定去重键关联建议、证据和人工处理状态。所有实体继续携带 `run_id`、`trace_id`、`span_id` 与业务 `stage`。

## State transitions

```text
directory loaded → valid scope selected → run accepted → data inspection
    → attribution (or review when no anomaly) → strategy → review/todo → terminal result

invalid/expired directory selection → rejected before run creation
Open API unavailable or quota exhausted → completed with data limitation, or failed with explicit stage error
```

运行创建前必须验证选择组合；不能在运行中发现负责人/店铺/产品组合不合法后再继续。父 ASIN 范围解析失败时拒绝启动或明确显示无可诊断广告对象。

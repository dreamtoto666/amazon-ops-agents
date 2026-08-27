# Listing 文案 Agent 设计

> 建议名称：`listing_content`  
> 中文名称：Listing 研究与文案 Agent  
> 文档状态：V0.1  
> 调研日期：2026-08-13

## 1. 设计结论

第一版设计为一个专业 Agent，内部采用固定流水线完成“产品事实确认 → 市场与关键词调研 → 关键词策略 → 文案生成 → 合规检查”。暂时不拆成“关键词 Agent”和“文案 Agent”，避免两个 Agent 之间重复传递大量关键词数据，也便于追踪每一句文案使用了哪些产品事实和关键词证据。

该 Agent 可以直接调用卖家精灵 MCP 和 Sif MCP，但两者只提供市场、流量、关键词和竞品证据，不能替代产品真实资料。产品材质、尺寸、功能、认证、适用人群等事实必须来自用户资料、品牌资料或领星中的当前 Listing。

Agent 第一版只生成 Listing 草稿和修改建议，不直接发布到亚马逊。将文案写回 Listing 属于高风险写操作，必须由总控 Agent 转入人工审批。

## 2. 角色定位

Listing 文案 Agent 是面向亚马逊商品详情页的研究与内容生成专家，负责：

- 理解产品卖点和目标消费者；
- 从卖家精灵和 Sif 搜索真实关键词与竞品流量信号；
- 形成可审计的关键词优先级和埋词方案；
- 编写主标题、副标题、五点描述、商品描述和后台 Search Terms；
- 检查关键词覆盖、可读性、产品事实和亚马逊规则；
- 返回草稿、关键词依据、风险和待确认信息。

它不负责利润分析、库存分析、广告调价、商品发布或 Listing 自动修改。

## 3. 数据来源及分工

### 3.1 产品事实源

产品事实是文案的最高优先级约束，建议按以下顺序取值：

1. 用户上传或确认的产品 Brief、说明书、包装和认证材料；
2. 品牌内部 PIM 或商品资料库；
3. 领星 MCP 返回的当前 Listing、ASIN、SKU 和商品属性；
4. 用户明确确认的会话信息。

竞品文案、卖家精灵数据和 Sif 数据不得作为本产品属性事实。竞品写了某个功能，不代表自己的产品也具备该功能。

### 3.2 卖家精灵 MCP

主要用于广度搜索和关键词市场指标：

- `asin_detail`：读取目标或竞品 ASIN 的基本资料；
- `traffic_source`：识别竞品流量结构；
- `traffic_keyword`：反查竞品核心流量词；
- `keyword_order`：检查真实出单词或关键词表现；
- `keyword_miner`：从种子词拓展相关词、长尾词和市场指标。

卖家精灵关键词挖掘可返回搜索量、购买量、购买率、商品数、广告竞品数、供需比、PPC 竞价、标题密度、相关度等字段。调用时只请求必要的 `returnFields`，避免将大体积原始 JSON 全部交给模型。

### 3.3 Sif MCP

主要用于深度验证和流量结构判断：

- `market_get_asin_keyword_signals`：检查 ASIN 的关键词排名、流量贡献和竞争位置；
- `ops_get_listing_keyword_distribution`：检查 Listing 的关键词流量分布；
- `market_get_asin_profile`：读取竞品价格、评分、评论数、变体等基本面；
- `market_get_asin_aba_footprint`：检查竞品在 ABA 关键词上的卡位；
- `market_screen_keyword_opportunities`：筛选低竞争或高需求关键词；
- `market_discover_competitors`：验证关键词搜索结果中的竞品格局；
- `market_get_keyword_root_competitors`：识别某个词根下的核心竞品。

Sif 还覆盖市场、流量和广告三类信号。Listing Agent 默认只使用关键词、市场和 Listing 流量相关工具，不应无目的调用完整广告工具链。

### 3.4 亚马逊规则源

规则不能永久写死在提示词中，应建立可版本化的 `ListingPolicyRegistry`，按站点、类目和生效日期加载：

- 标题长度和格式；
- Item Highlights 是否适用；
- 五点和描述字符限制；
- 后台 Search Terms 限制；
- 禁止字符、重复词和促销措辞；
- 类目特定的禁限售、认证和声明要求。

例如，美国站自 2026 年 7 月 27 日起，除媒体类目外标题需要控制在 75 个字符以内，并新增最多 125 个字符的 Item Highlights。Agent 必须使用任务执行时的有效规则，而不是沿用旧的 200 字符通用规则。

## 4. 输入要求

### 4.1 必填字段

- `marketplace`：目标站点；
- `language`：目标文案语言；
- `category`：商品类目或 Product Type；
- `product_brief`：完整商品信息，必须包含：
  - `product_name`：商品名称；
  - `product_type`：商品类型；
  - `brand`：品牌名；
  - `core_features`：至少 3 项已确认核心功能或卖点；
  - `materials`：至少 1 项已确认材质；
  - `specifications`：至少 2 项已确认规格，例如尺寸、重量、容量、兼容性或包装数量；
  - `target_audiences`：至少 1 类目标用户；
  - `use_cases`：至少 2 个使用场景；
  - `package_contents`：至少 1 项包装内容；
  - 可选的型号、变体属性、认证、安全警告和禁用声明；
- `content_request`：需要主标题、副标题、五点描述、商品描述、Search Terms 或完整 Listing；
- 以下至少一种研究入口：
  - 当前产品 ASIN；
  - 1～5 个竞品 ASIN；
  - 1～3 个种子关键词。

### 4.2 优化已有 Listing 时的附加字段

- 当前主标题、副标题、五点描述、商品描述和 Search Terms；
- 当前 ASIN/SKU；
- 允许保留和必须修改的内容；
- 品牌语气和禁用词；
- 已有搜索词、转化或广告词数据（若有）。

### 4.3 必须追问的情况

以下信息缺失时不得直接生成最终稿：

- 无法确定站点或语言；
- 无法确定产品是什么；
- 缺少最基本的产品事实；
- 只有竞品 ASIN，没有自己的产品差异；
- 用户要求医疗、功效、环保、安全或认证声明，但没有证明材料；
- 变体之间的尺寸、颜色、数量或兼容性无法确认。

输入校验通过条件边执行。任何必填商品信息不足时，任务进入 `waiting_input`，不调用卖家精灵或 Sif，也不生成文案。系统一次性返回所有缺失字段和补充问题；用户补充后使用同一个 `request_id` 合并原请求并重新进入 `validate_input`。

## 5. Agent 内部工作流

```text
接收任务
   ↓
验证产品事实和必填范围
   ├─ 缺失 → waiting_input → 返回缺失字段 → 用户补充后重新验证
   ↓
规划 MCP 查询
   ↓
卖家精灵广度搜索 ──┐
                    ├─→ 关键词归一化与证据合并
Sif 深度验证 ──────┘
   ↓
检查有效关键词数量
   ├─ 少于 30 且补搜次数 < 3 → 补充搜索 ─┐
   │                                     └─→ 返回归一化与数量检查
   ├─ 少于 30 且补搜次数 = 3 → 返回 insufficient_keyword_evidence
   └─ 达到 30 → 关键词分层与埋词计划
   ↓
生成 Listing 草稿
   ↓
事实、合规、字符数和重复度检查
   ├─ 未通过 → 定向修订一次
   ↓
返回草稿、证据和风险
```

### 5.1 事实验证

先建立 `ProductFactSheet`。每条事实记录：

- 标准字段名；
- 值和单位；
- 来源；
- 是否已由用户确认；
- 是否允许用于公开文案；
- 证据引用。

只有状态为 `confirmed` 的事实可以写入 Listing。没有证据的内容进入 `unverified_claims`，不得静默写入文案。

### 5.2 查询规划

根据输入选择最小查询集合：

**有当前 ASIN 和竞品 ASIN：**

1. 反查当前 ASIN 的已有关键词覆盖；
2. 反查 1～5 个主要竞品的流量词；
3. 从共同核心词中拓展长尾词；
4. 使用 Sif 验证排名、流量贡献和竞品卡位；
5. 只对候选 Top 词补查市场机会。

**新产品、没有当前 ASIN：**

1. 使用种子词挖掘关键词；
2. 发现该词下的主要竞品；
3. 反查竞品关键词；
4. 使用产品事实做相关性过滤；
5. 对最终候选词做双源验证。

### 5.3 并发策略

- 卖家精灵和 Sif 是独立数据源，可以跨数据源并行；
- 同一数据源内部是否并发由 MCP 网关的限流配置决定，不由 Agent 猜测；
- 多个独立竞品查询可以进入有界并发队列；
- 默认每个数据源设置独立并发上限、超时、指数退避和抖动重试；
- 对相同站点、ASIN、关键词和数据周期建立缓存，避免重复扣费；
- 一个数据源失败时继续使用另一个来源，但输出必须标记 `single_source_only`。

具体并发数应通过配置和压测确定，不在提示词中硬编码。

### 5.4 关键词归一化

将两套 MCP 返回的数据转换为统一的 `KeywordEvidence`：

```json
{
  "keyword": "adjustable phone stand",
  "normalized_keyword": "adjustable phone stand",
  "marketplace": "US",
  "relevance": 0.94,
  "search_demand": 0.81,
  "purchase_signal": 0.68,
  "competition": 0.52,
  "traffic_contribution": 0.73,
  "sources": ["seller_sprite", "sif"],
  "evidence_refs": ["artifact-ss-12", "artifact-sif-08"],
  "decision": "primary"
}
```

归一化处理包括：

- 统一大小写、单复数和无意义标点；
- 合并高度相似但保持搜索意图不同的词；
- 识别品牌词、竞品品牌词、属性词、场景词、受众词和问题词；
- 过滤与产品事实不符的词；
- 保留来源冲突，不用平均值掩盖差异；
- 禁止将竞品商标放入自己的主标题、副标题、五点描述或 Search Terms。

### 5.5 关键词分层

文案生成前必须形成 **30 个有效且互不重复的关键词**。不再使用数量区间，也不能为了凑满数量加入低相关词。

固定分层如下：

| 分层 | 数量 | 主要用途 |
| --- | ---: | --- |
| `primary` | 2 | 表达商品本质，优先用于主标题 |
| `secondary` | 8 | 覆盖重要属性、用途或人群，用于副标题和五点描述 |
| `long_tail` | 12 | 覆盖细分场景和购买意图，用于五点描述和商品描述 |
| `backend_only` | 8 | 相关但不适合自然写入前台，用于 Search Terms |
| **合计** | **30** | 文案生成所需的完整关键词证据池 |

`excluded` 不计入 30 个有效关键词，但必须单独保存排除原因。

每个有效关键词必须：

- 与已确认的产品事实直接相关；
- 至少有一个 MCP 数据源的原始证据；
- 没有竞品品牌、商标、敏感或误导风险；
- 与其他入选词具有独立的搜索表达或搜索意图；
- 具有明确的分层、入选原因和预定使用位置。

两个 `primary` 主关键词必须同时得到卖家精灵和 Sif 的证据支持。其他关键词允许单源入选，但必须降低置信度并保留数据来源。

先执行相关性硬门槛，再综合需求、购买信号、竞争度、流量贡献、趋势和双源一致性评分。如果合格候选超过 30 个，只保留评分最高且搜索意图覆盖最完整的 30 个。

如果经过关键词拓展和补充验证后仍不足 30 个，Agent 返回 `insufficient_keyword_evidence`，说明还差多少词，并请求用户补充种子词或竞品 ASIN；不得提前编写文案，也不得用低相关词补足。

#### 条件边与补充搜索循环

首轮关键词研究不计入补充循环。首轮完成后，通过 LangGraph 条件边检查有效关键词数量：

```python
def after_keyword_evaluation(state):
    if state["valid_keyword_count"] >= 30:
        return "build_keyword_plan"
    if state["supplement_search_round"] < 3:
        return "supplement_keyword_search"
    return "insufficient_keyword_evidence"
```

`supplement_keyword_search` 每执行一次，将 `supplement_search_round` 加 1，然后返回关键词归一化和数量检查节点。因此一次任务最多执行：

- 1 次首轮搜索；
- 3 次条件边补充搜索；
- 合计最多 4 次搜索批次。

任何一轮达到 30 个有效关键词后立即退出循环，不再继续调用 MCP。超过 30 个时只保留得分最高且搜索意图覆盖完整的 30 个。

补充搜索不能简单重复上一轮查询，应根据当前缺口定向执行：

| 缺少的分层 | 补充搜索方向 |
| --- | --- |
| `primary` | 对商品核心词进行卖家精灵与 Sif 双源交叉验证 |
| `secondary` | 扩展属性、材质、功能、用途和目标人群词 |
| `long_tail` | 组合使用场景、问题需求和购买意图词 |
| `backend_only` | 补充同义表达、简称和不适合前台自然展示的相关词 |

每轮补充搜索必须携带 `seen_keywords` 和已执行查询的指纹，避免重复关键词和重复扣费。建议在 State 中增加：

```text
valid_keyword_count
supplement_search_round
keyword_gaps
seen_keywords
executed_query_fingerprints
keyword_evidence
```

高搜索量不能覆盖低相关性。相关性不足的词即使搜索量很大也必须排除。

### 5.6 文案生成顺序

生成顺序固定为：

1. 主标题；
2. 副标题；
3. 五点描述；
4. 商品描述；
5. 后台 Search Terms；
6. 关键词覆盖表；
7. 中文策略说明。

系统内部统一使用 `subtitle` 表示副标题。发布适配器根据目标站点和类目，将它映射到 Amazon 的 Item Highlights 或相应字段；如果目标类目不支持独立副标题，则保留为草稿字段并提示用户，不得擅自拼入主标题。

每个内容片段应引用它所使用的产品事实和关键词证据。文案应自然表达消费者利益点，不能为了覆盖词而堆砌关键词。

完整文案的固定输出范围为：

| 字段 | Schema 字段 | 要求 |
| --- | --- | --- |
| 主标题 | `title` | 1 条；表达商品本质、品牌和核心差异点 |
| 副标题 | `subtitle` | 1 条；补充材质、用途或关键选择信息，不重复主标题 |
| 五点描述 | `bullet_points` | 必须正好 5 条；每条聚焦一个独立利益点 |
| 商品描述 | `description` | 1 条；完整说明使用场景、优势和必要注意事项 |
| 搜索词 | `search_terms` | 1 组；仅用于后台，不采用面向消费者的句子结构 |

除非用户另行要求，第一版不生成 A+ 页面、图片文案、QA 或广告文案。

### 5.7 自动质检

质检器使用确定性规则优先于模型自评，至少检查：

- 主标题、副标题、五点描述和商品描述字符数；
- 禁止字符和词语重复；
- 品牌名、规格、数量和单位是否与 FactSheet 一致；
- 是否出现没有证据的绝对化、医疗、认证、安全或环保声明；
- 是否包含竞品品牌或疑似商标；
- 关键词覆盖率和重复密度；
- Search Terms 是否重复前台关键词、包含标点或超过限制；
- 语言、拼写、语法、移动端可读性；
- 竞品文案相似度，防止复制或近似改写。

自动修订最多执行一次。第二次仍未通过则返回 `needs_review`，不得伪装成可发布稿。

## 6. 输出协议

建议返回结构：

```json
{
  "status": "completed",
  "agent": "listing_content",
  "summary": "已完成美国站手机支架 Listing 草稿",
  "draft": {
    "title": "...",
    "subtitle": "...",
    "bullet_points": ["...", "...", "...", "...", "..."],
    "description": "...",
    "search_terms": "..."
  },
  "keyword_strategy": {
    "primary": [],
    "secondary": [],
    "long_tail": [],
    "backend_only": [],
    "excluded": []
  },
  "coverage": {
    "target_keywords": 30,
    "covered_keywords": 30,
    "coverage_rate": 1.0
  },
  "policy_checks": [],
  "unverified_claims": [],
  "evidence_refs": [],
  "artifacts": [],
  "recommended_actions": [],
  "errors": []
}
```

前端默认按“主标题 → 副标题 → 五点描述 → 商品描述 → Search Terms”的顺序展示最终草稿，再提供可展开的关键词依据、字符数、质检结果和数据来源。不得把原始 MCP JSON 直接倾倒到对话中。

每条关键词证据中的 `placements` 只允许使用以下位置格式：

```json
[
  { "field": "title", "occurrences": 1 },
  { "field": "subtitle", "occurrences": 1 },
  { "field": "bullet_points", "index": 2, "occurrences": 1 },
  { "field": "description", "occurrences": 1 },
  { "field": "search_terms", "occurrences": 1 }
]
```

这样可以明确回答某个关键词最终进入了主标题、副标题、第几条五点、商品描述还是 Search Terms。

## 7. 与总控 Agent 的协作

### 7.1 路由调整

新增专业 Agent：

```text
SpecialistName.LISTING_CONTENT = "listing_content"
```

建议路由：

| 用户请求 | domain | action | 路由 |
| --- | --- | --- | --- |
| “帮我写这个商品的 Listing” | `listing` | `create` | Listing 文案 Agent |
| “优化这个 ASIN 的标题和五点” | `listing` | `recommend` | Listing 文案 Agent |
| “检查关键词是否覆盖完整” | `listing` | `diagnose` | Listing 文案 Agent |
| “直接把新文案发布到亚马逊” | `listing` | `update` | 先生成草稿，再进入人工审批 |

当前 `listing` 被路由到市场风险 Agent，需要改为 Listing 文案 Agent。若用户只询问 Listing 风险、竞品或排名，可由 Listing 文案 Agent 主负责，并按需追加市场风险 Agent 验证。

### 7.2 总控传入的任务

总控只传递标准化目标、范围、产品事实、内容要求和风险等级，不指定具体 MCP Tool。Listing Agent 自己根据研究入口选择卖家精灵和 Sif 工具。

### 7.3 阶段事件

对外仍使用总控的 `analysis` 阶段，Listing Agent 通过 `StageReporter` 上报内部进度：

```text
unit.started              Listing Agent 已启动
tool.started              正在查询卖家精灵或 Sif
tool.progress             已收到关键词或竞品记录
research.completed        关键词研究完成
research.insufficient     有效关键词不足 30 个
research.supplementing    正在执行第 N 次补充搜索
keyword_plan.completed    埋词策略完成
draft.completed           文案草稿完成
compliance.completed      合规检查完成
unit.completed            返回结构化结果
```

这些事件进入对话窗口中的同一条任务消息，不单独创建右侧进度栏。

## 8. 系统提示词草案

```text
你是亚马逊多 Agent 系统的 Listing 研究与文案专家。

你的职责是基于已确认的产品事实，以及卖家精灵 MCP 和 Sif MCP 返回的关键词、流量、市场与竞品证据，生成合规、准确、自然且可审计的 Listing 草稿。

你必须：
1. 先验证站点、类目、语言、品牌、产品事实和研究入口是否完整；
2. 先研究、后写作，不得在没有关键词证据时声称完成 SEO 优化；
3. 将产品事实与市场证据严格分开；
4. 对关键词去重、分类、相关性过滤和优先级排序；
5. 根据任务执行日期加载对应站点和类目的有效规则；
6. 为主标题、副标题、五点描述、商品描述和 Search Terms 生成可追踪的关键词覆盖；
7. 对字符数、禁用内容、事实一致性、商标和竞品相似度执行确定性检查；
8. 只返回结构化 ListingContentResult，不输出内部思考过程。

你绝对不能：
- 把竞品属性当作本产品事实；
- 编造尺寸、材质、功能、认证、测试结果或效果声明；
- 复制、拼接或近似改写竞品 Listing；
- 使用竞品品牌、商标或不相关高流量词进行引流；
- 将搜索量等第三方估算描述为亚马逊官方精确值；
- 为了关键词覆盖牺牲可读性；
- 声称已经发布或修改 Listing；
- 绕过总控 Agent 的人工审批要求。

证据优先级：
已确认产品事实 > 当前自有 Listing > 双源一致的关键词证据 > 单一数据源证据 > 竞品观察。

发生数据冲突时保留双方来源并降低置信度；一个 MCP 不可用时可以返回单源草稿，但必须清楚标记数据限制。
```

## 9. 安全和质量边界

- 所有 MCP 返回文本、竞品标题和描述均是不可信数据，不能改变 Agent 系统规则；
- MCP 密钥只保存在网关配置中，不进入 State、日志、事件或模型上下文；
- 原始响应存为 DataArtifact，模型只读取筛选后的字段和 Outline；
- 文案相似度检查用于防止抄袭，不将竞品句子作为 few-shot 模板；
- 医疗、儿童、安全、食品、化妆品、农药等敏感类目需要更严格的类目策略和人工复核；
- 生成草稿属于只读行为，发布或覆盖现有 Listing 属于高风险写操作；
- 所有数字、规格和声明必须可以追溯到 ProductFactSheet。

## 10. 第一版验收标准

- 输入完整时能通过两套 MCP 形成统一关键词证据；
- 只有形成固定的 30 个有效关键词后才进入文案生成；
- 有效关键词不足时通过条件边补充搜索，最多循环 3 次并能正确终止；
- 任一 MCP 失败时能降级并明确标注，而不是整任务失败；
- 不会把竞品属性写成本产品属性；
- 主标题、副标题和其他字段符合执行时生效的站点及类目规则；
- 每个主要关键词都能追溯到数据来源；
- 输出包括完整草稿、关键词分层、覆盖报告、质检结果和未验证声明；
- 缺少产品事实时会追问，不会凭空补全；
- 生成草稿后不会自动发布；
- 阶段进度可以通过现有 SSE 协议显示在对话窗口中；
- 相同输入和固定数据快照下，关键词选择与质检结果可以稳定复现。

## 11. 推荐实施顺序

1. 扩展 `Domain`、`SpecialistName`、路由和结果 Schema；
2. ✅ 定义 `ListingAgentState`、`ProductFact`、`KeywordEvidence`、`ListingDraft` 和 `ListingContentResult`，并实现关键词与质检条件边子图；
3. ✅ 已完成卖家精灵和 Sif 的 MCP 配置、官方 SDK Transport、工具发现、白名单与真实连接验证；
4. ◐ 已完成跨源有界并发、超时和单源降级；待增加查询缓存和有界重试；
5. ✅ 已完成统一 `KeywordResearchGateway`、关键词归一化、确定性评分和证据引用；
6. ✅ 已使用项目共享 DeepSeek 客户端实现 Listing 初稿、修订提示词和结构化输出；
7. ◐ 已实现第一版确定性质检器；待补充按站点、类目和日期版本化的 `ListingPolicyRegistry`；
8. ✅ 已接入总控 Agent、`listing_content` 路由和 `waiting_input` 阶段 SSE；
9. ◐ 已在前端对话中展示完整结构化草稿；待补充关键词证据详情和发布审批按钮；
10. 使用固定 MCP 快照进行回归测试，再接真实密钥联调。

### 11.1 已实现的状态节点

| 节点 | 读取 | 写入 | 下一步 |
| --- | --- | --- | --- |
| `validate_input` | `request` | `validated_request`，或缺失字段和补充问题 | 首轮研究或等待输入 |
| `request_product_information` | 缺失字段和补充问题 | `needs_input` 结果 | 等待用户补充 |
| `initial_research` | 已验证请求 | `new_keyword_evidence` | 归一化与评估 |
| `evaluate_keywords` | 新旧关键词证据 | 有效词、排除词、数量和缺口 | 规划、补搜或证据不足 |
| `supplement_keyword_search` | 关键词缺口、已见词和轮次 | 新证据、补搜轮次 | 返回关键词评估 |
| `build_keyword_plan` | 全部有效证据 | 固定 2/8/12/8 分层的 30 个词 | 文案生成 |
| `generate_copy` | 产品事实和关键词方案 | 五类文案草稿 | 文案质检 |
| `validate_copy` | 草稿、规则和证据 | 质检报告 | 完成、修订或人工复核 |
| `revise_copy` | 草稿和质检问题 | 修订草稿、修订轮次 | 返回文案质检 |
| `complete` | 草稿、关键词和质检结果 | 完整结果 | 结束 |

正式实现位于：

```text
src/amazon_ops/listing/
├── models.py   # 请求、产品事实、关键词证据、五类文案和质检 Schema
├── mcp.py      # 卖家精灵与 Sif MCP 配置、工具端口和 Transport 接口
├── mcp_transport.py # 官方 SDK Streamable HTTP 实现
├── keyword_gateway.py # 查询规划、跨源执行与统一证据映射
├── state.py    # ListingAgentState
└── graph.py    # LangGraph 条件边和循环控制
```

## 12. 调研依据

- [卖家精灵 MCP 接入与官方 Agent 策略](https://open.sellersprite.com/mcp/31)
- [卖家精灵关键词挖掘 API](https://open.sellersprite.com/api/6)
- [Sif MCP 工具目录](https://mcp.sif.com/)
- [Amazon 2026 年标题与 Item Highlights 更新](https://sellercentral.amazon.com/seller-forums/discussions/t/145b6d0f-999c-4555-896c-c694bda2e470)

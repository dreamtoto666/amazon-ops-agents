# Research: 广告异常诊断重构

## Decision: 核心广告报表优先使用领星 Open API

**Rationale**: 领星 Open API 覆盖 SP/SB/SD 的活动、广告组、广告商品、关键词/投放、搜索词及小时报表，也提供店铺与广告账号查询。固定报表链路采用固定业务接口和字段契约，便于分页、口径校验、回归测试与审计。领星说明 MCP 与 Open API 的单 Tool QPS 都是 1，固定诊断工作流不因 MCP 获得吞吐优势。

**Alternatives considered**:

- 继续仅使用 MCP：保留动态能力，但核心报表要反复发现工具与 Schema，口径、测试和治理成本更高。
- Open API 主路径 + Open API 失败时静默 MCP 回退：拒绝。两条路径可能有不同筛选、归因窗口或字段口径，会使证据不可解释。

**Migration rule**: 某个端点尚未完成 Open API 字段验证前可继续使用该端点的受控只读 MCP；已迁移端点出现运行时故障时展示数据不可用，不自动混用 MCP。先对店铺/广告账号和活动报表做相同范围影子比对，再迁移广告组、关键词、投放和搜索词下钻。

## Decision: 保持稳定的广告数据业务端口

**Rationale**: 现有 `AdvertisingDataGateway` 已将诊断工作流与领星调用解耦。新的 Open API 适配器继续输出统一的广告报告、规范化事实与证据引用，四个诊断阶段不需感知上游协议变化。

**Alternatives considered**:

- 让各 Agent 直接调用 Open API：拒绝。会削弱白名单、限流、清洗、字段映射与审计治理。
- 为每个广告报表建立独立 Agent 工具：拒绝。会把固定的数据治理职责放给模型选择，增加越界与配额风险。

## Decision: 用内部选择数据 MCP 封装领星 Open API 并转换店铺、负责人和父 ASIN

**Rationale**: 领星 Open API 的店铺与 Listing/产品查询会包含不属于诊断的人员信息。内部 MCP 仅在后端执行，立即映射为工作台选择目录；返回给浏览器的负责人显示名只用于授权人工选择，诊断提交中使用安全范围引用，AI 永不获得负责人信息。

**Alternatives considered**:

- 将上游 MCP 原始结果直接给工作台或 Agent：拒绝。会暴露负责人及其他未知字段，并使外部内容越过信任边界。
- 仅在给 AI 前删除负责人字段：拒绝。PII 仍可能进入日志、事件、持久化、异常信息或其他调用者。

**Boundary rule**: 内部 MCP 不加入广告诊断 Agent 的工具集，也不使用会保存原始上游负载的调用账本。它的审计只记录安全范围引用、工具名、记录数、耗时和脱敏错误。

## Decision: 父 ASIN 必须成为真实的诊断过滤条件

**Rationale**: 当前请求模型已有 `asins`，但现有巡检并未以它筛选广告活动。若只增加下拉框，用户会误以为结果已按产品限定。后端必须先把已选父 ASIN 解析为授权店铺中的有效子 ASIN/广告对象范围，并在巡检及所有归因下钻中一致应用；不能在结果页面事后隐藏记录。

**Alternatives considered**:

- 仅在前端隐藏不匹配记录：拒绝。会消耗无关数据配额，且影响异常优先级、归因与代办去重。
- 忽略父 ASIN 直到上游报表原生支持：拒绝。功能要求已声明产品选择，必须在网关进行确定性映射或对上游筛选进行验证。

## Decision: 以单个授权选择目录支持级联筛选

**Rationale**: 一个目录快照在后端完成授权后再返回，可让前端本地进行“负责人 → 店铺 → 父 ASIN”级联，避免从浏览器以任意店铺查询负责人信息。目录过大时可演进为多个经过相同授权检查的子目录接口。

**Alternatives considered**:

- 保持现有仅店铺接口：拒绝，无法满足负责人和父 ASIN 的筛选需求。
- 浏览器直接调用领星 MCP/Open API：拒绝，凭证、权限和原始数据会暴露且绕开统一治理。

## Sources

- [领星 Open API 文档](https://apidoc.lingxing.com/)
- [领星 MCP 说明](https://www.lingxing.com/help/article/mcp)
- 当前广告网关：`src/amazon_ops/advertising/gateway.py`
- 当前工作台：`frontend/src/features/advertising-diagnostics/components/advertising-diagnostics-workbench.tsx`

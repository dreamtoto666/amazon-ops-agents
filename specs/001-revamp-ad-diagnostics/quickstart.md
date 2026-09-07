# Quickstart: Validate the Advertising Diagnostics Revamp

## Prerequisites

- 已配置本地认证、PostgreSQL 和领星 Open API 的非生产测试凭证；密钥不可写入命令输出、测试快照或文档。
- 测试用户拥有至少一个负责人、店铺和父 ASIN 的选择范围。
- 如某个未迁移的只读维度需要 MCP，测试环境应仅启用它的白名单工具。

## 1. Run automated checks

```bash
uv run pytest
cd frontend && npm run typecheck
```

重点覆盖：选择目录字段白名单、用户授权隔离、非法负责人/店铺/产品组合拒绝、负责人 PII 不进入运行/证据/日志/模型输入、父 ASIN 的实际报表过滤、Open API 指标口径、幂等提交、SSE 阶段与代办去重。

## 2. Validate selection and privacy

1. 以授权用户打开广告诊断工作台。
2. 确认工作台依次可选择负责人、店铺和父 ASIN；切换上级选择会清除失效的下级选择。
3. 从 BFF 返回和浏览器诊断请求中检查：负责人显示名仅出现在目录渲染数据，创建运行请求只含安全范围引用。
4. 以另一用户重复检查，确认无法读取或提交不属于其目录的范围。

预期结果：选择目录可用；跨范围组合被拒绝；负责人信息不出现在诊断结果、SSE、历史或代办。

## 3. Validate a product-scoped diagnostic

1. 选择一个店铺、一个负责人和一个父 ASIN，填写当前期与可选对照期。
2. 发起诊断并观察四个业务阶段的 SSE 进度。
3. 打开最终结果，验证所有异常、证据、归因和代办仅覆盖所选产品的有效广告对象。
4. 对照期不可用时，确认页面清楚提示数据限制，不展示确定性趋势结论。

预期结果：核心报表由 Open API 提供；异常可追溯；系统只生成建议/待办而不改动广告设置。

## 4. Validate data-source limits and recovery

1. 使一个已迁移 Open API 报表不可用。
2. 发起同范围诊断。
3. 验证系统显示该阶段数据限制或失败原因，且不会静默改走 MCP。
4. 修复数据源后，用新的幂等键重试；确认已有运行不会产生重复代办。

详细请求与响应形状参见 [contracts/ad-diagnostics-api.md](contracts/ad-diagnostics-api.md)，选择数据边界参见 [contracts/selection-data-mcp.md](contracts/selection-data-mcp.md)，实体和状态规则参见 [data-model.md](data-model.md)。

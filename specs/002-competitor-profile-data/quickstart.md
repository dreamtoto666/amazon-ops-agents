# Quickstart Validation: 竞品画像四模块

## Prerequisites

- 已验证父 ASIN 的变体 ASIN 标识与关键词流量信号；多自然位额外流量由同周期子变体自然流量原始得分计算。推荐专栏直接 Campaign 归属仍须由 Sif 补充原始字段。
- 使用脱敏夹具覆盖 1 个自有父 ASIN、1 个竞品父 ASIN、多个变体和多个周期。

## Validation

1. 运行 `uv run pytest tests/test_competitor_research.py tests/test_competitor_data_processor.py tests/test_competitor_report.py tests/test_prompts.py`。
2. 验证两个父 ASIN 的变体记录都含 `variant_asin` 和规定流量字段。
3. 验证 0.99%、1.00% 的反查流量词记录未进入输出，1.01% 的记录保留。
4. 验证自有推荐专栏包含当前和历史最高 3 个过去周期，竞品仅包含当前周期。
5. 验证多自然位使用 `sum(子变体自然流量) - max(子变体自然流量)`，并在任何子变体明细分页不完整时不计算该父体结果、携带真实缺失原因。
6. 验证推荐专栏模块在所需字段未返回时携带 `unavailable` 状态和真实缺失原因。
7. 运行 `uv run pytest`，确认报告上下文消费 `competitor_data_modules`。

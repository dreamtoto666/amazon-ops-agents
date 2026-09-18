# Implementation Plan: 竞品画像数据获取

**Branch**: `002-competitor-profile-data` | **Date**: 2026-09-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-competitor-profile-data/spec.md`

## Summary

以确定性数据处理替换现有 LLM 摘要画像，将竞品对比结果组织为四个有证据引用的 JSON 模块，直接提供给报告 Agent。请求固定接收一个自有父 ASIN 与一个竞品父 ASIN；处理器只执行用户明确规定的 `>1%` 与周期过滤，不增加其他筛选条件。

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11

**Primary Dependencies**: FastAPI、Pydantic、LangGraph、官方 MCP SDK、DeepSeek Structured LLM（报告写作；不参与模块数据提取）

**Storage**: PostgreSQL 运行历史；本轮模块数据仅保存在 LangGraph 状态与报告上下文中

**Testing**: pytest；使用脱敏 Sif 响应夹具进行服务、处理器、图和报告上下文契约测试

**Target Platform**: Docker/Linux 部署的 Python API 服务

**Project Type**: 后端 Agent 编排服务

**Performance Goals**: 一次父 ASIN 对比在现有受限并发内完成四模块采集；所有满足显式阈值的可用分页记录均传给报告 Agent，或如实返回缺失状态

**Constraints**: 仅调用白名单只读 Sif 工具；外部数据不可信；不得增加/变更用户规定的过滤条件；报告 Agent 不调用工具

**Scale/Scope**: 每次 1 个自有父 ASIN、1 个竞品父 ASIN，4 个固定模块；不涉及前端、广告写操作或历史持久化格式迁移

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

项目宪章仍是模板；应用仓库约束后结果为 PASS。设计保持只读，Sif 原始响应仍标记为不可信业务数据，每条输出保留真实 `evidence_id`，报告只能引用本轮实际证据。两项外部 Sif 数据契约是实现门禁：变体记录必须包含 `variant_asin`，推荐专栏必须包含其直接归属的 Campaign ID 列表；未满足时不得以别的字段替代。

## Project Structure

### Documentation (this feature)

```text
specs/002-competitor-profile-data/
├── plan.md              # This file ($speckit-plan command output)
├── research.md          # Phase 0 output ($speckit-plan command)
├── data-model.md        # Phase 1 output ($speckit-plan command)
├── quickstart.md        # Phase 1 output ($speckit-plan command)
├── contracts/           # Phase 1 output ($speckit-plan command)
└── tasks.md             # Phase 2 output ($speckit-tasks command - NOT created by $speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
src/amazon_ops/
├── competitor_research.py             # Sif 只读业务组合与证据包装
├── competitor_research_catalog.py     # 可信能力目录
├── competitor_data_processor.py       # 确定性四模块数据处理
├── models.py / state.py                # JSON 模型与图状态
├── prompts.py / interfaces.py          # 报告 Agent 上下文
├── competitor_report.py                # 报告证据校验
└── graph.py                            # 数据处理阶段编排

tests/
├── test_competitor_research.py
├── test_competitor_data_processor.py
├── test_competitor_router.py
├── test_competitor_report.py
└── test_prompts.py
```

**Structure Decision**: 保持现有内部竞品研究 MCP 和 LangGraph 数据处理阶段。只替换 `competitor_profiles` 状态与其消费者，不新增浏览器接口或存储表。

## Phase 0: Research Summary

详见 [research.md](research.md)。对 US 站自有 `B0CZDH7649` 与竞品 `B09Z72Q5XN` 的只读探测确认：流量总览可提供自然/广告、SP、SP 推荐、SB、SBV 和推荐专栏的得分/占比；流量历史可提供 `totalScore` 序列；现有接口不能提供变体 ASIN 标识，也不能提供推荐专栏到 Campaign 的直接归属。后二者是外部契约门禁。

## Post-Design Constitution Check

**Result: PASS（受外部契约门禁约束）。** 确定性处理器避免让 LLM 改写阈值或数据；缺失外部字段将导致对应模块 `unavailable`，不影响其他可用模块，但在取得直接归属字段前不得宣称推荐专栏模块完整。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

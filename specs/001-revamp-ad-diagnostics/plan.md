# Implementation Plan: 广告异常诊断重构

**Branch**: `001-revamp-ad-diagnostics` | **Date**: 2026-09-03 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-revamp-ad-diagnostics/spec.md`

**Note**: This template is filled in by the `$speckit-plan` command; its definition describes the execution workflow.

## Summary

重构独立广告诊断工作台，使运营人员按负责人、店铺、父 ASIN 和周期发起仅覆盖广告层级的可追溯诊断。核心广告报表迁移至领星 Open API；仅在 Open API 明确未覆盖的只读能力中保留受控 MCP。新增仅服务于人工筛选界面的内部选择数据 MCP，立即将上游“店铺—负责人—父 ASIN”结果转换为字段白名单目录，并保证负责人信息绝不进入诊断 State、模型上下文、结果、代办、事件或日志。

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: Python 3.11；TypeScript 5.7

**Primary Dependencies**: FastAPI、Pydantic、LangGraph、官方 MCP SDK；Next.js App Router、React、TanStack Query、shadcn/ui

**Storage**: PostgreSQL（诊断运行、调用账本、证据与代办历史）；进程内 SSE 重放窗口

**Testing**: pytest；前端 TypeScript 类型检查；新增后端契约/API/网关测试

**Target Platform**: 浏览器运营工作台 + Docker/Linux 部署的 API 服务

**Project Type**: Web 应用（Next.js 同源 BFF + Python API）

**Performance Goals**: 完整且已授权数据可用时，90% 手动诊断在 5 分钟内交付结果或明确数据限制；筛选目录应支持工作台首次加载与级联选择，不阻塞已有诊断结果浏览。

**Constraints**: 只读、只建议不执行广告修改；核心广告报表优先 Open API；每类数据必须字段白名单与范围校验；负责人 PII 仅能到授权人工界面；浏览器只经同源 BFF；不混用不同数据口径的静默回退；保持四个稳定 SSE 业务阶段。

**Scale/Scope**: 一个广告诊断工作台、选择目录、运行与历史接口、核心活动/明细报告适配；诊断范围限于活动、广告组、关键词、投放与搜索词，不包括库存、利润或 Listing 归因。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

项目宪章当前仍为未填写模板，无法形成额外可执行门禁。仍需遵守仓库级约束：广告修改仅生成建议/待办；外部数据不可信；API、类型、BFF 与测试同步更新；凭证和 PII 不进入日志、模型或浏览器外的授权范围。

**Pre-design result**: PASS。没有已定义宪章原则与本设计冲突；隐私、只读与可追溯要求已在规格、研究与契约中形成明确门禁。

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
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
├── api.py                              # FastAPI routes and dependency assembly
├── advertising/
│   ├── gateway.py                      # Existing read-report port; Open API implementation
│   ├── selection_gateway.py            # New transformed selection-directory boundary
│   ├── models.py                       # Request, scope and safe directory models
│   ├── runtime.py / graph.py / agents.py
│   └── history.py                      # Durable run history and identity-free scope
└── listing/mcp.py / mcp_transport.py   # Reused MCP transport primitives only

frontend/src/
├── app/api/ad-diagnostics/             # Same-origin BFF routes, including selection-directory
├── app/dashboard/ad-diagnostics/page.tsx
└── features/advertising-diagnostics/
    ├── api/                            # Types, service and query keys
    └── components/advertising-diagnostics-workbench.tsx

tests/
├── test_lingxing_advertising_gateway.py
├── test_advertising_runtime.py
└── test_api.py
```

**Structure Decision**: 保持现有双项目工作区与 `AdvertisingDataGateway` 业务端口。新增的选择数据 MCP 是后端受限适配器，不是一个浏览器可直连或 Agent 可发现的独立外部服务；前端只通过新增同源 BFF 调用。

## Phase 0: Research Summary

研究结论见 [research.md](research.md)。所有技术不确定性已解析：核心广告报表采用 Open API；目录型负责人数据仅进入人工选择层；父 ASIN 将在提交前解析为经授权的有效广告范围并贯穿数据巡检与下钻过滤；Open API 故障不静默切至 MCP。

## Post-Design Constitution Check

**Result: PASS。** 设计不引入广告写操作；运行范围、证据和历史仍由现有 `run_id`、`trace_id`、阶段和幂等机制追踪。选择目录的敏感字段在转换边界处停止，不会进入模型、SSE、持久化原始载荷或前端诊断结果。所有新增浏览器调用均有对应 BFF 路由。

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| None | N/A | N/A |

# Tasks: 广告异常诊断重构

**Input**: Design documents from `/specs/001-revamp-ad-diagnostics/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: 本功能涉及授权、负责人隐私、数据口径与广告决策安全，必须为每个新增边界编写后端测试；前端至少通过 TypeScript 类型检查，并在现有前端测试基础设施可用时添加交互测试。

**Organization**: 任务按用户故事组织。任何涉及浏览器调用的任务都必须同时实现 Python API 与 `frontend/src/app/api/` 同源 BFF，不允许浏览器直连领星或内部 MCP。

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: 建立迁移期间的稳定数据来源与测试边界。

- [X] T001 Document the approved Open API endpoint-to-normalized-report field matrix, pagination rules, attribution windows, and zero-denominator behavior in `docs/advertising-anomaly-multi-agent-design.md`
- [X] T002 [P] Add non-secret Open API configuration placeholders and purpose documentation in `.env.example`, `docker-compose.yml`, `docker-compose.prod.yml`, and `README.md`
- [X] T003 [P] Add sanitized Open API and selection-directory fixture builders in `tests/test_lingxing_advertising_gateway.py` and `tests/test_advertising_runtime.py`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 建立全部用户故事共享的数据、授权与隐私边界。

**⚠️ CRITICAL**: 本阶段完成前不得实现工作台选择框、诊断提交或周期任务。

- [X] T004 Define identity-free `DiagnosticScope`, safe `SelectionDirectory`, and Open API report provenance Pydantic models in `src/amazon_ops/advertising/models.py`
- [X] T005 [P] Implement a signed, rate-limited, read-only LingXing Open API transport with credential redaction and normalized gateway errors in `src/amazon_ops/advertising/openapi_transport.py`
- [X] T006 Implement the Open API-backed `AdvertisingDataGateway` adapter with fixed read endpoint allowlists and field-whitelist normalization in `src/amazon_ops/advertising/gateway.py`
- [X] T007 Add Open API report mapping, pagination, missing-versus-zero, and forbidden-write-endpoint tests in `tests/test_lingxing_advertising_gateway.py`
- [X] T008 Implement the authorization and opaque-reference resolver for selection directories and submitted diagnostic scopes in `src/amazon_ops/advertising/selection_gateway.py`
- [ ] T009 Add tests for user authorization isolation, expired references, cross-store combinations, and absence of PII in selection audits in `tests/test_advertising_runtime.py`
- [X] T010 Update service wiring, safe error handling, and dependency assembly for Open API reports and the selection gateway in `src/amazon_ops/api.py` and `src/amazon_ops/advertising/runtime.py`
- [ ] T011 Add request fingerprint, durable history, and recovery serialization support for identity-free product scope in `src/amazon_ops/advertising/runtime.py` and `src/amazon_ops/advertising/history.py`
- [ ] T012 Verify foundational gateway and scope tests with `tests/test_lingxing_advertising_gateway.py`, `tests/test_advertising_runtime.py`, and `tests/test_api.py`

**Checkpoint**: 固定只读数据入口、选择数据脱敏边界、授权解析与产品范围持久化均已可用；之后的用户故事可实施。

---

## Phase 3: User Story 1 - 按负责人、店铺和产品发起可信诊断 (Priority: P1) 🎯 MVP

**Goal**: 运营人员从独立工作台选择负责人、店铺、父 ASIN 和周期，发起实际受产品范围约束的广告诊断，并得到带证据边界的结果。

**Independent Test**: 以一个有多位负责人、多店铺和多个父 ASIN 的测试用户，选择有效组合发起诊断；验证结果只包含所选产品的广告对象，负责人姓名只出现在授权选择框而不出现在请求、结果、SSE、历史、日志或模型输入。

### Tests for User Story 1

- [ ] T013 [P] [US1] Add API contract tests for `GET /api/ad-diagnostics/selection-directory`, authorization failures, empty directory, and safe response fields in `tests/test_api.py`
- [ ] T014 [P] [US1] Add API contract tests for product-scoped `POST /api/ad-diagnostics/runs`, invalid/expired selection references, and idempotency conflicts in `tests/test_api.py`
- [ ] T015 [P] [US1] Add runtime regression tests proving parent-ASIN scope filters inspection and all attribution reports before anomaly ranking in `tests/test_advertising_runtime.py`
- [ ] T016 [P] [US1] Add privacy regression tests proving responsible person data is absent from Agent state, evidence, SSE payloads, run history, todos, and error/log summaries in `tests/test_advertising_runtime.py`

### Implementation for User Story 1

- [X] T017 [US1] Implement fixed allowlisted LingXing Open API calls and immediate white-list transformation for store-to-responsible-person-to-parent-ASIN data in `src/amazon_ops/advertising/selection_gateway.py`
- [X] T018 [US1] Replace/augment the legacy shop route with authenticated `GET /api/ad-diagnostics/selection-directory` and server-side user filtering in `src/amazon_ops/api.py`
- [X] T019 [P] [US1] Add the matching same-origin directory BFF route in `frontend/src/app/api/ad-diagnostics/selection-directory/route.ts`
- [X] T020 [US1] Extend run request validation to accept directory version, opaque shop/product references, resolve them server-side, and exclude all responsible-person fields in `src/amazon_ops/advertising/models.py` and `src/amazon_ops/api.py`
- [X] T021 [US1] Apply the resolved parent-ASIN/ad-object scope consistently to activity inspection, all four attribution queries, normalized facts, evidence, and deduplication in `src/amazon_ops/advertising/agents.py`, `src/amazon_ops/advertising/gateway.py`, and `src/amazon_ops/advertising/runtime.py`
- [X] T022 [US1] Complete the Open API activity-report migration with shadow-comparison instrumentation and explicit data-limitation behavior (no silent MCP fallback) in `src/amazon_ops/advertising/gateway.py` and `src/amazon_ops/advertising/runtime.py`
- [X] T023 [P] [US1] Add selection-directory and safe scope request/response types, service calls, and query options in `frontend/src/features/advertising-diagnostics/api/types.ts`, `frontend/src/features/advertising-diagnostics/api/service.ts`, and `frontend/src/features/advertising-diagnostics/api/queries.ts`
- [X] T024 [US1] Replace the single shop selector with dependent responsible-person, store, and parent-ASIN selectors; clear invalid descendants and block submit until the full valid scope is selected in `frontend/src/features/advertising-diagnostics/components/advertising-diagnostics-workbench.tsx`
- [ ] T025 [US1] Update the workbench result rendering to show the identity-free effective store/product scope, data completeness, evidence status, and no-baseline limitations in `frontend/src/features/advertising-diagnostics/components/advertising-diagnostics-workbench.tsx`
- [ ] T026 [US1] Run the P1 quickstart scenarios and verify backend tests plus `cd frontend && npm run typecheck` using `specs/001-revamp-ad-diagnostics/quickstart.md`

**Checkpoint**: 用户能够完成受授权且受父 ASIN 真实约束的诊断；只读 Open API 活动报告、四阶段进度和证据可追溯链路均可独立演示。

---

## Phase 4: User Story 2 - 高效审阅和处理运营代办 (Priority: P2)

**Goal**: 运营人员能用安全店铺/产品范围检索、审阅和处理可追溯代办，而不暴露负责人 PII 或执行广告修改。

**Independent Test**: 对同一产品范围重复完成两次诊断，验证代办去重；可按店铺、父 ASIN、严重程度和状态筛选；一项代办被不采纳后保留理由且不产生任何广告写操作。

### Tests for User Story 2

- [ ] T027 [P] [US2] Add API tests for scoped todo listing and review-status updates, including ownership checks and rejection reasons, in `tests/test_api.py`
- [ ] T028 [P] [US2] Add runtime/history tests for identity-free todo dedupe keys, evidence traceability, conflicting recommendations, and no-write behavior in `tests/test_advertising_runtime.py`

### Implementation for User Story 2

- [ ] T029 [US2] Extend todo and run-history models with effective product scope, review decision, reason, and identity-free dedupe fields in `src/amazon_ops/advertising/models.py` and `src/amazon_ops/advertising/history.py`
- [ ] T030 [US2] Implement owner-authorized scoped todo listing and review-status update endpoints without advertising execution in `src/amazon_ops/api.py` and `src/amazon_ops/advertising/runtime.py`
- [ ] T031 [P] [US2] Add matching BFF handlers for todo list and review updates in `frontend/src/app/api/ad-diagnostics/todos/route.ts` and `frontend/src/app/api/ad-diagnostics/todos/[todoId]/route.ts`
- [ ] T032 [P] [US2] Add todo query types, mutations, and cache invalidation in `frontend/src/features/advertising-diagnostics/api/types.ts`, `frontend/src/features/advertising-diagnostics/api/service.ts`, and `frontend/src/features/advertising-diagnostics/api/queries.ts`
- [ ] T033 [US2] Add store/product/severity/status filters, evidence links, conflict indicators, and review/reject actions to the workbench todo view in `frontend/src/features/advertising-diagnostics/components/advertising-diagnostics-workbench.tsx`
- [ ] T034 [US2] Run scoped todo API/runtime tests and `cd frontend && npm run typecheck` for the P2 independent scenario

**Checkpoint**: 运营人员可在不查看原始报表的情况下审阅并处理证据充分的代办；重复诊断不产生重复代办，拒绝建议不会触发广告写入。

---

## Phase 5: User Story 3 - 管理安全且可持续的诊断范围 (Priority: P3)

**Goal**: 管理员可配置周期性诊断，并在授权、限流、数据源故障或中断恢复时看到可信状态与安全范围。

**Independent Test**: 创建一个受授权店铺和父 ASIN 的周期任务，模拟 Open API 限流/不可用和服务重启，验证只运行有效范围、显示受影响阶段、可恢复且不重复产生结果或代办。

### Tests for User Story 3

- [ ] T035 [P] [US3] Add API tests for create/list/pause periodic diagnostic schedules, owner authorization, and invalid safe scope rejection in `tests/test_api.py`
- [ ] T036 [P] [US3] Add runtime tests for scheduled idempotency, rate-limit/data-source limitation events, recovery checkpoints, and no silent MCP fallback in `tests/test_advertising_runtime.py`

### Implementation for User Story 3

- [ ] T037 [US3] Define persistent identity-free diagnostic schedule models and migration/store operations in `src/amazon_ops/advertising/models.py` and `src/amazon_ops/advertising/history.py`
- [ ] T038 [US3] Implement schedule validation, submission, pause/resume, idempotent dispatch, and recovery-safe scope revalidation in `src/amazon_ops/advertising/runtime.py`
- [ ] T039 [US3] Add authenticated schedule management API routes with explicit error/status responses in `src/amazon_ops/api.py`
- [ ] T040 [P] [US3] Add same-origin schedule BFF routes in `frontend/src/app/api/ad-diagnostics/schedules/route.ts` and `frontend/src/app/api/ad-diagnostics/schedules/[scheduleId]/route.ts`
- [ ] T041 [P] [US3] Add schedule types, services, queries, and mutations in `frontend/src/features/advertising-diagnostics/api/types.ts`, `frontend/src/features/advertising-diagnostics/api/service.ts`, and `frontend/src/features/advertising-diagnostics/api/queries.ts`
- [ ] T042 [US3] Add periodic-run configuration, scope summary, status, pause/resume, and data-limitation display to `frontend/src/features/advertising-diagnostics/components/advertising-diagnostics-workbench.tsx`
- [ ] T043 [US3] Run scheduled/recovery validation from `specs/001-revamp-ad-diagnostics/quickstart.md`, backend tests, and `cd frontend && npm run typecheck`

**Checkpoint**: 授权管理员可安全管理周期任务；数据源问题被如实显示，恢复和重复调度不会越界、重复计费或重复创建代办。

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: 完成迁移、可观测性、安全复核与全量验证。

- [ ] T044 [P] Update architecture, Open API/MCP governance, selector privacy, scheduling, and failure-behavior documentation in `docs/advertising-anomaly-multi-agent-design.md`, `docs/listing-mcp-integration.md`, and `docs/controller-agent-responsibilities.md`
- [ ] T045 [P] Add a cross-boundary redaction audit for Open API credentials, responsible-person fields, and raw selection payloads in `src/amazon_ops/advertising/openapi_transport.py`, `src/amazon_ops/advertising/selection_gateway.py`, `src/amazon_ops/advertising/runtime.py`, and `src/amazon_ops/presenter.py`
- [ ] T046 Validate Open API shadow comparisons for record coverage, entity identity, core metrics, missing data, and supported ad types before enabling each endpoint in `src/amazon_ops/advertising/gateway.py`
- [ ] T047 Run the complete backend and frontend verification suite: `uv run pytest` and `cd frontend && npm run typecheck`
- [ ] T048 Execute every scenario in `specs/001-revamp-ad-diagnostics/quickstart.md` and record any unresolved external-data limitations in `docs/advertising-anomaly-multi-agent-design.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1**: 可立即开始。
- **Phase 2**: 依赖 Phase 1；完成前阻塞所有用户故事。
- **US1 (Phase 3)**: 依赖 Phase 2，是 MVP。
- **US2 (Phase 4)**: 依赖 Phase 2 和 US1 的安全范围、证据及历史记录。
- **US3 (Phase 5)**: 依赖 Phase 2 和 US1 的范围验证；可在 US2 实施期间并行，但最终集成应在 US2 后完成。
- **Phase 6**: 依赖所需用户故事完成。

### User Story Dependency Graph

```text
Setup → Foundation → US1 (product-scoped trusted diagnosis)
                         ├── US2 (reviewable, deduplicated todos)
                         └── US3 (safe scheduled diagnostics)
US2 + US3 → Polish and full validation
```

### Parallel Opportunities

- T002 与 T003 可并行；T005 与 T008 可在模型 T004 完成后并行。
- P1 的 T013–T016 可并行编写；T019 与 T023 可在后端目录契约确定后并行。
- P2 的 T027、T028 可并行；T031、T032 可并行。
- P3 的 T035、T036 可并行；T040、T041 可并行。
- US2 与 US3 可由不同开发者并行，但都不得改变 US1 已确定的安全范围契约。

## Parallel Example: User Story 1

```text
Task: "T013 contract tests for the selection directory in tests/test_api.py"
Task: "T014 contract tests for product-scoped run creation in tests/test_api.py"
Task: "T015 product scope runtime tests in tests/test_advertising_runtime.py"
Task: "T016 responsible-person privacy regression tests in tests/test_advertising_runtime.py"

After the directory contract is fixed:
Task: "T019 BFF directory route in frontend/src/app/api/ad-diagnostics/selection-directory/route.ts"
Task: "T023 client directory types and queries in frontend/src/features/advertising-diagnostics/api/"
```

## Implementation Strategy

### MVP First (US1 only)

1. 完成 Phase 1 和 Phase 2，先建立字段、授权和数据来源边界。
2. 完成 T013–T026，确保父 ASIN 是实际报表过滤条件而非前端装饰。
3. 在不启用广告写操作的前提下，按 quickstart 完成一次完整手动诊断演示。
4. 通过 P1 验收后再推进代办管理和周期任务。

### Incremental Delivery

1. **P1**：安全选择目录 + Open API 活动报表 + 产品范围诊断。
2. **P2**：安全范围内的代办审阅和去重闭环。
3. **P3**：定时运行、恢复和运行限制提示。
4. **Polish**：报表端点逐项影子验证后迁移，不以未验证的数据源替换关键诊断结论。

## Notes

- 所有任务都遵循必需的复选框、任务 ID、故事标签（故事阶段）和精确路径格式。
- `[P]` 仅表示不同文件且无未完成依赖时可并行；同一文件的任务仍需协调合并。
- 当前工作区已有用户未提交改动；实施时只修改本功能相关文件，避免覆盖无关工作。

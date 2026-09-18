# Tasks: 竞品画像数据获取

**Input**: [spec.md](spec.md)、[plan.md](plan.md)、[research.md](research.md)、[data-model.md](data-model.md)、[report-agent contract](contracts/report-agent-data-modules.md)、[quickstart.md](quickstart.md)

**Tests**: 本功能改变外部数据契约、过滤边界和报告输入，必须先补齐服务、处理器、图和报告契约测试。

**Organization**: 任务按用户故事排列；每项数据提取都须以已记录的 Sif 原始字段为前提。

## Phase 1: Setup and External Contract Gate

**Purpose**: 锁定 Sif 提供的字段，防止以猜测或 ASIN 总 Campaign 数替代用户要求的数据。

- [X] T001 Obtain and record the approved read-only Sif contract for parent-ASIN variant records, including `variant_asin` and all required traffic ratios, in `specs/002-competitor-profile-data/contracts/sif-source-contract.md`
- [X] T002 Obtain and record the approved read-only Sif contract for keyword growth-period and multi-organic-position fields in `specs/002-competitor-profile-data/contracts/sif-source-contract.md`
- [ ] T003 Obtain and record the approved read-only Sif contract mapping each recommendation placement to direct `campaign_ids` in `specs/002-competitor-profile-data/contracts/sif-source-contract.md`
- [ ] T004 [P] Create sanitized Sif response fixtures covering both parent ASINs, multiple variants, threshold boundaries, historical periods, and missing fields in `tests/fixtures/competitor_data_modules/`
- [X] T005 [P] Update the Sif capability matrix and approved field mappings in `docs/competitor-advertising-agent-design.md`

**Checkpoint**: T001 and T002 are verified. T003 remains required before the recommendation-placement module can be marked available; independent, fully sourced work may proceed with explicit unavailable states for missing required fields.

## Phase 2: Foundational Replacement Contract

**Purpose**: Replace the old LLM-generated profile state with deterministic, evidence-bounded module JSON.

- [X] T006 Define `CompetitorDataModules`, four module models, record models, evidence references, and status/missing-reason validation in `src/amazon_ops/models.py`
- [X] T007 [P] Replace `competitor_profiles` with `competitor_data_modules` in the graph state contract in `src/amazon_ops/state.py`
- [ ] T008 Extend the trusted catalog and local MCP surface with only the approved Sif read operations and parent-ASIN input validation in `src/amazon_ops/competitor_research_catalog.py`, `src/amazon_ops/competitor_research.py`, and `src/amazon_ops/competitor_research_mcp.py`
- [ ] T009 Add Sif service contract tests for approved calls, pagination, parent-only inputs, evidence IDs, and missing-field propagation in `tests/test_competitor_research.py` and `tests/test_competitor_router.py`
- [ ] T010 Replace the LLM profile transformation with deterministic source-field extraction, exact threshold checks, unit validation, and per-module status handling in `src/amazon_ops/competitor_data_processor.py`
- [ ] T011 Add deterministic processor tests for provenance, 0.99/1.00/1.01% boundaries, no extra filters, and no fabricated values in `tests/test_competitor_data_processor.py`
- [X] T012 Wire the new module state, processing progress count, failure status, and replacement semantics into the LangGraph flow in `src/amazon_ops/graph.py`

**Checkpoint**: A completed research result produces `competitor_data_modules` only; no execution path writes or reads `competitor_profiles`.

## Phase 3: User Story 1 - 获取流量词与变体流量分布 (Priority: P1) 🎯 MVP

**Goal**: 报告 Agent 获得双方父 ASIN 的 Listing 流量与带 ASIN 标识的变体流量分布。

**Independent Test**: 用两个父 ASIN 和多个变体夹具运行数据处理，验证每个变体保留所有规定字段或真实缺失状态。

- [X] T013 [P] [US1] Add parent-only scope tests in `tests/test_competitor_research.py`
- [ ] T014 [P] [US1] Add variant record mapping and required-field completeness tests in `tests/test_competitor_data_processor.py`
- [X] T015 [US1] Implement `traffic_keyword_lookup` extraction for Listing natural/ad distribution and variant traffic ratios in `src/amazon_ops/competitor_data_processor.py`
- [X] T016 [US1] Add `traffic_keyword_lookup` evidence, status, and missing-reason serialization to the module model in `src/amazon_ops/models.py`
- [ ] T017 [US1] Verify the US1 scenario in `specs/002-competitor-profile-data/quickstart.md`

## Phase 4: User Story 2 - 对比单个流量词与多变体自然位 (Priority: P1)

**Goal**: 报告 Agent 获得自有和竞品的规定阈值流量词，以及多自然位自然流量数据。

**Independent Test**: 使用带上升期、关键词占比边界和多自然位额外流量的夹具，验证只输出用户规定的记录和字段。

- [X] T018 [P] [US2] Add reverse-keyword threshold and self-versus-competitor field tests in `tests/test_competitor_data_processor.py`
- [X] T019 [P] [US2] Add growth-period-source and multi-organic-field absence tests in `tests/test_competitor_data_processor.py`
- [X] T020 [US2] Implement `traffic_keyword_reverse_lookup` extraction, retaining self growth-period changes only when explicitly returned by Sif, in `src/amazon_ops/competitor_data_processor.py`
- [X] T021 [US2] Implement `multi_variant_organic_position` extraction from same-period returned child natural scores in `src/amazon_ops/competitor_data_processor.py`
- [ ] T022 [US2] Verify the US2 scenario in `specs/002-competitor-profile-data/quickstart.md`

## Phase 5: User Story 3 - 对比推荐专栏周期数据 (Priority: P2)

**Goal**: 报告 Agent 获得规定周期的推荐专栏、流量占比和直接归属 Campaign 数。

**Independent Test**: 使用多个历史周期、推荐专栏和直接 Campaign 归属夹具，验证周期选择、`>1%` 边界及数量一致性。

- [ ] T023 [P] [US3] Add current-versus-top-three-past-period selection tests in `tests/test_competitor_research.py`
- [ ] T024 [P] [US3] Add placement threshold, direct-Campaign deduplication, and unavailable-state tests in `tests/test_competitor_data_processor.py`
- [ ] T025 [US3] Implement source retrieval for current and historical recommendation-placement data with complete pagination in `src/amazon_ops/competitor_research.py`
- [ ] T026 [US3] Implement `recommendation_placement` extraction, strict placement-ratio filtering, and direct Campaign counting in `src/amazon_ops/competitor_data_processor.py`
- [ ] T027 [US3] Verify the US3 scenario in `specs/002-competitor-profile-data/quickstart.md`

## Phase 6: Report Integration and Cross-Cutting Validation

**Purpose**: 让报告与普通总控汇总只消费新模块，并持续执行证据边界。

- [ ] T028 Replace `competitor_profiles` with `competitor_data_modules` in aggregation/report contexts and report-agent instructions in `src/amazon_ops/prompts.py` and `src/amazon_ops/interfaces.py`
- [X] T029 Update report evidence collection and module-aware validation in `src/amazon_ops/competitor_report.py`
- [ ] T030 [P] Add aggregation, report-context, and prompt regression tests for the replacement contract in `tests/test_prompts.py` and `tests/test_competitor_report.py`
- [ ] T031 [P] Update graph integration tests to assert module-state production and report consumption in `tests/test_graph.py` and `tests/test_competitor_data_processor.py`
- [ ] T032 Update the competitor design and controller-responsibility documentation with four-module semantics, Sif contract limitations, and parent-only input rules in `docs/competitor-advertising-agent-design.md` and `docs/controller-agent-responsibilities.md`
- [X] T033 Run the targeted validation command documented in `specs/002-competitor-profile-data/quickstart.md`
- [X] T034 Run the full backend suite with `uv run pytest`

## Dependencies & Execution Order

```text
T001 → US1 foundation → T004–T012 → US1 + US2 + US3 → T028–T034
                         T002 ───────┘    T003 ───────────┘
```

- US1, US2 and US3 can proceed in parallel after Phase 2, provided their shared model changes are coordinated.
- T028–T031 depend on all required module shapes being stable.
- T001–T003 are external contract gates, not optional cleanup work.

## Parallel Example

```text
Task: "T004 sanitized Sif fixtures"
Task: "T005 capability matrix documentation"

After T006–T012:
Task: "T013–T014 US1 tests"
Task: "T018–T019 US2 tests"
Task: "T023–T024 US3 tests"
```

## Implementation Strategy

1. Confirm all three Sif contracts and build fixtures.
2. Replace state and deterministic processor before changing any report prompt.
3. Deliver US1 first, then independently validate US2 and US3.
4. Switch report contexts only after all required modules have stable models and evidence tests.

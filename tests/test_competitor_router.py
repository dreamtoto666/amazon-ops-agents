from __future__ import annotations

import asyncio
from datetime import date, timedelta

from amazon_ops.competitor_advertising import CompetitorAdvertisingSpecialist
from amazon_ops.competitor_research import CompetitorResearchService
from amazon_ops.competitor_research_catalog import (
    COMPETITOR_RESEARCH_CAPABILITIES,
    OVERVIEW_TOOLS,
    PROVIDER_DEFAULT_WINDOW_TOOLS,
    competitor_research_catalog,
)
from amazon_ops.competitor_research_mcp import create_competitor_research_mcp_server
from amazon_ops.competitor_research_mcp_client import CompetitorResearchMCPClient
from amazon_ops.competitor_router import (
    SCOPE_DEPENDENCY_FIELDS,
    CompetitorResearchPlan,
    CompetitorResearchRouter,
    validate_competitor_research_plan,
)
from amazon_ops.events import InMemoryEventHub, StageController, StageName, StageReporter
from amazon_ops.listing.mcp import MCPProviderClient, sif_mcp_config
from amazon_ops.models import AgentTask, AnalysisPeriod, DateRange, QueryScope, SpecialistName


class StubLLM:
    def __init__(self, payload: dict):
        self.payload = payload
        self.context = ""

    def complete(self, *, context, output_model, **_kwargs):
        self.context = context
        return output_model.model_validate(self.payload)


class StubService:
    pass


class RecordingTransport:
    def __init__(self):
        self.calls = []

    def list_tools(self, _config):
        return []

    def call_tool(self, _config, tool, arguments):
        self.calls.append((tool, arguments))
        return {"data": {"items": []}}


def _scope(**updates):
    return QueryScope(
        marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], **updates
    )


def _task(objective="对比我和竞品"):
    return AgentTask(task_id="task-1", agent=SpecialistName.COMPETITOR_ADVERTISING, objective=objective, reason="test")


def test_mcp_catalog_and_native_tool_descriptions_cover_all_business_capabilities():
    server = create_competitor_research_mcp_server(service=StubService())

    tools = asyncio.run(server.list_tools())
    catalog = asyncio.run(server.call_tool("get_competitor_research_catalog", {})).structured_content

    capability_tools = {item.tool for item in COMPETITOR_RESEARCH_CAPABILITIES}
    exposed = {tool.name: tool for tool in tools}
    assert capability_tools <= set(exposed)
    assert all(exposed[name].description for name in capability_tools)
    assert catalog == {"ok": True, "capabilities": competitor_research_catalog()}


def test_router_uses_trusted_catalog_and_executes_llm_overview_plan():
    llm = StubLLM({"batches": [list(OVERVIEW_TOOLS)], "rationale": "全景比较"})
    calls = []
    specialist = CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(tool_caller=lambda tool, args: calls.append((tool, args)) or {"ok": True, "summary": {}, "evidence": [], "limitations": [], "errors": []}),
        CompetitorResearchRouter(llm),
    )

    result = specialist.invoke(_task(), _scope(), {})

    assert result.status == "completed"
    assert {tool for tool, _ in calls} == set(OVERVIEW_TOOLS)
    assert '"capabilities"' in llm.context
    assert "inspect_campaign" in llm.context


def test_specialist_marks_partial_tool_success_as_degraded():
    specialist = CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(
            tool_caller=lambda _tool, _args: {
                "ok": True,
                "summary": {},
                "evidence": [],
                "limitations": [],
                "errors": [{"code": "INVALID_REQUEST", "tool": "traffic_trend"}],
            }
        ),
        CompetitorResearchRouter(
            StubLLM({"batches": [["analyze_traffic_structure"]], "rationale": "流量结构"})
        ),
    )

    result = specialist.invoke(_task(), _scope(), {})

    assert result.status == "degraded"
    assert result.errors == [{"code": "INVALID_REQUEST", "tool": "traffic_trend"}]


def test_invalid_llm_plan_falls_back_to_existing_keyword_route():
    llm = StubLLM({"batches": [["not_a_real_tool"]], "rationale": "bad"})
    calls = []
    specialist = CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(tool_caller=lambda tool, args: calls.append((tool, args)) or {"ok": True, "summary": {}, "evidence": [], "limitations": [], "errors": []}),
        CompetitorResearchRouter(llm),
    )

    specialist.invoke(_task("运营时光机复盘竞品"), _scope(), {})

    assert [tool for tool, _ in calls] == ["replay_operations_history"]


def test_plan_validator_rejects_unconfirmed_drilldown_identifiers():
    assert validate_competitor_research_plan(CompetitorResearchPlan(batches=[["inspect_campaign"]]), _scope()) is None
    assert validate_competitor_research_plan(CompetitorResearchPlan(batches=[["inspect_ad_group"]]), _scope(campaign_ids=["c-1"])) is None


def test_router_can_select_explicit_campaign_when_identifier_is_confirmed():
    llm = StubLLM({"batches": [["inspect_campaign"]], "explicit_drilldown_requested": True, "rationale": "用户指定活动"})
    calls = []
    specialist = CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(tool_caller=lambda tool, args: calls.append((tool, args)) or {"ok": True, "summary": {}, "evidence": [], "limitations": [], "errors": []}),
        CompetitorResearchRouter(llm),
    )

    specialist.invoke(_task("拆解指定 Campaign"), _scope(campaign_ids=["campaign-1"]), {})

    assert calls == [("inspect_campaign", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "country": "US"})]


# --- 计划的并行契约与依赖 --------------------------------------------------

def test_non_parallelizable_tool_must_be_alone_in_its_batch():
    """A batch runs concurrently, so the catalogue's parallelizable flag binds."""

    assert validate_competitor_research_plan(
        CompetitorResearchPlan(batches=[["compare_asin_sales", "inspect_campaign"]], explicit_drilldown_requested=True),
        _scope(campaign_ids=["campaign-1"]),
    ) is None
    assert validate_competitor_research_plan(
        CompetitorResearchPlan(batches=[["compare_asin_sales", "inspect_ad_group"]], explicit_drilldown_requested=True),
        _scope(campaign_ids=["campaign-1"], ad_group_ids=["group-1"]),
    ) is None
    # 单独成批是允许的，且可以排在并行批次之后
    assert validate_competitor_research_plan(
        CompetitorResearchPlan(
            batches=[list(OVERVIEW_TOOLS), ["inspect_campaign"]], explicit_drilldown_requested=True
        ),
        _scope(campaign_ids=["campaign-1"]),
    ) == [list(OVERVIEW_TOOLS), ["inspect_campaign"]]


def test_every_catalogue_dependency_is_enforced_by_a_scope_field():
    """dependencies 指向已确认的 scope 输入，必须全部有对应的强制字段。"""

    declared = {name for item in COMPETITOR_RESEARCH_CAPABILITIES for name in item.dependencies}

    assert declared, "catalogue 不再声明任何依赖，此测试需要重新审视"
    assert declared <= set(SCOPE_DEPENDENCY_FIELDS), f"未映射的依赖: {declared - set(SCOPE_DEPENDENCY_FIELDS)}"


def test_id_dependent_tools_require_both_explicit_intent_and_scope_inputs():
    plan = CompetitorResearchPlan(batches=[["inspect_campaign"]], explicit_drilldown_requested=True)

    assert validate_competitor_research_plan(plan, _scope(campaign_ids=["campaign-1"])) == [["inspect_campaign"]]
    assert validate_competitor_research_plan(plan, _scope()) is None
    # 没有明确下钻意图时，即使 ID 齐全也不允许
    assert validate_competitor_research_plan(
        CompetitorResearchPlan(batches=[["inspect_campaign"]]), _scope(campaign_ids=["campaign-1"])
    ) is None


# --- legacy 回退路径（LLM 计划器不可用时）-----------------------------------

def _legacy_specialist(calls):
    return CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(
            tool_caller=lambda tool, args: calls.append((tool, args))
            or {"ok": True, "summary": {}, "evidence": [], "limitations": [], "errors": []}
        ),
        CompetitorResearchRouter(StubLLM({"batches": [["not_a_real_tool"]]})),
    )


def test_legacy_route_asks_for_the_campaign_id_instead_of_sweeping_everything():
    calls = []
    specialist = _legacy_specialist(calls)

    result = specialist.invoke(_task("拆解竞品 Campaign"), _scope(), {})

    assert result.status == "needs_input"
    assert "Campaign ID" in result.summary
    assert calls == [], "缺少标识时不得发起任何 Sif 调用"


def test_legacy_route_performs_the_drilldown_when_the_id_is_confirmed():
    calls = []
    specialist = _legacy_specialist(calls)

    result = specialist.invoke(_task("拆解竞品 Campaign"), _scope(campaign_ids=["campaign-1"]), {})

    assert result.status == "completed"
    assert calls == [("inspect_campaign", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "country": "US"})]


def test_legacy_route_emits_tool_events_like_the_planned_path():
    """两条路径的可观测性必须一致，否则下钻在 SSE 里是静默的。"""

    hub = InMemoryEventHub()
    controller = StageController(hub)
    calls = []
    specialist = _legacy_specialist(calls)
    controller.start("run-legacy", StageName.ANALYSIS, title="分析数据")
    state = {"_stage_reporter": StageReporter(controller=controller, run_id="run-legacy", stage=StageName.ANALYSIS)}

    specialist.invoke(_task("拆解竞品 Campaign"), _scope(campaign_ids=["campaign-1"]), state)

    kinds = [event.data.get("kind") for event in hub.events_after("run-legacy")]
    assert "tool.call.started" in kinds
    assert "tool.call.completed" in kinds


# --- 观察窗口 coverage ------------------------------------------------------

REQUESTED_PERIOD = AnalysisPeriod(current=DateRange(start=date(2026, 8, 31), end=date(2026, 9, 6)))


def _coverage_specialist(calls):
    alignment = {
        "compare_asin_sales": "unaligned",
        "analyze_traffic_structure": "unaligned",
        "compare_traffic_keywords": "partially_aligned",
        "inspect_ad_architecture": "unaligned",
    }
    return CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(
            tool_caller=lambda tool, args: calls.append((tool, args))
            or {
                "ok": True,
                "summary": {},
                "evidence": [],
                "limitations": [],
                "errors": [],
                "observation_window": {"alignment": alignment[tool], "maturity": "COMPLETE"},
            }
        ),
        CompetitorResearchRouter(StubLLM({"batches": [list(OVERVIEW_TOOLS)], "rationale": "全景"})),
    )


def test_a_requested_period_is_forwarded_only_to_capabilities_that_accept_one():
    calls = []
    specialist = _coverage_specialist(calls)

    specialist.invoke(_task(), _scope(period=REQUESTED_PERIOD), {})

    sent = {tool: args for tool, args in calls}
    assert sent["compare_traffic_keywords"]["period_start"] == "2026-08-31"
    assert sent["compare_traffic_keywords"]["period_end"] == "2026-09-06"
    # 其余总览工具不接受日期参数，不得凭空塞进去
    for tool in ("compare_asin_sales", "analyze_traffic_structure", "inspect_ad_architecture"):
        assert "period_start" not in sent[tool] and "period_end" not in sent[tool]


def test_coverage_records_which_sources_could_not_observe_the_requested_period():
    calls = []
    specialist = _coverage_specialist(calls)

    result = specialist.invoke(_task(), _scope(period=REQUESTED_PERIOD), {})

    coverage = result.deliverables[0]["observation_window"]
    assert coverage["source"] == "requested_period"
    assert coverage["requested"] == {"start": "2026-08-31", "end": "2026-09-06"}
    assert coverage["fully_aligned"] == []
    assert coverage["partially_aligned"] == ["compare_traffic_keywords"]
    assert set(coverage["unaligned"]) == {
        "compare_asin_sales", "analyze_traffic_structure", "inspect_ad_architecture"
    }
    # 用户必须能看见，而不是只在机器可读字段里
    assert "2026-08-31" in result.summary and "未与请求周期完整对齐" in result.summary


def test_without_a_requested_period_the_default_source_is_recorded_silently():
    calls = []
    specialist = _coverage_specialist(calls)

    result = specialist.invoke(_task(), _scope(), {})

    coverage = result.deliverables[0]["observation_window"]
    assert coverage["source"] == "service_default"
    assert coverage["requested"] is None
    assert "请求周期" not in result.summary


def test_coverage_is_recorded_even_when_every_source_fails():
    specialist = CompetitorAdvertisingSpecialist(
        CompetitorResearchMCPClient(tool_caller=lambda tool, args: {}),
        CompetitorResearchRouter(StubLLM({"batches": [list(OVERVIEW_TOOLS)]})),
    )

    result = specialist.invoke(_task(), _scope(period=REQUESTED_PERIOD), {})

    assert result.status == "unavailable"
    assert result.deliverables[0]["observation_window"]["unaligned"]


def test_the_catalogue_declares_the_window_contract_for_every_capability():
    """provider_default 是「无法对齐」的唯一判据，必须与目录一致。"""

    every = {item.tool: item.observation_window for item in COMPETITOR_RESEARCH_CAPABILITIES}

    assert every, "目录为空，此测试需重新审视"
    assert set(PROVIDER_DEFAULT_WINDOW_TOOLS) == {
        tool for tool, window in every.items() if window == "provider_default"
    }


def test_every_business_tool_is_callable_through_the_mcp_server():
    """Exercise the public MCP boundary, not just service methods in isolation."""

    transport = RecordingTransport()
    service = CompetitorResearchService(MCPProviderClient(config=sif_mcp_config(), transport=transport))
    server = create_competitor_research_mcp_server(service=service)
    comparison = {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}

    async def call_all():
        requests = [
            ("compare_asin_sales", comparison),
            ("analyze_traffic_structure", comparison),
            ("compare_traffic_keywords", comparison),
            ("replay_operations_history", comparison),
            ("inspect_ad_architecture", comparison),
            ("analyze_recommendation_traffic", comparison),
            ("inspect_campaign", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "country": "US"}),
            ("inspect_ad_group", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "ad_group_id": "group-1", "country": "US"}),
        ]
        return [await server.call_tool(name, arguments) for name, arguments in requests]

    results = asyncio.run(call_all())

    assert all(result.structured_content["ok"] is True for result in results)
    assert transport.calls


def test_mcp_rejects_a_future_period_without_calling_sif():
    transport = RecordingTransport()
    service = CompetitorResearchService(MCPProviderClient(config=sif_mcp_config(), transport=transport))
    server = create_competitor_research_mcp_server(service=service)
    tomorrow = date.today() + timedelta(days=1)

    result = asyncio.run(
        server.call_tool(
            "compare_traffic_keywords",
            {
                "own_asin": "B0OWN00001",
                "competitor_asins": ["B0COMP0001"],
                "country": "US",
                "period_start": tomorrow.isoformat(),
                "period_end": tomorrow.isoformat(),
            },
        )
    )

    assert result.structured_content["errors"] == [{"code": "PERIOD_IN_FUTURE"}]
    assert transport.calls == []


def test_mcp_keeps_the_half_specified_period_error():
    transport = RecordingTransport()
    service = CompetitorResearchService(MCPProviderClient(config=sif_mcp_config(), transport=transport))
    server = create_competitor_research_mcp_server(service=service)

    result = asyncio.run(
        server.call_tool(
            "compare_traffic_keywords",
            {
                "own_asin": "B0OWN00001",
                "competitor_asins": ["B0COMP0001"],
                "country": "US",
                "period_start": date.today().isoformat(),
            },
        )
    )

    assert result.structured_content["errors"] == [{"code": "PERIOD_INCOMPLETE"}]
    assert transport.calls == []

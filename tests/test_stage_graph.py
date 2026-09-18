from amazon_ops.events import InMemoryEventHub, StageController, StageEventType, StageName
from amazon_ops.events import get_stage_reporter
from amazon_ops.graph import build_controller_graph
from amazon_ops.models import (
    Action,
    AgentTask,
    Domain,
    Finding,
    FinalResponse,
    Hypothesis,
    QueryScope,
    RequestRoute,
    RiskLevel,
    SpecialistName,
    SpecialistResult,
    UnderstandRequestResult,
    UserIntent,
)


class FakeInterpreter:
    def __init__(self, result):
        self.result = result

    def invoke(self, state):
        return self.result


class FakeSpecialist:
    def __init__(self, name, hypothesis=None):
        self.name = name.value
        self.hypothesis = hypothesis

    def invoke(self, task: AgentTask, scope: QueryScope, state):
        reporter = get_stage_reporter(state)
        if reporter:
            reporter.emit("tool.progress", tool="fake_tool", records_received=10)
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName(self.name),
            summary=f"{self.name} 完成",
            findings=[
                Finding(
                    finding="发现",
                    severity="medium",
                    confidence=0.9,
                    hypotheses=[self.hypothesis] if self.hypothesis else [],
                )
            ],
        )


class FakeDirectResponder:
    def invoke(self, state):
        return FinalResponse(answer="ACOS 是广告花费与广告销售额的比率。")


class HistoryDirectResponder:
    def invoke(self, state):
        assert state["conversation_history"][-1]["content"] == "核心词 garage door seal 的差距最大。"
        return FinalResponse(
            answer="根据本次对话此前取得的数据，差距最大的是 garage door seal。"
        )


class ExplodingHistoryResponder:
    def invoke(self, state):
        raise RuntimeError("provider secret failure detail")


class MustNotRunSpecialist:
    name = SpecialistName.ADVERTISING.value

    def invoke(self, task, scope, state):
        raise AssertionError("history response must not call a specialist")


class NeedsInputSpecialist:
    name = SpecialistName.LISTING_CONTENT.value

    def invoke(self, task, scope, state):
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.LISTING_CONTENT,
            status="needs_input",
            summary="请补充商品材质、尺寸和核心卖点。",
        )


class FailedSpecialist:
    name = SpecialistName.ADVERTISING.value

    def invoke(self, task, scope, state):
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.ADVERTISING,
            status="failed",
            summary="广告报表查询未成功完成，系统未读取任何广告数据，因此无法给出可靠结论。请稍后重试。",
            errors=[{"code": "AD_REPORT_QUERY_FAILED"}],
        )


class DegradedSpecialist:
    name = SpecialistName.COMPETITOR_ADVERTISING.value

    def invoke(self, task, scope, state):
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.COMPETITOR_ADVERTISING,
            status="degraded",
            summary="竞品分析已降级完成，部分趋势数据不可用。",
            errors=[{"code": "INVALID_REQUEST", "tool": "traffic_trend"}],
        )


def test_controller_graph_emits_analysis_verification_and_synthesis_stages():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.STORE, action=Action.DIAGNOSE, confidence=0.95),
        scope=QueryScope(shop_ids=["10001"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="诊断店铺经营异常",
    )
    specialists = {
        "sales_profit": FakeSpecialist(
            SpecialistName.SALES_PROFIT,
            Hypothesis(
                description="检查广告流量",
                requires_agent=SpecialistName.ADVERTISING,
                confidence=0.8,
            ),
        ),
        "advertising": FakeSpecialist(SpecialistName.ADVERTISING),
    }
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists=specialists,
        stages=StageController(hub),
    )

    graph.invoke({"messages": [], "request_id": "stage-run-1"})
    events = hub.events_after("stage-run-1")

    started = [event.stage for event in events if event.event == StageEventType.STAGE_STARTED]
    assert started == [
        StageName.UNDERSTANDING,
        StageName.PLANNING,
        StageName.ANALYSIS,
        StageName.VERIFICATION,
        StageName.SYNTHESIS,
    ]
    assert events[-1].event == StageEventType.RUN_COMPLETED
    unit_events = [
        event.data["kind"]
        for event in events
        if event.event == StageEventType.STAGE_PROGRESS
        and event.data.get("kind", "").startswith("unit.")
    ]
    assert unit_events == ["unit.started", "unit.completed", "unit.started", "unit.completed"]
    tool_progress = [
        event
        for event in events
        if event.event == StageEventType.STAGE_PROGRESS
        and event.data.get("kind") == "tool.progress"
    ]
    assert len(tool_progress) == 2
    assert tool_progress[0].data["unit_id"] == "sales_profit"


def test_clarification_emits_waiting_stage_without_run_completion():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.PRODUCT, action=Action.DIAGNOSE, confidence=0.8),
        scope=QueryScope(shop_ids=["10001"]),
        route=RequestRoute.CLARIFY,
        risk_level=RiskLevel.READ_ONLY,
        missing_fields=["product_identifier"],
        clarification_question="你要分析哪个商品？",
        normalized_request="诊断商品",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={},
        stages=StageController(hub),
    )

    graph.invoke({"messages": [], "request_id": "stage-run-2"})
    events = hub.events_after("stage-run-2")

    assert events[-1].event == StageEventType.STAGE_WAITING
    assert events[-1].stage == StageName.WAITING_INPUT
    assert not any(event.event == StageEventType.RUN_COMPLETED for event in events)


def test_direct_deepseek_response_is_carried_by_terminal_sse_event():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.ADVERTISING, action=Action.EXPLAIN, confidence=0.98),
        scope=QueryScope(),
        route=RequestRoute.RESPOND,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="解释 ACOS",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={},
        responder=FakeDirectResponder(),
        stages=StageController(hub),
    )

    graph.invoke({"messages": [], "request_id": "stage-direct-1"})
    terminal = hub.events_after("stage-direct-1")[-1]

    assert terminal.event == StageEventType.RUN_COMPLETED
    assert terminal.data["result"]["answer"].startswith("ACOS")


def test_explicit_history_followup_forces_respond_and_skips_specialists():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.KEYWORD, action=Action.QUERY, confidence=0.98),
        scope=QueryScope(asins=["B0OWN"]),
        # Deliberately inconsistent to verify the graph's safety guard.
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="根据已给出的机会说明应调整哪些词",
        answer_source="history",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: MustNotRunSpecialist()},
        responder=HistoryDirectResponder(),
        stages=StageController(hub),
    )

    result = graph.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": "针对给出的机会，我应该从哪些广告类型和词调整？",
                }
            ],
            "conversation_history": [
                {"role": "assistant", "content": "核心词 garage door seal 的差距最大。"}
            ],
            "current_user_message": "针对给出的机会，我应该从哪些广告类型和词调整？",
            "request_id": "stage-history-1",
        }
    )
    events = hub.events_after("stage-history-1")
    intent_event = next(
        event
        for event in events
        if event.event == StageEventType.STAGE_PROGRESS
        and event.data.get("kind") == "intent.resolved"
    )

    assert result["route"] == RequestRoute.RESPOND.value
    assert result["answer_source"] == "history"
    assert result["final_response"]["answer"].startswith("根据本次对话")
    assert intent_event.data["answer_source"] == "history"
    assert not any(event.stage == StageName.PLANNING for event in events)
    assert not any(
        event.data.get("kind", "").startswith(("unit.", "tool."))
        for event in events
        if event.event == StageEventType.STAGE_PROGRESS
    )
    assert events[-1].event == StageEventType.RUN_COMPLETED


def test_history_response_failure_is_sanitized_without_live_fallback():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.COMPETITOR, action=Action.EXPLAIN, confidence=0.95),
        scope=QueryScope(),
        route=RequestRoute.RESPOND,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="解释上面的报告",
        answer_source="history",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: MustNotRunSpecialist()},
        responder=ExplodingHistoryResponder(),
        stages=StageController(hub),
    )

    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": "解释上面的报告"}],
            "conversation_history": [
                {"role": "assistant", "content": "报告中有已确认的历史结论。"}
            ],
            "current_user_message": "解释上面的报告",
            "request_id": "stage-history-failed",
        }
    )
    events = hub.events_after("stage-history-failed")
    answer = result["final_response"]["answer"]

    assert "本轮未重新调用数据工具" in answer
    assert "provider secret failure detail" not in str(events)
    assert not any(event.stage == StageName.PLANNING for event in events)
    assert events[-1].event == StageEventType.RUN_COMPLETED


def test_old_understanding_payload_defaults_to_live_source():
    understanding = UnderstandRequestResult.model_validate(
        {
            "intent": {"domain": "advertising", "action": "query", "confidence": 0.9},
            "scope": {},
            "route": "execute",
            "risk_level": "read_only",
            "normalized_request": "查询广告花费",
        }
    )

    assert understanding.answer_source == "live"


def test_history_source_without_explicit_reference_is_forced_to_live_execution():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.95),
        scope=QueryScope(),
        route=RequestRoute.RESPOND,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="这个产品销量多少",
        answer_source="history",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: FakeSpecialist(SpecialistName.ADVERTISING)},
        stages=StageController(hub),
    )

    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": "这个产品销量多少？"}],
            "conversation_history": [
                {"role": "assistant", "content": "旧的销量数据"}
            ],
            "current_user_message": "这个产品销量多少？",
            "request_id": "stage-history-not-explicit",
        }
    )

    assert result["answer_source"] == "live"
    assert result["route"] == RequestRoute.EXECUTE.value
    assert any(
        event.stage == StageName.PLANNING
        for event in hub.events_after("stage-history-not-explicit")
    )


def test_refresh_marker_overrides_history_reuse():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.95),
        scope=QueryScope(),
        route=RequestRoute.RESPOND,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="重新查询最新销量",
        answer_source="history",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: FakeSpecialist(SpecialistName.ADVERTISING)},
        stages=StageController(hub),
    )

    result = graph.invoke(
        {
            "messages": [{"role": "user", "content": "重新查一下刚才的最新销量"}],
            "conversation_history": [
                {"role": "assistant", "content": "旧的销量数据"}
            ],
            "current_user_message": "重新查一下刚才的最新销量",
            "request_id": "stage-history-refresh",
        }
    )

    assert result["answer_source"] == "live"
    assert result["route"] == RequestRoute.EXECUTE.value
    assert any(
        event.data.get("kind") == "unit.started"
        for event in hub.events_after("stage-history-refresh")
        if event.event == StageEventType.STAGE_PROGRESS
    )


def test_controller_graph_can_resume_after_understanding_without_calling_interpreter():
    class MustNotRunInterpreter:
        def invoke(self, state):
            raise AssertionError("checkpoint recovery must not repeat understanding")

    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=MustNotRunInterpreter(),
        specialists={},
        responder=FakeDirectResponder(),
        stages=StageController(hub),
    )
    result = graph.invoke(
        {
            "messages": [],
            "request_id": "stage-resume-1",
            "resume_next": "direct_response",
            "route": RequestRoute.RESPOND.value,
            "understanding": UnderstandRequestResult(
                intent=UserIntent(domain=Domain.ADVERTISING, action=Action.EXPLAIN, confidence=0.9),
                scope=QueryScope(),
                route=RequestRoute.RESPOND,
                risk_level=RiskLevel.READ_ONLY,
                normalized_request="解释 ACOS",
            ).model_dump(mode="json"),
        }
    )

    assert result["final_response"]["answer"].startswith("ACOS")
    assert all(event.stage != StageName.UNDERSTANDING for event in hub.events_after("stage-resume-1"))


def test_controller_waits_when_listing_agent_needs_product_information():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.LISTING, action=Action.CREATE, confidence=0.98),
        scope=QueryScope(marketplaces=["US"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="为商品编写 Listing",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.LISTING_CONTENT.value: NeedsInputSpecialist()},
        stages=StageController(hub),
    )

    result = graph.invoke({"messages": [], "request_id": "stage-listing-input"})
    terminal = hub.events_after("stage-listing-input")[-1]
    unit_events = [
        event.data.get("kind")
        for event in hub.events_after("stage-listing-input")
        if event.event == StageEventType.STAGE_PROGRESS
        and str(event.data.get("kind", "")).startswith("unit.")
    ]

    assert terminal.event == StageEventType.STAGE_WAITING
    assert terminal.stage == StageName.WAITING_INPUT
    assert unit_events == ["unit.started", "unit.waiting"]
    assert "商品材质" in result["final_response"]["answer"]


def test_controller_marks_the_run_failed_when_every_specialist_fails():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.98),
        scope=QueryScope(),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="查询广告花费最高的广告",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: FailedSpecialist()},
        stages=StageController(hub),
    )

    result = graph.invoke({"messages": [], "request_id": "stage-advertising-failed"})
    terminal = hub.events_after("stage-advertising-failed")[-1]

    assert result["final_response"]["answer"].startswith("广告报表查询未成功完成")
    assert terminal.event == StageEventType.RUN_FAILED
    assert terminal.data["error"] == result["final_response"]["answer"]


def test_controller_emits_degraded_unit_without_failing_run():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=0.98),
        scope=QueryScope(own_asin="B0OWN", competitor_asins=["B0COMPETITOR"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="对比竞品",
    )
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={
            SpecialistName.COMPETITOR_ADVERTISING.value: DegradedSpecialist()
        },
        stages=StageController(hub),
    )

    graph.invoke({"messages": [], "request_id": "stage-competitor-degraded"})
    events = hub.events_after("stage-competitor-degraded")
    unit_events = [
        event.data.get("kind")
        for event in events
        if event.event == StageEventType.STAGE_PROGRESS
        and str(event.data.get("kind", "")).startswith("unit.")
    ]

    assert unit_events == ["unit.started", "unit.degraded"]
    assert events[-1].event == StageEventType.RUN_COMPLETED

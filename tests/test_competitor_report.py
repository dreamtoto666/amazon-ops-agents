from amazon_ops.competitor_report import SECTION_TITLES, render_competitor_report, render_competitor_report_section, validate_competitor_report
from amazon_ops.models import CompetitorAdvertisingReport, CompetitorReportSection


def test_report_validation_drops_unknown_evidence_and_private_competitor_metrics():
    report = CompetitorAdvertisingReport(
        title="报告",
        sections=[
            CompetitorReportSection(
                key="traffic_keyword_lookup",
                status="available",
                content="竞品 ACOS 为 12%，需要立即跟随。",
                evidence_refs=["known", "invented"],
            ),
            CompetitorReportSection(
                key="multi_variant_organic_position",
                status="available",
                content="竞品的公开流量结构存在差异。",
                evidence_refs=["invented"],
            ),
        ],
    )
    state = {"specialist_results": [{"artifacts": [{"artifact_id": "known"}]}]}

    validated = validate_competitor_report(report, state)

    summary = next(item for item in validated.sections if item.key == "traffic_keyword_lookup")
    traffic = next(item for item in validated.sections if item.key == "multi_variant_organic_position")
    assert summary.status == "partial"
    assert "不可得" in summary.content
    assert traffic.status == "partial"
    assert traffic.evidence_refs == []
    assert "EVIDENCE_REFERENCE_MISSING" in traffic.missing_reasons


def test_private_metric_unavailable_disclaimer_does_not_erase_valid_budget_plan():
    report = CompetitorAdvertisingReport(
        title="报告",
        sections=[
            CompetitorReportSection(
                key="traffic_keyword_reverse_lookup",
                status="partial",
                content=(
                    "竞品花费、竞价、ACOS 和 ROAS 不可得，不作数值判断。\n"
                    "建议将我方测试预算控制在可承受范围，先用 10% 流量进行小流量验证。"
                ),
            )
        ],
    )

    validated = validate_competitor_report(report, {})
    section = next(item for item in validated.sections if item.key == "traffic_keyword_reverse_lookup")

    assert "10%" in section.content
    assert section.missing_reasons == []


def test_report_validation_accepts_evidence_from_competitor_data_modules():
    report = CompetitorAdvertisingReport(
        title="报告",
        sections=[
            CompetitorReportSection(
                key="traffic_keyword_lookup",
                status="available",
                content="父 ASIN 查询返回了可追溯的变体流量分布。",
                evidence_refs=["variant-evidence"],
            )
        ],
    )
    state = {
        "competitor_data_modules": {
            "traffic_keyword_lookup": {
                "status": "available",
                "evidence_ids": ["variant-evidence"],
            }
        }
    }

    validated = validate_competitor_report(report, state)

    section = next(item for item in validated.sections if item.key == "traffic_keyword_lookup")
    assert section.status == "available"
    assert section.evidence_refs == ["variant-evidence"]


def test_report_renderer_always_outputs_every_template_section_without_placeholders():
    report = CompetitorAdvertisingReport(
        title="亚马逊竞品广告对比报告",
        sections=[
            CompetitorReportSection(
                key="traffic_keyword_lookup", status="unavailable", content="暂无可验证数据。"
            )
        ],
    )
    validated = validate_competitor_report(report, {})

    markdown = render_competitor_report(validated)

    assert "## 01｜模块：查流量词" in markdown
    assert "## 04｜模块：查推荐专栏" in markdown
    assert "{{" not in markdown


def test_unavailable_recommendation_placement_still_renders_the_fixed_table():
    section = CompetitorReportSection(
        key="recommendation_placement",
        status="unavailable",
        content="推荐专栏到直接归属 Campaign 的映射未返回。",
        missing_reasons=["PLACEMENT_CAMPAIGN_MAPPING_MISSING"],
    )

    markdown = render_competitor_report_section(section)

    assert "| 广告词数量 |" not in markdown


def test_each_unavailable_module_still_renders_its_fixed_table():
    for key in SECTION_TITLES:
        markdown = render_competitor_report_section(
            CompetitorReportSection(key=key, status="unavailable", content="该模块数据未返回。")
        )
        assert "|" in markdown


# --- 报告节点的错误路径（之前没有任何测试走过这里）---------------------------

class ExplodingReportWriter:
    def __init__(self, error: Exception = RuntimeError("writer failed")):
        self.error = error
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        raise self.error

    def invoke_section(self, state, section_key):
        self.calls += 1
        raise self.error


class EventuallySuccessfulReportWriter:
    def __init__(self):
        self.calls = 0

    def invoke(self, state):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary writer failure")
        return CompetitorAdvertisingReport(
            title="重试后生成的报告",
            sections=[
                CompetitorReportSection(
                    key="traffic_keyword_lookup",
                    status="partial",
                    content="已使用既有数据生成报告。",
                )
            ],
        )

    def invoke_section(self, state, section_key):
        self.calls += 1
        if self.calls < 3:
            raise RuntimeError("temporary writer failure")
        return CompetitorReportSection(
            key=section_key,
            status="partial",
            content="已使用既有数据生成报告。",
        )


class CountingReportWriter:
    def __init__(self):
        self.section_keys = []

    def invoke_section(self, state, section_key):
        self.section_keys.append(section_key)
        return CompetitorReportSection(
            key=section_key,
            status="partial",
            content=f"{section_key} 已完成。",
        )


def _report_graph(writer, hub, report_checkpoint=None, specialist=None):
    from amazon_ops.events import StageController
    from amazon_ops.graph import build_controller_graph
    from amazon_ops.models import (
        Action,
        Domain,
        QueryScope,
        RequestRoute,
        RiskLevel,
        SpecialistName,
        UnderstandRequestResult,
        UserIntent,
    )
    from test_stage_graph import FakeInterpreter, FakeSpecialist

    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=0.95),
        scope=QueryScope(marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="生成竞品对比报告",
        response_mode="competitor_report",
    )
    return build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={
            SpecialistName.COMPETITOR_ADVERTISING.value: specialist
            or FakeSpecialist(SpecialistName.COMPETITOR_ADVERTISING)
        },
        responder=None,
        stages=StageController(hub),
        competitor_report_writer=writer,
        report_checkpoint=report_checkpoint,
    )


def test_failed_report_sections_degrade_without_failing_the_run():

    from amazon_ops.events import InMemoryEventHub, StageEventType

    hub = InMemoryEventHub()
    writer = ExplodingReportWriter()
    graph = _report_graph(writer, hub)

    result = graph.invoke({"messages": [], "request_id": "report-failure-1"})

    answer = result["final_response"]["answer"]
    assert "本章生成失败" in answer
    terminal = hub.events_after("report-failure-1")[-1]
    assert terminal.event == StageEventType.RUN_COMPLETED
    assert writer.calls == 12
    # 不得把原始异常文本（可能包含整份报告）泄露到事件流。
    assert "writer failed" not in str(terminal.data)


def test_report_writer_retries_twice_and_uses_the_successful_result():
    from amazon_ops.events import InMemoryEventHub, StageEventType

    hub = InMemoryEventHub()
    writer = EventuallySuccessfulReportWriter()
    graph = _report_graph(writer, hub)

    result = graph.invoke({"messages": [], "request_id": "report-retry-success"})

    retry_events = [
        event
        for event in hub.events_after("report-retry-success")
        if event.event == StageEventType.STAGE_PROGRESS
        and event.data.get("kind") == "report.retrying"
    ]
    assert writer.calls == 6
    assert [event.data["attempt"] for event in retry_events] == [2, 3]
    assert "已使用既有数据生成报告" in result["final_response"]["answer"]

    streamed = "".join(
        event.data["text"]
        for event in hub.events_after("report-retry-success")
        if event.data.get("kind") == "report.section.delta"
    )
    assert streamed == result["final_response"]["answer"]


def test_report_recovery_skips_checkpointed_sections_and_replays_their_text():
    from amazon_ops.events import InMemoryEventHub

    checkpoints = []
    first_writer = CountingReportWriter()
    first_hub = InMemoryEventHub()
    first_graph = _report_graph(
        first_writer, first_hub, report_checkpoint=lambda _run_id, state: checkpoints.append(state)
    )

    first_graph.invoke({"messages": [], "request_id": "report-checkpoint-source"})
    resume_state = checkpoints[2]
    resume_state["request_id"] = "report-checkpoint-resume"

    resumed_writer = CountingReportWriter()
    resumed_hub = InMemoryEventHub()
    resumed_graph = _report_graph(resumed_writer, resumed_hub)
    result = resumed_graph.invoke(resume_state)

    assert len(resumed_writer.section_keys) == 1
    assert resumed_writer.section_keys[0] == "recommendation_placement"
    replayed = [
        event
        for event in resumed_hub.events_after("report-checkpoint-resume")
        if event.data.get("kind") == "report.section.delta"
    ]
    assert len(replayed) == 4
    assert "## 01｜模块：查流量词" in replayed[0].data["text"]
    assert result["final_response"]["answer"] == "".join(
        event.data["text"] for event in replayed
    )


def test_report_mode_suppresses_specialist_user_facing_deltas():
    from amazon_ops.events import InMemoryEventHub, get_stage_reporter
    from amazon_ops.models import SpecialistName, SpecialistResult

    class ChattySpecialist:
        name = SpecialistName.COMPETITOR_ADVERTISING.value

        def invoke(self, task, scope, state):
            reporter = get_stage_reporter(state)
            assert reporter is not None
            reporter.emit("response.delta", text="不应直接展示的专家正文")
            reporter.emit("response.completed")
            return SpecialistResult(
                task_id=task.task_id,
                agent=SpecialistName.COMPETITOR_ADVERTISING,
                summary="结构化取数完成",
            )

    hub = InMemoryEventHub()
    graph = _report_graph(CountingReportWriter(), hub, specialist=ChattySpecialist())

    graph.invoke({"messages": [], "request_id": "report-suppress-specialist"})

    events = hub.events_after("report-suppress-specialist")
    assert not any(event.data.get("kind") == "response.delta" for event in events)
    assert any(event.data.get("kind") == "report.section.delta" for event in events)


def test_a_failed_report_writer_does_not_leak_the_raw_report_into_events():
    from amazon_ops.events import InMemoryEventHub, StageEventType

    secret = "竞品 ACOS 为 12% 的内部草稿内容"
    hub = InMemoryEventHub()
    graph = _report_graph(ExplodingReportWriter(ValueError(secret)), hub)

    graph.invoke({"messages": [], "request_id": "report-failure-2"})

    emitted = str([event.data for event in hub.events_after("report-failure-2")])
    assert secret not in emitted

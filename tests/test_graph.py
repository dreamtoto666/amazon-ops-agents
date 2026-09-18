from amazon_ops.graph import build_controller_graph
from amazon_ops.models import (
    Action,
    AgentTask,
    Domain,
    Finding,
    Hypothesis,
    QueryScope,
    RequestRoute,
    RiskLevel,
    SpecialistName,
    SpecialistResult,
    UnderstandRequestResult,
    UserIntent,
    CompetitorAdvertisingReport,
    CompetitorReportSection,
)


class FakeInterpreter:
    def __init__(self, result: UnderstandRequestResult):
        self.result = result

    def invoke(self, state):
        return self.result


class FakeSpecialist:
    def __init__(self, name: SpecialistName, hypothesis: Hypothesis | None = None):
        self.name = name.value
        self.hypothesis = hypothesis

    def invoke(self, task: AgentTask, scope: QueryScope, state):
        hypotheses = [self.hypothesis] if self.hypothesis else []
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName(self.name),
            summary=f"{self.name} 完成",
            findings=[
                Finding(
                    finding=f"{self.name} 发现",
                    severity="medium",
                    confidence=0.9,
                    hypotheses=hypotheses,
                )
            ],
        )


class FakeCompetitorReportWriter:
    def __init__(self):
        self.state = None

    def invoke(self, state):
        self.state = state
        return CompetitorAdvertisingReport(
            title="亚马逊竞品广告对比报告",
            sections=[
                CompetitorReportSection(
                    key="traffic_keyword_lookup",
                    status="partial",
                    content="本期仅基于已读取的竞品观察与我方广告报表证据生成。",
                    missing_reasons=["部分章节数据不足"],
                )
            ],
            limitations=["竞品私有广告后台指标不可得。"],
        )

    def invoke_section(self, state, section_key):
        self.state = state
        return CompetitorReportSection(
            key=section_key,
            status="partial",
            content=f"{section_key} 仅基于已读取的竞品观察生成。",
            missing_reasons=["部分章节数据不足"],
        )


def test_graph_executes_initial_and_followup_specialist():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.STORE, action=Action.DIAGNOSE, confidence=0.95),
        scope=QueryScope(shop_ids=["10001"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="诊断店铺经营异常",
    )
    specialists = {
        SpecialistName.SALES_PROFIT.value: FakeSpecialist(
            SpecialistName.SALES_PROFIT,
            Hypothesis(
                description="需要检查广告流量",
                requires_agent=SpecialistName.ADVERTISING,
                confidence=0.8,
            ),
        ),
        SpecialistName.ADVERTISING.value: FakeSpecialist(SpecialistName.ADVERTISING),
    }
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists=specialists,
    )

    result = graph.invoke({"messages": [], "request_id": "r-1"})

    assert set(result["called_agents"]) == {"sales_profit", "advertising"}
    assert result["round"] == 2
    assert len(result["specialist_results"]) == 2
    assert result["final_response"]["answer"]


def test_graph_stops_and_asks_for_clarification():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.PRODUCT, action=Action.DIAGNOSE, confidence=0.8),
        scope=QueryScope(shop_ids=["10001"]),
        route=RequestRoute.CLARIFY,
        risk_level=RiskLevel.READ_ONLY,
        missing_fields=["product_identifier"],
        clarification_question="你要分析哪个 ASIN、MSKU 或 SKU？",
        normalized_request="诊断指定商品销量下降原因",
    )
    graph = build_controller_graph(interpreter=FakeInterpreter(understanding), specialists={})

    result = graph.invoke({"messages": [], "request_id": "r-2"})

    assert result["final_response"]["answer"] == "你要分析哪个 ASIN、MSKU 或 SKU？"
    assert result["specialist_results"] == []


def test_graph_executes_imported_ad_report_query_without_shop_or_period():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.96),
        scope=QueryScope(),
        route=RequestRoute.CLARIFY,
        risk_level=RiskLevel.READ_ONLY,
        missing_fields=["shop_id", "period"],
        clarification_question="请提供店铺和时间范围。",
        normalized_request="查询花费最多的广告",
    )
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={SpecialistName.ADVERTISING.value: FakeSpecialist(SpecialistName.ADVERTISING)},
    )

    result = graph.invoke({"messages": [], "request_id": "r-imported-ad-report"})

    assert result["route"] == RequestRoute.EXECUTE.value
    assert result["missing_fields"] == []
    assert result["clarification_question"] is None
    assert result["called_agents"] == [SpecialistName.ADVERTISING.value]


def test_graph_routes_attached_image_to_direct_response():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.STORE, action=Action.QUERY, confidence=0.9),
        scope=QueryScope(shop_ids=["1"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="请分析这张图片",
    )
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={},
    )

    result = graph.invoke({
        "messages": [{"role": "user", "content": "请分析这张图片"}],
        "request_id": "r-img",
        "image_attachments": ["data:image/png;base64,AAAA"],
    })

    assert result["route"] == RequestRoute.RESPOND.value
    assert result["final_response"]["answer"]


def test_graph_uses_report_writer_only_for_explicit_competitor_report_requests():
    understanding = UnderstandRequestResult(
        intent=UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=0.95),
        scope=QueryScope(marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="生成竞品对比报告",
        response_mode="competitor_report",
    )
    writer = FakeCompetitorReportWriter()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(understanding),
        specialists={
            SpecialistName.COMPETITOR_ADVERTISING.value: FakeSpecialist(SpecialistName.COMPETITOR_ADVERTISING),
            SpecialistName.ADVERTISING.value: FakeSpecialist(SpecialistName.ADVERTISING),
        },
        competitor_report_writer=writer,
    )

    result = graph.invoke({"messages": [], "request_id": "report-1"})

    assert writer.state is not None
    assert set(result["called_agents"]) == {"competitor_advertising", "advertising"}
    assert "## 01｜模块：查流量词" in result["final_response"]["answer"]
    assert "## 04｜模块：查推荐专栏" in result["final_response"]["answer"]
    assert result["final_response"]["deliverables"][0]["type"] == "competitor_advertising_report"

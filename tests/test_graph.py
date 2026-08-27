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

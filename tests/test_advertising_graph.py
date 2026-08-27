from datetime import date, datetime, timezone

from amazon_ops.advertising import (
    AdDiagnosticRequest,
    AdEntityRef,
    AdEntityType,
    AdvertisingDiagnosticServices,
    AnomalyType,
    AttributionResult,
    DataInspectionResult,
    DetectedAnomaly,
    DiagnosticEvidence,
    OperationsTodo,
    ProblemCause,
    ReviewTodoResult,
    StrategyRecommendation,
    StrategyResult,
    build_advertising_diagnostic_graph,
)


def request_payload():
    return AdDiagnosticRequest(
        profile_ids=["profile-1"],
        current_period={"start": date(2026, 8, 17), "end": date(2026, 8, 17)},
        baseline_period={"start": date(2026, 8, 10), "end": date(2026, 8, 10)},
    ).model_dump(mode="json")


class NoAnomalyInspector:
    def invoke(self, request, state):
        return DataInspectionResult()


class MustNotRun:
    def invoke(self, request, state):
        raise AssertionError("agent should not run")


class EmptyReviewer:
    def invoke(self, request, state):
        return ReviewTodoResult()


def test_no_anomaly_skips_attribution_and_strategy():
    graph = build_advertising_diagnostic_graph(
        services=AdvertisingDiagnosticServices(
            inspector=NoAnomalyInspector(),
            attribution=MustNotRun(),
            strategist=MustNotRun(),
            reviewer=EmptyReviewer(),
        )
    )

    result = graph.invoke(
        {
            "request_id": "ad-run-1",
            "run_id": "ad-run-1",
            "trace_id": "trace-1",
            "span_id": "span-1",
            "stage": "queued",
            "request": request_payload(),
        }
    )

    assert result["final_result"]["status"] == "no_anomaly"
    assert result["final_result"]["todos"] == []


def anomaly_fixture():
    entity = AdEntityRef(
        entity_type=AdEntityType.CAMPAIGN,
        entity_id="campaign-1",
        profile_id="profile-1",
        name="核心词-SP",
    )
    evidence = DiagnosticEvidence(
        evidence_id="e-1",
        trace_id="trace-2",
        span_id="span-2",
        stage="data_inspection",
        tool="ad_campaign_report",
        query={"profile_ids": ["profile-1"]},
        fetched_at=datetime.now(timezone.utc),
        record_count=1,
    )
    anomaly = DetectedAnomaly(
        anomaly_id="a-1",
        anomaly_type=AnomalyType.ACOS_RISE,
        entity=entity,
        metric="acos",
        current_value=0.62,
        baseline_value=0.30,
        relative_change=1.07,
        severity="high",
        confidence=0.9,
        evidence_refs=["e-1"],
    )
    return entity, evidence, anomaly


class AnomalyInspector:
    def invoke(self, request, state):
        _, evidence, anomaly = anomaly_fixture()
        return DataInspectionResult(anomalies=[anomaly], evidence=[evidence])


class TwoRoundAttribution:
    def __init__(self):
        self.calls = 0

    def invoke(self, request, state):
        self.calls += 1
        if self.calls == 1:
            return AttributionResult(
                needs_more_evidence=True,
                requested_tools=["ad_campaign_search_term_report"],
            )
        return AttributionResult(
            causes=[
                ProblemCause(
                    cause_id="c-1",
                    anomaly_ids=["a-1"],
                    category="conversion",
                    statement="高点击搜索词未产生订单。",
                    confidence=0.82,
                    evidence_refs=["e-1"],
                )
            ]
        )


class FakeStrategist:
    def invoke(self, request, state):
        entity, _, _ = anomaly_fixture()
        return StrategyResult(
            recommendations=[
                StrategyRecommendation(
                    recommendation_id="r-1",
                    cause_ids=["c-1"],
                    action_type="add_negative",
                    target=entity,
                    title="复核并否定高消耗无单搜索词",
                    rationale="减少无效消耗。",
                    risk_level="high_risk_write",
                    evidence_refs=["e-1"],
                )
            ]
        )


class FakeReviewer:
    def invoke(self, request, state):
        entity, _, _ = anomaly_fixture()
        return ReviewTodoResult(
            todos=[
                OperationsTodo(
                    todo_id="todo-1",
                    recommendation_id="r-1",
                    profile_id="profile-1",
                    title="复核无单搜索词",
                    description="确认相关性后再添加否定词。",
                    priority="high",
                    action_type="add_negative",
                    target=entity,
                    evidence_refs=["e-1"],
                    dedupe_key="profile-1:add_negative:campaign-1",
                )
            ]
        )


def test_anomaly_runs_bounded_attribution_and_creates_todo():
    attribution = TwoRoundAttribution()
    graph = build_advertising_diagnostic_graph(
        services=AdvertisingDiagnosticServices(
            inspector=AnomalyInspector(),
            attribution=attribution,
            strategist=FakeStrategist(),
            reviewer=FakeReviewer(),
        )
    )

    result = graph.invoke(
        {
            "request_id": "ad-run-2",
            "run_id": "ad-run-2",
            "trace_id": "trace-2",
            "span_id": "span-2",
            "stage": "queued",
            "request": request_payload(),
        }
    )

    assert attribution.calls == 2
    assert result["final_result"]["status"] == "completed"
    assert result["final_result"]["trace_id"] == "trace-2"
    assert result["final_result"]["todos"][0]["approval_required"] is True

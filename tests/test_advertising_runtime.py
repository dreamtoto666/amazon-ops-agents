from __future__ import annotations

import json
from datetime import date, datetime, timezone
from time import monotonic, sleep

from fastapi.testclient import TestClient

from amazon_ops.advertising.agents import (
    EvidenceBasedProblemAttributionAgent,
    RuleBasedDataInspectionAgent,
)
from amazon_ops.advertising.gateway import AdvertisingReport
from amazon_ops.advertising.models import (
    AdDiagnosticRequest,
    AdEntityRef,
    AdEntityType,
    AdShop,
    AnomalyType,
    DataInspectionResult,
    DiagnosticPeriod,
    DetectedAnomaly,
    DiagnosticEvidence,
    InspectionHypothesis,
    AttributionResult,
)
from amazon_ops.advertising.llm_agents import DeepSeekDataInspectionAgent
from amazon_ops.advertising.llm_models import InspectionHypothesisLLMOutput
from amazon_ops.llm import LLMError
from amazon_ops.advertising.runtime import AdvertisingRunManager, ResolvedExecutionScope, _LedgerGateway, _ScopedAdvertisingGateway
from amazon_ops.api import create_app
from amazon_ops.auth import AuthUser
from amazon_ops.idempotency import InMemoryIdempotencyRegistry
from amazon_ops.listing.mcp import MCPProvider, MCPToolResult
from amazon_ops.llm import LLMError


def trace(tool: str) -> MCPToolResult:
    now = datetime.now(timezone.utc)
    return MCPToolResult(
        call_id=f"call-{tool}",
        provider=MCPProvider.LINGXING,
        tool=tool,
        arguments={},
        payload={},
        started_at=now,
        finished_at=now,
        duration_ms=1,
    )


class FakeAdvertisingGateway:
    def list_shops(self):
        return [
            AdShop(
                profile_id="profile-1",
                sid=1,
                store_id=2,
                alias="测试店铺",
                country="US",
            )
        ]

    def campaign_report(self, *, profile_ids, period, campaign_ids=None):
        current = period.start == date(2026, 8, 18)
        row = {
            "profile_id": "profile-1",
            "campaign_id": "campaign-1",
            "name": "核心词广告",
            "impressions": 1000 if current else 900,
            "clicks": 20 if current else 15,
            "spends": 100 if current else 30,
            "sales": 0 if current else 100,
            "orders": 0 if current else 2,
        }
        return AdvertisingReport(
            tool="ad_campaign_report",
            arguments={"profile_ids": profile_ids},
            rows=[row],
            total=1,
            trace=trace("ad_campaign_report"),
        )

    def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
        rows = [{"campaign_id": "campaign-1"}]
        if tool == "ad_campaign_search_term_report":
            rows = [{
                "campaign_id": "campaign-1",
                "search_term": "unrelated door seal",
                "spends": 80,
                "sales": 0,
                "orders": 0,
            }]
        return AdvertisingReport(
            tool=tool,
            arguments={"profile_ids": profile_ids, "campaign_id": campaign_ids},
            rows=rows,
            total=1,
            trace=trace(tool),
        )


def test_private_scope_filters_reports_and_removes_asin_store_and_name_fields():
    gateway = _ScopedAdvertisingGateway(
        FakeAdvertisingGateway(),
        ResolvedExecutionScope(sid="sensitive-sid", child_asins=("B0SECRET",), campaign_ids=("campaign-1",)),
    )
    report = gateway.campaign_report(profile_ids=["attempted-store"], period=DiagnosticPeriod(start=date(2026, 8, 1), end=date(2026, 8, 1)), campaign_ids=["campaign-1", "outside"])

    serialized = json.dumps({"rows": report.rows, "arguments": report.arguments}, ensure_ascii=False)
    assert "sensitive-sid" not in serialized
    assert "B0SECRET" not in serialized
    assert "核心词广告" not in serialized
    assert report.rows[0]["profile_id"] == "scoped-store"
    assert report.arguments == {"product_scope_applied": True, "campaign_count": 1}


def test_agent_safe_campaign_scope_validates_without_store_or_asin():
    request = AdDiagnosticRequest.model_validate({
        "profile_ids": [], "campaign_ids": ["campaign-1"],
        "current_period": {"start": "2026-08-01", "end": "2026-08-01"},
    })
    assert request.profile_ids == []
    assert request.asins == []


class FakeAdvertisingLLM:
    def __init__(self, *, bid_change_percent=-15):
        self.output_models = []
        self.bid_change_percent = bid_change_percent

    def complete(self, *, system_prompt, context, output_model, max_tokens=None):
        self.output_models.append(output_model.__name__)
        payload = json.loads(context)
        if output_model.__name__ == "InspectionHypothesisLLMOutput":
            anomaly = payload["anomalies"][0]
            return output_model.model_validate(
                {
                    "hypotheses": [
                        {
                            "anomaly_id": anomaly["anomaly_id"],
                            "category": "conversion",
                            "statement": "需要验证高消耗点击是否来自无转化搜索词或投放目标。",
                            "priority": 1,
                            "required_tools": ["ad_campaign_search_term_report"],
                        }
                    ]
                }
            )
        if output_model.__name__ == "AttributionLLMOutput":
            anomaly = next((item for item in payload["anomalies"] if item["verified_findings"]), None)
            if anomaly is None:
                return output_model.model_validate({
                    "candidate_findings": [],
                    "missing_evidence": ["缺少直接明细事实"],
                    "suggested_follow_up_tools": ["ad_campaign_search_term_report"],
                    "needs_more_evidence": True,
                })
            finding = anomaly["verified_findings"][0]
            return output_model.model_validate(
                {
                    "decisions": [
                        {
                            "anomaly_id": anomaly["anomaly_id"],
                            "category": "conversion",
                            "statement": "搜索词“unrelated door seal”花费高且没有订单。",
                            "confidence": 0.88,
                            "evidence_refs": finding["evidence_refs"],
                            "finding_ids": [finding["finding_id"]],
                            "missing_evidence": [],
                        }
                    ],
                    "needs_more_evidence": False,
                }
            )
        cause = payload["causes"][0]
        return output_model.model_validate(
            {
                "decisions": [
                    {
                        "cause_id": cause["cause_id"],
                        "action_type": "adjust_bid",
                        "title": "降低异常活动竞价",
                        "rationale": "成交效率下降且消耗上升，需要控制点击成本。",
                        "proposed_change": {
                            "bid_change_percent": self.bid_change_percent
                        },
                        "expected_effect": "降低无效消耗。",
                        "evidence_refs": [cause["allowed_evidence_refs"][0]],
                    }
                ]
            }
        )

class StubChatManager:
    roles = None

    def health(self):
        return {"status": "ok"}

    def get(self, run_id, *, owner_id=None):
        return None


def request_payload():
    return {
        "profile_ids": ["profile-1"],
        "current_period": {"start": "2026-08-18", "end": "2026-08-24"},
        "baseline_period": {"start": "2026-08-11", "end": "2026-08-17"},
        "goal": {"growth_priority": "balanced"},
        "trigger": "manual",
    }


def test_inspection_hypothesis_normalizes_sales_to_conversion():
    result = InspectionHypothesisLLMOutput.model_validate(
        {
            "hypotheses": [
                {
                    "anomaly_id": "anomaly-1",
                    "category": "sales",
                    "statement": "需要验证销售额下降是否由无转化流量导致。",
                    "priority": 1,
                    "required_tools": ["ad_campaign_search_term_report"],
                }
            ]
        }
    )

    assert result.hypotheses[0].category == "conversion"


def test_invalid_inspection_llm_output_falls_back_to_rule_hypotheses():
    class InvalidInspectionLLM:
        def complete(self, **_kwargs):
            raise LLMError(
                "DeepSeek returned an invalid structured response",
                code="DEEPSEEK_INVALID_OUTPUT",
            )

    request = AdDiagnosticRequest.model_validate(request_payload())
    result = DeepSeekDataInspectionAgent(
        FakeAdvertisingGateway(), InvalidInspectionLLM()
    ).invoke(
        request,
        {"trace_id": "trace-test", "span_id": "span-test", "stage": "data_inspection"},
    )

    assert result.hypotheses
    assert "本批异常已改用规则化假设继续归因" in result.warnings[0]


def test_detail_batches_keep_per_tool_budget_and_skip_prior_queries():
    searches = [f"campaign-{index}" for index in range(30)]
    batches = EvidenceBasedProblemAttributionAgent._detail_batches(
        {"ad_campaign_search_term_report": searches},
        {},
        {"ad_campaign_targeting_report": searches},
        set(),
    )

    assert len([item for item in batches if item[0] == "ad_campaign_search_term_report"]) == 30
    assert len([item for item in batches if item[0] == "ad_campaign_targeting_report"]) == 6

    repeated = EvidenceBasedProblemAttributionAgent._detail_batches(
        {"ad_campaign_search_term_report": searches[:2]},
        {},
        {"ad_campaign_targeting_report": searches[:2]},
        {("ad_campaign_search_term_report", "campaign-0")},
    )
    assert repeated[0] == ("ad_campaign_search_term_report", ["campaign-1"])


def test_current_period_mode_uses_absolute_acos_and_ctr_guardrails():
    class CurrentOnlyGateway(FakeAdvertisingGateway):
        def campaign_report(self, *, profile_ids, period, campaign_ids=None):
            return AdvertisingReport(
                tool="ad_campaign_report",
                arguments={"profile_ids": profile_ids},
                rows=[
                    {
                        "profile_id": "profile-1",
                        "campaign_id": "campaign-1",
                        "impressions": 1000,
                        "clicks": 2,
                        "spends": 60,
                        "sales": 100,
                        "orders": 1,
                    }
                ],
                total=1,
                trace=trace("ad_campaign_report"),
            )

    request = AdDiagnosticRequest.model_validate(
        {
            **request_payload(),
            "baseline_period": None,
            "goal": {"growth_priority": "balanced"},
        }
    )
    result = RuleBasedDataInspectionAgent(CurrentOnlyGateway()).invoke(
        request,
        {"trace_id": "trace-test", "span_id": "span-test", "stage": "data_inspection"},
    )

    assert {item.anomaly_type.value for item in result.anomalies} == {
        "acos_rise",
        "ctr_drop",
    }


def wait_for_run(manager: AdvertisingRunManager, run_id: str, owner_id: str | None = None):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        record = manager.get(run_id, owner_id=owner_id)
        if record and record.status != "running":
            return record
        sleep(0.01)
    raise AssertionError("advertising run did not finish")


def test_advertising_runtime_creates_evidence_backed_todos():
    llm = FakeAdvertisingLLM()
    manager = AdvertisingRunManager(gateway=FakeAdvertisingGateway(), llm=llm)
    request = AdDiagnosticRequest.model_validate(request_payload())

    run_id = manager.submit(request)
    record = wait_for_run(manager, run_id)

    assert record.status == "completed"
    assert record.result is not None
    assert record.trace_id.startswith("trace-")
    assert record.result.trace_id == record.trace_id
    assert record.result.stage == "review_todo"
    assert record.result.anomalies
    assert record.result.todos
    assert all(todo.approval_required for todo in record.result.todos)
    assert all(todo.description.startswith("原因摘要：") for todo in record.result.todos)
    assert all(len(todo.description) <= 96 for todo in record.result.todos)
    assert all(todo.detail is not None for todo in record.result.todos)
    assert all(len(todo.detail.attribution) <= 400 for todo in record.result.todos if todo.detail)
    assert all(
        "主因：" in todo.detail.attribution
        for todo in record.result.todos
        if todo.detail
    )
    assert len(record.result.evidence) >= 3
    assert all(item.trace_id == record.trace_id for item in record.result.evidence)
    assert {item.stage for item in record.result.evidence} == {
        "data_inspection",
        "problem_attribution",
    }
    assert llm.output_models == [
        "InspectionHypothesisLLMOutput",
        "AttributionLLMOutput",
        "StrategyLLMOutput",
    ]
    events = manager.hub.events_after(run_id)
    assert events[0].event == "run.started"
    assert events[-1].event == "run.completed"
    assert all(event.trace_id == record.trace_id for event in events)
    agent_spans = {
        event.span_id
        for event in events
        if event.event == "stage.started" and event.span_id is not None
    }
    assert len(agent_spans) == 4


def test_call_ledger_request_normalizes_diagnostic_period_for_jsonb():
    request = AdDiagnosticRequest.model_validate(request_payload())
    encoded = _LedgerGateway._json_value({"period": request.current_period})

    assert encoded == {"period": {"start": "2026-08-18", "end": "2026-08-24"}}
    assert json.dumps(encoded)


def test_invalid_inspection_hypothesis_output_falls_back_to_rule_based_hypotheses():
    class InvalidInspectionLLM(FakeAdvertisingLLM):
        def complete(self, *, output_model, **kwargs):
            if output_model.__name__ == "InspectionHypothesisLLMOutput":
                raise LLMError("invalid", code="DEEPSEEK_INVALID_OUTPUT")
            return super().complete(output_model=output_model, **kwargs)

    manager = AdvertisingRunManager(gateway=FakeAdvertisingGateway(), llm=InvalidInspectionLLM())
    run_id = manager.submit(AdDiagnosticRequest.model_validate(request_payload()))
    record = wait_for_run(manager, run_id)

    assert record.status == "completed"
    assert record.result is not None
    assert any("规则化假设" in warning for warning in record.result.warnings)


def test_todo_detail_keeps_sparse_evidence_short_and_explicit():
    from amazon_ops.advertising.agents import ApprovalRequiredReviewTodoAgent

    narrative = ApprovalRequiredReviewTodoAgent._fit_detail_parts_to_limit(
        ["异常情况：ACOS 异常。", "证据说明：当前仍待补充搜索词明细，以下仅作为待验证方向。"],
        400,
    )

    assert len(narrative) < 300
    assert "待补充搜索词明细" in narrative
    assert "异常情况：ACOS 异常。" in narrative


def test_advertising_api_lists_shops_and_runs_workflow():
    manager = AdvertisingRunManager(
        gateway=FakeAdvertisingGateway(), llm=FakeAdvertisingLLM()
    )
    class StubAuthStore:
        def user_for_token(self, token):
            return AuthUser("test-user", "operator@example.com", "operator", True) if token == "test-token" else None
        def close(self):
            return None

    client = TestClient(
        create_app(
            StubChatManager(),
            advertising_manager=manager,
            idempotency_registry=InMemoryIdempotencyRegistry(),
            auth_store=StubAuthStore(),
        )
        , headers={"Authorization": "Bearer test-token"}
    )

    shops = client.get("/api/ad-diagnostics/shops")
    headers = {"Idempotency-Key": "advertising-test-0001"}
    created = client.post(
        "/api/ad-diagnostics/runs", json=request_payload(), headers=headers
    )
    replay = client.post(
        "/api/ad-diagnostics/runs", json=request_payload(), headers=headers
    )
    record = wait_for_run(manager, created.json()["run_id"], owner_id="test-user")

    assert shops.status_code == 200
    assert shops.json()[0]["alias"] == "测试店铺"
    assert created.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["run_id"] == created.json()["run_id"]
    assert replay.json()["trace_id"] == created.json()["trace_id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert record.result is not None
    fetched = client.get(f"/api/ad-diagnostics/runs/{record.run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["result"]["todos"]

    deleted = client.delete(f"/api/ad-diagnostics/history/{record.run_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/ad-diagnostics/runs/{record.run_id}").status_code == 404
    assert client.get("/api/ad-diagnostics/history").json() == []


def test_running_advertising_history_cannot_be_deleted():
    class SlowGateway(FakeAdvertisingGateway):
        def campaign_report(self, **kwargs):
            sleep(0.2)
            return super().campaign_report(**kwargs)

    manager = AdvertisingRunManager(gateway=SlowGateway(), llm=FakeAdvertisingLLM())
    run_id = manager.submit(AdDiagnosticRequest.model_validate(request_payload()), owner_id="test-user")

    try:
        manager.delete_history(run_id, owner_id="test-user")
    except ValueError as exc:
        assert "running" in str(exc)
    else:
        raise AssertionError("running diagnostic should not be deletable")


def test_deepseek_strategy_is_clamped_before_todo_creation():
    manager = AdvertisingRunManager(
        gateway=FakeAdvertisingGateway(),
        llm=FakeAdvertisingLLM(bid_change_percent=-100),
    )

    record = wait_for_run(
        manager,
        manager.submit(AdDiagnosticRequest.model_validate(request_payload())),
    )

    assert record.result is not None
    assert record.result.todos[0].proposed_change == {"bid_change_percent": -30.0}


def test_invalid_strategy_evidence_is_discarded_without_failing_the_run():
    class InvalidEvidenceLLM(FakeAdvertisingLLM):
        def complete(self, **kwargs):
            result = super().complete(**kwargs)
            if kwargs["output_model"].__name__ == "StrategyLLMOutput":
                payload = result.model_dump()
                payload["decisions"][0]["evidence_refs"] = ["not-a-verified-evidence-ref"]
                return kwargs["output_model"].model_validate(payload)
            return result

    manager = AdvertisingRunManager(gateway=FakeAdvertisingGateway(), llm=InvalidEvidenceLLM())
    record = wait_for_run(manager, manager.submit(AdDiagnosticRequest.model_validate(request_payload())))

    assert record.result is not None
    assert record.result.todos == []
    assert any("未验证证据" in warning for warning in record.result.warnings)


def _detail_inspection() -> DataInspectionResult:
    evidence = DiagnosticEvidence(
        evidence_id="inspection-evidence",
        trace_id="trace-detail",
        span_id="span-detail",
        stage="data_inspection",
        tool="ad_campaign_report",
        query={},
        fetched_at=datetime.now(timezone.utc),
        record_count=2,
    )
    anomalies = [
        DetectedAnomaly(
            anomaly_id=f"anomaly-{campaign_id}",
            anomaly_type=AnomalyType.ACOS_RISE,
            entity=AdEntityRef(
                entity_type=AdEntityType.CAMPAIGN,
                entity_id=campaign_id,
                campaign_id=campaign_id,
                profile_id="profile-1",
                name=campaign_id,
            ),
            metric="acos",
            current_value=0.9,
            severity="high",
            confidence=0.9,
            evidence_refs=[evidence.evidence_id],
        )
        for campaign_id in ("campaign-a", "campaign-b")
    ]
    return DataInspectionResult(
        anomalies=anomalies,
        evidence=[evidence],
        hypotheses=[
            InspectionHypothesis(
                hypothesis_id=f"hypothesis-{item.anomaly_id}",
                anomaly_id=item.anomaly_id,
                category="conversion",
                statement="验证高消耗无转化搜索词。",
                priority=1,
                required_tools=["ad_campaign_search_term_report"],
            )
            for item in anomalies
        ],
    )


def test_attribution_uses_distinct_detail_facts_for_each_campaign():
    class DetailGateway(FakeAdvertisingGateway):
        def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
            campaign_id = campaign_ids[0]
            term = "cheap unrelated query" if campaign_id == "campaign-a" else "wrong fit query"
            return AdvertisingReport(
                tool=tool,
                arguments={"campaign_id": campaign_id},
                rows=[{
                    "campaign_id": campaign_id,
                    "search_term": term,
                    "spends": 50 if campaign_id == "campaign-a" else 80,
                    "sales": 0,
                    "orders": 0,
                }],
                total=1,
                trace=trace(tool),
            )

    request = AdDiagnosticRequest.model_validate({**request_payload(), "baseline_period": None})
    result = EvidenceBasedProblemAttributionAgent(DetailGateway()).invoke(
        request,
        {
            "trace_id": "trace-detail",
            "span_id": "span-detail",
            "stage": "problem_attribution",
            "inspection": _detail_inspection().model_dump(mode="json"),
            "attribution_round": 0,
        },
    )

    primary = [item for item in result.causes if item.role == "primary"]
    assert all(item.verified for item in primary)
    assert "cheap unrelated query" in primary[0].statement
    assert "wrong fit query" in primary[1].statement
    assert all("升至" not in item.statement and "降至" not in item.statement for item in primary)
    assert set(result.report_summaries) == {
        "ad_campaign_search_term_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_group_report",
    }
    search_summary = result.report_summaries["ad_campaign_search_term_report"]
    assert {item.campaign_id for item in search_summary.campaign_anomalies} == {"campaign-a", "campaign-b"}
    assert {finding.object_ref for item in search_summary.campaign_anomalies for finding in item.findings} == {
        "cheap unrelated query", "wrong fit query"
    }
    assert all(item.status == "finding_detected" for item in search_summary.campaign_anomalies)
    assert result.report_summaries["ad_campaign_group_report"].campaign_anomalies


def test_attribution_marks_missing_detail_as_insufficient_not_generic_cause():
    class EmptyDetailGateway(FakeAdvertisingGateway):
        def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
            return AdvertisingReport(
                tool=tool,
                arguments={"campaign_id": campaign_ids[0]},
                rows=[{"campaign_id": campaign_ids[0]}],
                total=1,
                trace=trace(tool),
            )

    request = AdDiagnosticRequest.model_validate({**request_payload(), "baseline_period": None})
    result = EvidenceBasedProblemAttributionAgent(EmptyDetailGateway()).invoke(
        request,
        {
            "trace_id": "trace-detail",
            "span_id": "span-detail",
            "stage": "problem_attribution",
            "inspection": _detail_inspection().model_dump(mode="json"),
            "attribution_round": 0,
        },
    )

    assert not any(item.verified for item in result.causes)
    assert all("暂不输出归因结论" in item.statement for item in result.causes)
    assert result.needs_more_evidence is True


def test_detail_quotas_are_independent_per_report_and_round():
    class CountingGateway(FakeAdvertisingGateway):
        def __init__(self):
            self.calls = []

        def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
            self.calls.append((tool, tuple(campaign_ids), period.start))
            return AdvertisingReport(
                tool=tool,
                arguments={"campaign_id": campaign_ids},
                rows=[{"campaign_id": campaign_id} for campaign_id in campaign_ids],
                total=len(campaign_ids),
                trace=trace(tool),
            )

    anomalies = [
        DetectedAnomaly(
            anomaly_id=f"anomaly-{index}",
            anomaly_type=AnomalyType.ZERO_ORDER_WASTE,
            entity=AdEntityRef(entity_type=AdEntityType.CAMPAIGN, entity_id=f"campaign-{index}", campaign_id=f"campaign-{index}", profile_id="profile-1"),
            metric="orders",
            severity="high",
            confidence=0.9,
            evidence_refs=["inspection-evidence"],
        )
        for index in range(31)
    ]
    inspection = DataInspectionResult(
        anomalies=anomalies,
        hypotheses=[
            InspectionHypothesis(
                hypothesis_id=f"hypothesis-{item.anomaly_id}", anomaly_id=item.anomaly_id,
                category="conversion", statement="验证无转化流量", priority=1,
                required_tools=["ad_campaign_search_term_report"],
            )
            for item in anomalies
        ],
    )
    gateway = CountingGateway()
    result = EvidenceBasedProblemAttributionAgent(gateway).invoke(
        AdDiagnosticRequest.model_validate(request_payload()),
        {"trace_id": "trace", "span_id": "span", "stage": "problem_attribution", "inspection": inspection.model_dump(mode="json"), "attribution_round": 0},
    )

    quotas = {(item.round, item.tool): item for item in result.detail_call_quotas}
    assert quotas[(1, "ad_campaign_search_term_report")].used == 30
    assert all(item.used <= 30 and item.limit == 30 for item in quotas.values())
    assert sum(item.used for item in quotas.values()) <= 4 * 30
    # 31st campaign is outside the stable, fixed focus set.
    queried = {campaign_id for _, campaign_ids, _ in gateway.calls for campaign_id in campaign_ids}
    assert "campaign-30" not in queried


def test_second_round_only_fills_missing_requested_report_dimension():
    class CountingGateway(FakeAdvertisingGateway):
        def __init__(self):
            self.calls = []

        def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
            self.calls.append((tool, tuple(campaign_ids)))
            return AdvertisingReport(tool=tool, arguments={"campaign_id": campaign_ids}, rows=[], total=0, trace=trace(tool))

    inspection = _detail_inspection()
    previous = AttributionResult(
        requested_tools=["ad_campaign_targeting_report"],
        evidence=[
            DiagnosticEvidence(
                evidence_id=f"search-{item.entity.campaign_id}", trace_id="trace", span_id="span", stage="problem_attribution",
                tool="ad_campaign_search_term_report", query={"campaign_id": [item.entity.campaign_id]},
                fetched_at=datetime.now(timezone.utc), record_count=0,
            )
            for item in inspection.anomalies
        ],
    )
    gateway = CountingGateway()
    result = EvidenceBasedProblemAttributionAgent(gateway).invoke(
        AdDiagnosticRequest.model_validate({**request_payload(), "baseline_period": None}),
        {"trace_id": "trace", "span_id": "span", "stage": "problem_attribution", "inspection": inspection.model_dump(mode="json"), "attribution": previous.model_dump(mode="json"), "attribution_round": 1},
    )

    assert {tool for tool, _ in gateway.calls} == {"ad_campaign_targeting_report"}
    assert all(item.round == 2 for item in result.detail_call_quotas)


def test_attribution_keeps_low_spend_long_tail_in_normalized_facts_and_aggregates():
    class LongTailGateway(FakeAdvertisingGateway):
        def attribution_report(self, *, tool, profile_ids, period, campaign_ids):
            rows = []
            if tool == "ad_campaign_search_term_report":
                rows = [
                    {
                        "campaign_id": "campaign-a",
                        "search_term": f"long-tail-{index}",
                        "spends": 3.5,
                        "sales": 0,
                        "orders": 0,
                    }
                    for index in range(12)
                ]
            return AdvertisingReport(tool=tool, arguments={"campaign_id": campaign_ids}, rows=rows, total=len(rows), trace=trace(tool))

    result = EvidenceBasedProblemAttributionAgent(LongTailGateway()).invoke(
        AdDiagnosticRequest.model_validate({**request_payload(), "baseline_period": None}),
        {
            "trace_id": "trace-detail", "span_id": "span-detail", "stage": "problem_attribution",
            "inspection": _detail_inspection().model_dump(mode="json"), "attribution_round": 0,
        },
    )

    facts = [item for item in result.normalized_detail_facts if item.report_type == "ad_campaign_search_term_report"]
    aggregate = next(item for item in result.attribution_aggregates if item.report_type == "ad_campaign_search_term_report")
    assert len(facts) == 12
    assert all(item.spend == 3.5 and item.orders == 0 for item in facts)
    assert aggregate.total_spend == 42
    assert aggregate.long_tail == {"objects": 2, "spend": 7, "orders": 0, "sales": 0}

    from amazon_ops.advertising.llm_agents import DeepSeekProblemAttributionAgent
    from amazon_ops.advertising.llm_models import AttributionLLMOutput

    rejected = DeepSeekProblemAttributionAgent._validate_interpretations(
        result,
        AttributionLLMOutput.model_validate({
            "candidate_findings": [{
                "campaign_id": facts[0].campaign_id,
                "anomaly_id": facts[0].anomaly_id,
                "object_type": "search_term",
                "object_name": facts[0].object_name,
                "metrics": {"spend": 99},
                "evidence_refs": [facts[0].evidence_ref],
                "judgment_type": "direct",
                "confidence": 0.9,
            }]
        }),
    )
    assert rejected[0].candidate_findings == []
    assert rejected[0].rejected_candidates

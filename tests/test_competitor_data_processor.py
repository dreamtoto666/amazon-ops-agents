from __future__ import annotations

import json

import pytest

from amazon_ops.competitor_data_processor import (
    COMPETITOR_PROFILE_MAX_TOKENS,
    CompetitorDataProcessor,
    build_processor_context,
)
from amazon_ops.events import InMemoryEventHub, StageController, StageEventType, StageName
from amazon_ops.graph import build_controller_graph
from amazon_ops.models import (
    Action,
    AgentTask,
    CompetitorProfile,
    CompetitorProfileFact,
    CompetitorProfileSection,
    CompetitorProfiles,
    CompetitorDataModules,
    MultiVariantOrganicPositionModule,
    RecommendationPlacementModule,
    TrafficKeywordLookupModule,
    TrafficKeywordReverseLookupModule,
    Domain,
    QueryScope,
    RequestRoute,
    RiskLevel,
    SpecialistName,
    SpecialistResult,
    UnderstandRequestResult,
    UserIntent,
)


class StubLLM:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload, self.error, self.calls = payload, error, 0

    def complete(self, *, output_model, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return output_model.model_validate(self.payload)


class CapturingLLM(StubLLM):
    def __init__(self, payload):
        super().__init__(payload)
        self.max_tokens = None

    def complete(self, *, output_model, max_tokens=None, **_kwargs):
        self.max_tokens = max_tokens
        return super().complete(output_model=output_model)


def _scope():
    return QueryScope(marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"])


def _result(evidence=None):
    evidence = evidence or [
        {"evidence_id": "own-evidence", "query": {"asin": "B0OWN00001"}, "data": {"sp": 18.2}},
        {"evidence_id": "competitor-evidence", "query": {"asin": "B0COMP0001"}, "data": {"sp": 31.6}},
    ]
    return SpecialistResult(
        task_id="task-1",
        agent=SpecialistName.COMPETITOR_ADVERTISING,
        summary="完成",
        deliverables=[
            {
                "type": "competitor_research",
                "tools": ["analyze_traffic_structure"],
                "results": [{"ok": True, "evidence": evidence}],
            }
        ],
    )


def _profiles(*, evidence_id="competitor-evidence", source="流量结构", status="available"):
    return {
        "own_asin": "B0OWN00001",
        "marketplace": "US",
        "profiles": [
            {
                "competitor_asin": "B0COMP0001",
                "status": status,
                "source_names": [source],
                "sections": [
                    {
                        "section": "traffic_structure",
                        "status": status,
                        "source_names": [source],
                        "facts": [
                            {
                                "field": "SP 流量占比",
                                "own_value": 18.2,
                                "competitor_value": 31.6,
                                "unit": "%",
                                "comparison": "竞品高 13.4 个百分点",
                                "source_names": [source],
                                "evidence_ids": [evidence_id],
                            }
                        ],
                        "key_gaps": ["竞品 SP 流量占比更高"],
                    }
                ],
            }
        ],
    }


def test_processor_writes_compact_profile_with_chinese_source_and_evidence():
    processor = CompetitorDataProcessor(StubLLM(_profiles()))

    profiles, error = processor.process(scope=_scope(), result=_result())

    assert error is None
    assert profiles is not None
    assert profiles.profiles[0].competitor_asin == "B0COMP0001"
    assert profiles.profiles[0].sections[0].source_names == ["流量结构"]
    assert profiles.profiles[0].sections[0].facts[0].evidence_ids == ["competitor-evidence"]


def test_processor_builds_parent_only_variant_module_from_verified_sif_fields():
    evidence = [
        {
            "evidence_id": "own-variants",
            "tool": "ops_get_listing_keyword_distribution",
            "query": {"asin": "B0OWN00001"},
            "data": {"data": {"asins": [{"asin": "[B0OWN00002]", "total": 80, "naturalRatio": 0.7, "adRatio": 0.3, "spRatio": 0.2, "spRecRatio": 0.1, "brandRatio": 0.05, "vedioRatio": 0.01}]}},
        },
        {
            "evidence_id": "competitor-variants",
            "tool": "ops_get_listing_keyword_distribution",
            "query": {"asin": "B0COMP0001"},
            "data": {"data": {"asins": [{"asin": "[B0COMP0002]", "total": 20, "naturalRatio": 0.6, "adRatio": 0.4, "spRatio": 0.3, "spRecRatio": 0.1, "brandRatio": 0.05, "vedioRatio": 0.02}]}},
        },
    ]
    modules, error = CompetitorDataProcessor(StubLLM()).process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    assert modules is not None
    assert modules.own_parent_asin == "B0OWN00001"
    assert modules.competitor_parent_asin == "B0COMP0001"
    assert modules.traffic_keyword_lookup.status == "available"
    own = modules.traffic_keyword_lookup.records[0]
    assert own.parent_asin == "B0OWN00001"
    assert own.variants[0].variant_asin == "B0OWN00002"
    assert own.variants[0].total_traffic_ratio == 1.0
    assert modules.multi_variant_organic_position.status == "unavailable"
    assert modules.recommendation_placement.status == "unavailable"


def test_processor_keeps_only_keyword_traffic_share_strictly_above_one_percent():
    keyword_rows = [
        {"keyword": "below", "traffic_share": 0.0099, "natural_ratio": 0.5},
        {"keyword": "equal", "traffic_share": 0.01, "natural_ratio": 0.5},
        {"keyword": "above", "traffic_share": 0.0101, "natural_ratio": 0.6},
    ]
    evidence = [
        {"evidence_id": "own-keywords", "tool": "market_get_asin_keyword_signals", "query": {"asin": "B0OWN00001"}, "data": {"top_keywords": keyword_rows}},
        {"evidence_id": "competitor-keywords", "tool": "market_get_asin_keyword_signals", "query": {"asin": "B0COMP0001"}, "data": {"top_keywords": keyword_rows}},
    ]
    modules, error = CompetitorDataProcessor(StubLLM()).process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    assert modules is not None
    reverse = modules.traffic_keyword_reverse_lookup
    assert reverse.status == "partial"  # Sif did not identify the own rising period.
    assert [row.keyword for row in reverse.records] == ["above", "above"]
    own = reverse.records[0]
    assert own.natural_traffic_ratio == pytest.approx(0.00606)
    assert own.ad_traffic_ratio == pytest.approx(0.00404)


def test_processor_calculates_multi_organic_extra_traffic_from_raw_child_scores():
    evidence = [
        {
            "evidence_id": "own-structure",
            "tool": "ops_get_listing_traffic_structure",
            "query": {"asin": "B0OWN00001"},
            "data": {"chars": {"dims": [{"val": "[B0OWN00002]"}, {"val": "[B0OWN00003]"}]}},
        },
        {
            "evidence_id": "competitor-structure",
            "tool": "ops_get_listing_traffic_structure",
            "query": {"asin": "B0COMP0001"},
            "data": {"chars": {"dims": [{"val": "[B0COMP0002]"}, {"val": "[B0COMP0003]"}]}},
        },
        {
            "evidence_id": "own-child-one",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0OWN00002"},
            "data": {"total": 2, "details": [
                {"keyword": "garage door seal", "score": 90, "pchangeReason": {"nfInfo": {"inFre": 7, "rankAvg": 1}}},
                {"keyword": "other", "score": 900, "pchangeReason": {"nfInfo": {"inFre": 7, "rankAvg": 2}}},
            ]},
        },
        {
            "evidence_id": "own-child-two",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0OWN00003"},
            "data": {"total": 2, "details": [
                {"keyword": "garage door seal", "score": 10, "pchangeReason": {"nfInfo": {"inFre": 6, "rankAvg": 14}}},
                {"keyword": "other", "score": 0, "pchangeReason": {"nfInfo": {"inFre": 0, "rankAvg": None}}},
            ]},
        },
        {
            "evidence_id": "competitor-child-one",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0COMP0002"},
            "data": {"total": 1, "details": [{"keyword": "garage door seal", "score": 80}]},
        },
        {
            "evidence_id": "competitor-child-two",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0COMP0003"},
            "data": {"total": 1, "details": [{"keyword": "garage door seal", "score": 20}]},
        },
    ]

    modules, error = CompetitorDataProcessor(StubLLM()).process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    assert modules is not None
    module = modules.multi_variant_organic_position
    assert module.status == "available"
    own = next(row for row in module.records if row.parent_asin == "B0OWN00001" and row.keyword == "garage door seal")
    assert own.natural_traffic == 100
    assert own.natural_traffic_ratio == pytest.approx(0.1)
    assert own.multi_organic_extra_natural_traffic == 10
    assert [variant.natural_traffic_ratio for variant in own.variants] == [pytest.approx(0.9), pytest.approx(0.1)]
    assert own.variants[0].natural_position_days == 7
    assert own.variants[1].average_natural_rank == 14


def test_processor_does_not_calculate_multi_organic_share_from_incomplete_detail_page():
    evidence = [
        {"evidence_id": "own-structure", "tool": "ops_get_listing_traffic_structure", "query": {"asin": "B0OWN00001"}, "data": {"chars": {"dims": [{"val": "[B0OWN00002]"}]}}},
        {"evidence_id": "competitor-structure", "tool": "ops_get_listing_traffic_structure", "query": {"asin": "B0COMP0001"}, "data": {"chars": {"dims": [{"val": "[B0COMP0002]"}]}}},
        {"evidence_id": "own-detail", "tool": "ops_get_asin_traffic_trend_detail", "query": {"asin": "B0OWN00002"}, "data": {"total": 201, "details": [{"keyword": "incomplete", "score": 1}]}},
        {"evidence_id": "competitor-detail", "tool": "ops_get_asin_traffic_trend_detail", "query": {"asin": "B0COMP0002"}, "data": {"total": 1, "details": [{"keyword": "complete", "score": 1}]}},
    ]

    modules, _ = CompetitorDataProcessor(StubLLM()).process_modules(scope=_scope(), result=_result(evidence))

    assert modules is not None
    assert modules.multi_variant_organic_position.status == "partial"
    assert not any(row.parent_asin == "B0OWN00001" for row in modules.multi_variant_organic_position.records)
    assert any("未覆盖完整关键词页" in reason for reason in modules.multi_variant_organic_position.missing_reasons)


def test_processor_uses_expanded_profile_output_budget():
    llm = CapturingLLM(_profiles())

    profiles, error = CompetitorDataProcessor(llm).process(
        scope=_scope(), result=_result()
    )

    assert error is None
    assert profiles is not None
    assert llm.max_tokens == COMPETITOR_PROFILE_MAX_TOKENS == 16_000


def test_processor_keeps_every_evidence_item_without_sampling():
    """There is no evidence cap: the only business filter is the >1% rule."""
    evidence = [
        {"evidence_id": f"evidence-{index}", "query": {"asin": "B0COMP0001"}, "data": {"value": index}}
        for index in range(30)
    ]

    context, evidence_ids, _source_names, truncated_sources = build_processor_context(_scope(), _result(evidence))

    assert len(evidence_ids) == 30
    assert truncated_sources == set()
    assert len(json.loads(context)["evidence"]) == 30


def test_parent_incomplete_detail_page_does_not_block_multi_variant_module():
    """The parent Listing is listed in ``chars.dims`` with ``isVariant: false``.

    Its own detail page is far larger than one page, so counting it as a variant
    used to mark the whole module unavailable even though every child page was
    complete.
    """

    evidence = [
        {
            "evidence_id": "own-structure",
            "tool": "ops_get_listing_traffic_structure",
            "query": {"asin": "B0OWN00001"},
            "data": {"chars": {"dims": [
                {"val": "[B0OWN00001]", "isVariant": False},
                {"val": "[B0OWN00002]", "isVariant": True},
            ]}},
        },
        {
            "evidence_id": "competitor-structure",
            "tool": "ops_get_listing_traffic_structure",
            "query": {"asin": "B0COMP0001"},
            "data": {"chars": {"dims": [{"val": "[B0COMP0002]", "isVariant": True}]}},
        },
        {
            "evidence_id": "own-parent-page",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0OWN00001"},
            "data": {"total": 400, "details": [{"keyword": "garage door seal", "score": 5}]},
        },
        {
            "evidence_id": "own-child-page",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0OWN00002"},
            "data": {"total": 1, "details": [{"keyword": "garage door seal", "score": 95}]},
        },
        {
            "evidence_id": "competitor-child-page",
            "tool": "ops_get_asin_traffic_trend_detail",
            "query": {"asin": "B0COMP0002"},
            "data": {"total": 1, "details": [{"keyword": "garage door seal", "score": 80}]},
        },
    ]

    modules, error = CompetitorDataProcessor(StubLLM()).process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    module = modules.multi_variant_organic_position
    assert module.status == "available"
    own = next(row for row in module.records if row.parent_asin == "B0OWN00001")
    assert own.natural_traffic == 95  # 父体自己那页不参与变体分母


def test_processor_retries_three_times_and_keeps_query_success_separate():
    llm = StubLLM(error=RuntimeError("provider unavailable"))
    processor = CompetitorDataProcessor(llm)

    profiles, error = processor.process(scope=_scope(), result=_result())

    assert profiles is None
    assert llm.calls == 3
    assert error == {"code": "COMPETITOR_PROCESSING_FAILED", "attempts": 3, "last_error": "RuntimeError"}


def test_processor_rejects_unknown_evidence_and_private_metric_instead_of_writing_profile():
    invalid = _profiles(evidence_id="invented-evidence")
    invalid["profiles"][0]["sections"][0]["facts"][0]["field"] = "ACOS"
    llm = StubLLM(invalid)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert profiles is None
    assert llm.calls == 3
    assert error and error["code"] == "COMPETITOR_PROCESSING_FAILED"


class FakeInterpreter:
    def invoke(self, _state):
        return UnderstandRequestResult(
            intent=UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=1),
            scope=_scope(),
            route=RequestRoute.EXECUTE,
            risk_level=RiskLevel.READ_ONLY,
            normalized_request="对比竞品",
        )


class FakeCompetitorSpecialist:
    name = SpecialistName.COMPETITOR_ADVERTISING.value

    def invoke(self, _task: AgentTask, _scope: QueryScope, _state):
        return _result()


class FixedProcessor:
    def process_modules(self, *, scope, result):
        return CompetitorDataModules(
            own_parent_asin=scope.own_asin,
            competitor_parent_asin=scope.competitor_asins[0],
            marketplace=scope.marketplaces[0],
            traffic_keyword_lookup=TrafficKeywordLookupModule(status="available"),
            traffic_keyword_reverse_lookup=TrafficKeywordReverseLookupModule(status="unavailable"),
            multi_variant_organic_position=MultiVariantOrganicPositionModule(status="unavailable"),
            recommendation_placement=RecommendationPlacementModule(status="unavailable"),
        ), None


def test_graph_writes_data_modules_to_internal_state_before_aggregation():
    graph = build_controller_graph(
        interpreter=FakeInterpreter(),
        specialists={SpecialistName.COMPETITOR_ADVERTISING.value: FakeCompetitorSpecialist()},
        competitor_data_processor=FixedProcessor(),
    )

    state = graph.invoke({"messages": []})

    assert state["competitor_data_modules"]["competitor_parent_asin"] == "B0COMP0001"
    assert state["competitor_processing_errors"] == []


def test_graph_emits_data_processing_progress_for_competitor_profile():
    hub = InMemoryEventHub()
    graph = build_controller_graph(
        interpreter=FakeInterpreter(),
        specialists={SpecialistName.COMPETITOR_ADVERTISING.value: FakeCompetitorSpecialist()},
        competitor_data_processor=FixedProcessor(),
        stages=StageController(hub),
    )

    graph.invoke({"messages": [], "request_id": "competitor-processing-run"})

    events = hub.events_after("competitor-processing-run")
    assert StageName.DATA_PROCESSING in [item.stage for item in events if item.event == StageEventType.STAGE_STARTED]
    assert any(
        item.data.get("kind") == "competitor.processing.completed"
        for item in events
        if item.event == StageEventType.STAGE_PROGRESS
    )


# --- 私有指标：区分「断言」与「如实说明不可得」 ------------------------------

def _profiles_with(*, key_gaps=(), limitations=(), section_limitations=(), facts=None):
    payload = _profiles()
    section = payload["profiles"][0]["sections"][0]
    section["key_gaps"] = list(key_gaps)
    section["limitations"] = list(section_limitations)
    payload["profiles"][0]["limitations"] = list(limitations)
    if facts is not None:
        section["facts"] = facts
    return payload


def test_unavailability_notes_survive_validation():
    """要求模型说明不可得，就不能把这句话判成违规。"""

    payload = _profiles_with(
        key_gaps=["竞品 ACOS 不可得", "竞品订单数据未返回"],
        limitations=["竞品花费未披露，无法对比"],
    )
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert error is None
    assert llm.calls == 1
    section = profiles.profiles[0].sections[0]
    assert section.key_gaps == ["竞品 ACOS 不可得", "竞品订单数据未返回"]
    assert profiles.profiles[0].limitations == ["竞品花费未披露，无法对比"]


def test_asserted_private_metrics_are_dropped_from_every_text_field():
    """断言式的私有指标不能留在任何字段里，包括之前漏检的 limitations。"""

    payload = _profiles_with(
        key_gaps=["竞品 ACOS 约 12%"],
        limitations=["竞品 ROAS 为 3.1"],
        section_limitations=["竞品花费约 2000 美元"],
    )
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert error is None
    assert llm.calls == 1
    section = profiles.profiles[0].sections[0]
    assert section.key_gaps == []
    assert section.limitations == []
    assert profiles.profiles[0].limitations == []
    # 合规的事实仍然保留
    assert [fact.field for fact in section.facts] == ["SP 流量占比"]


def test_a_fact_asserting_a_private_metric_is_dropped_and_the_section_downgrades():
    payload = _profiles_with(
        facts=[
            {
                "field": "ACOS",
                "own_value": 18.2,
                "competitor_value": 31.6,
                "unit": "%",
                "comparison": "竞品 ACOS 更高",
                "source_names": ["流量结构"],
                "evidence_ids": ["competitor-evidence"],
            }
        ]
    )
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert error is None
    section = profiles.profiles[0].sections[0]
    assert section.facts == []
    # 没有可用事实时不能继续声称该主题 available
    assert section.status == "unavailable"


def test_fabricated_provenance_still_fails_hard():
    """来源/证据造假属于完整性问题，必须整次失败而不是裁剪。"""

    payload = _profiles(evidence_id="invented-evidence")
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert profiles is None
    assert llm.calls == 3
    assert error and error["code"] == "COMPETITOR_PROCESSING_FAILED"


def test_an_unavailability_marker_does_not_excuse_a_stated_value():
    """「不可得」不能成为同一句里给出估算值的通行证。"""

    payload = _profiles_with(key_gaps=["竞品 ACOS 不可得，但估计约 12%"])
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert error is None
    assert profiles.profiles[0].sections[0].key_gaps == []


def test_naming_a_private_metric_without_a_value_is_still_not_a_gap():
    payload = _profiles_with(key_gaps=["竞品 ACOS"])
    llm = StubLLM(payload)

    profiles, error = CompetitorDataProcessor(llm).process(scope=_scope(), result=_result())

    assert error is None
    assert profiles.profiles[0].sections[0].key_gaps == []

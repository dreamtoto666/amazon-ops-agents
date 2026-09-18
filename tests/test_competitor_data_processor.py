from __future__ import annotations

import pytest

from amazon_ops.competitor_data_processor import CompetitorDataProcessor
from amazon_ops.events import InMemoryEventHub, StageController, StageEventType, StageName
from amazon_ops.graph import build_controller_graph
from amazon_ops.models import (
    Action,
    AgentTask,
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
    modules, error = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

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


def test_processor_preserves_parent_and_ad_channel_scores_and_ratios():
    distribution = {"asins": [{"asin": "[B0OWN00002]", "total": 1}]}
    overview = {
        "overview": {
            "nf": {"score": 10295.753978, "ratio": 0.8288},
            "ad": {"score": 2126.13967946, "ratio": 0.1712},
        },
        "ad": {
            "sp": {"score": 1000.4, "ratio": 0.47},
            "recommend": {"score": 500.5, "ratio": 0.24},
            "sb": {"score": 400.6, "ratio": 0.19},
            "sbv": {"score": 225.7, "ratio": 0.10},
        },
    }
    evidence = [
        {"evidence_id": "own-variants", "tool": "ops_get_listing_keyword_distribution", "query": {"asin": "B0OWN00001"}, "data": distribution},
        {"evidence_id": "competitor-variants", "tool": "ops_get_listing_keyword_distribution", "query": {"asin": "B0COMP0001"}, "data": {"asins": [{"asin": "[B0COMP0002]", "total": 1}]}},
        {"evidence_id": "own-overview", "tool": "ops_get_listing_traffic_overview", "query": {"asin": "B0OWN00001"}, "data": overview},
        {"evidence_id": "competitor-overview", "tool": "ops_get_listing_traffic_overview", "query": {"asin": "B0COMP0001"}, "data": overview},
    ]

    modules, error = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    assert modules is not None
    own = modules.traffic_keyword_lookup.records[0]
    assert own.listing_natural_traffic.score == pytest.approx(10295.753978)
    assert own.listing_natural_traffic.ratio == pytest.approx(0.8288)
    assert own.listing_ad_traffic.score == pytest.approx(2126.13967946)
    assert own.advertising_traffic_distribution.sp_recommend.score == pytest.approx(500.5)
    assert own.advertising_traffic_distribution.sbv.ratio == pytest.approx(0.10)


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
    modules, error = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

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

    modules, error = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

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

    modules, _ = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

    assert modules is not None
    assert modules.multi_variant_organic_position.status == "partial"
    assert not any(row.parent_asin == "B0OWN00001" for row in modules.multi_variant_organic_position.records)
    assert any("未覆盖完整关键词页" in reason for reason in modules.multi_variant_organic_position.missing_reasons)


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

    modules, error = CompetitorDataProcessor().process_modules(scope=_scope(), result=_result(evidence))

    assert error is None
    module = modules.multi_variant_organic_position
    assert module.status == "available"
    own = next(row for row in module.records if row.parent_asin == "B0OWN00001")
    assert own.natural_traffic == 95  # 父体自己那页不参与变体分母


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

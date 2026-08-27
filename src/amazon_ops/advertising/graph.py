from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from langgraph.graph import END, START, StateGraph

from .models import (
    AdDiagnosticRequest,
    AdDiagnosticResult,
    AttributionResult,
    DataInspectionResult,
    ReviewTodoResult,
    StrategyResult,
)
from .state import AdvertisingDiagnosticState


MAX_ATTRIBUTION_ROUNDS = 2


class DataInspectionAgent(Protocol):
    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> DataInspectionResult: ...


class ProblemAttributionAgent(Protocol):
    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> AttributionResult: ...


class StrategyRecommendationAgent(Protocol):
    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> StrategyResult: ...


class ReviewTodoAgent(Protocol):
    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> ReviewTodoResult: ...


@dataclass(frozen=True)
class AdvertisingDiagnosticServices:
    inspector: DataInspectionAgent
    attribution: ProblemAttributionAgent
    strategist: StrategyRecommendationAgent
    reviewer: ReviewTodoAgent
    checkpoint: Callable[[AdvertisingDiagnosticState, str, dict], None] = lambda state, key, update: None


def build_advertising_diagnostic_graph(*, services: AdvertisingDiagnosticServices):
    def trace_update(state: AdvertisingDiagnosticState) -> dict:
        return {
            "trace_id": state["trace_id"],
            "run_id": state["run_id"],
            "span_id": state["span_id"],
            "stage": state["stage"],
        }

    def inspect_data(state: AdvertisingDiagnosticState) -> dict:
        request = AdDiagnosticRequest.model_validate(state["request"])
        result = services.inspector.invoke(request, state)
        update = {
            "inspection": result.model_dump(mode="json"),
            "attribution_round": 0,
            **trace_update(state),
        }
        services.checkpoint(state, "data_inspection", update)
        return update

    def after_inspection(state: AdvertisingDiagnosticState) -> str:
        inspection = DataInspectionResult.model_validate(state["inspection"])
        return "attribute" if inspection.anomalies else "review"

    def attribute_problem(state: AdvertisingDiagnosticState) -> dict:
        request = AdDiagnosticRequest.model_validate(state["request"])
        result = services.attribution.invoke(request, state)
        update = {
            "attribution": result.model_dump(mode="json"),
            "normalized_detail_facts": [item.model_dump(mode="json") for item in result.normalized_detail_facts],
            "attribution_aggregates": [item.model_dump(mode="json") for item in result.attribution_aggregates],
            "llm_interpretations": [item.model_dump(mode="json") for item in result.llm_interpretations],
            "validated_findings": [item.model_dump(mode="json") for item in result.validated_findings],
            "attribution_round": state.get("attribution_round", 0) + 1,
            "detail_call_quotas": [item.model_dump(mode="json") for item in result.detail_call_quotas],
            "search_term_summary": result.report_summaries.get(
                "ad_campaign_search_term_report", {}
            ).model_dump(mode="json") if "ad_campaign_search_term_report" in result.report_summaries else {},
            "keyword_summary": result.report_summaries.get(
                "ad_campaign_keyword_report", {}
            ).model_dump(mode="json") if "ad_campaign_keyword_report" in result.report_summaries else {},
            "targeting_summary": result.report_summaries.get(
                "ad_campaign_targeting_report", {}
            ).model_dump(mode="json") if "ad_campaign_targeting_report" in result.report_summaries else {},
            "ad_group_summary": result.report_summaries.get(
                "ad_campaign_group_report", {}
            ).model_dump(mode="json") if "ad_campaign_group_report" in result.report_summaries else {},
            **trace_update(state),
        }
        services.checkpoint(state, f"problem_attribution:{update['attribution_round']}", update)
        return update

    def after_attribution(state: AdvertisingDiagnosticState) -> str:
        result = AttributionResult.model_validate(state["attribution"])
        if result.needs_more_evidence and state.get("attribution_round", 0) < MAX_ATTRIBUTION_ROUNDS:
            return "attribute"
        return "strategy"

    def recommend_strategy(state: AdvertisingDiagnosticState) -> dict:
        request = AdDiagnosticRequest.model_validate(state["request"])
        result = services.strategist.invoke(request, state)
        update = {"strategy": result.model_dump(mode="json"), **trace_update(state)}
        services.checkpoint(state, "strategy_recommendation", update)
        return update

    def review_and_create_todos(state: AdvertisingDiagnosticState) -> dict:
        request = AdDiagnosticRequest.model_validate(state["request"])
        result = services.reviewer.invoke(request, state)
        inspection = DataInspectionResult.model_validate(state["inspection"])
        attribution = AttributionResult.model_validate(state.get("attribution", {}))
        strategy = StrategyResult.model_validate(state.get("strategy", {}))
        warnings = [
            *inspection.warnings,
            *attribution.warnings,
            *strategy.warnings,
            *result.warnings,
        ]
        if not inspection.anomalies:
            status = "no_anomaly"
            summary = "本次巡检未发现达到阈值的广告异常。"
        elif result.todos:
            status = "completed"
            summary = f"发现 {len(inspection.anomalies)} 个异常，生成 {len(result.todos)} 个运营代办。"
        else:
            status = "needs_review"
            summary = "检测到广告异常，但建议未通过复核，需要人工检查。"
        final = AdDiagnosticResult(
            trace_id=str(state["trace_id"]),
            run_id=str(state["run_id"]),
            span_id=str(state["span_id"]),
            stage=str(state["stage"]),
            status=status,
            summary=summary,
            anomalies=inspection.anomalies,
            causes=attribution.causes,
            recommendations=strategy.recommendations,
            todos=result.todos,
            evidence=[*inspection.evidence, *attribution.evidence],
            llm_interpretations=attribution.llm_interpretations,
            warnings=warnings,
        )
        update = {
            "review": result.model_dump(mode="json"),
            "final_result": final.model_dump(mode="json"),
            **trace_update(state),
        }
        services.checkpoint(state, "review_todo", update)
        return update

    graph = StateGraph(AdvertisingDiagnosticState)
    graph.add_node("inspect_data", inspect_data)
    graph.add_node("attribute_problem", attribute_problem)
    graph.add_node("recommend_strategy", recommend_strategy)
    graph.add_node("review_and_create_todos", review_and_create_todos)
    def resume_from_checkpoint(state: AdvertisingDiagnosticState) -> str:
        return state.get("resume_next", "inspect_data")

    graph.add_conditional_edges(START, resume_from_checkpoint, {
        "inspect_data": "inspect_data", "attribute_problem": "attribute_problem",
        "recommend_strategy": "recommend_strategy", "review_and_create_todos": "review_and_create_todos",
    })
    graph.add_conditional_edges(
        "inspect_data",
        after_inspection,
        {"attribute": "attribute_problem", "review": "review_and_create_todos"},
    )
    graph.add_conditional_edges(
        "attribute_problem",
        after_attribution,
        {"attribute": "attribute_problem", "strategy": "recommend_strategy"},
    )
    graph.add_edge("recommend_strategy", "review_and_create_todos")
    graph.add_edge("review_and_create_todos", END)
    return graph.compile()

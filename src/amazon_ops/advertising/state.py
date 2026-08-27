from __future__ import annotations

from typing import Any, TypedDict


class AdvertisingDiagnosticState(TypedDict, total=False):
    request_id: str
    trace_id: str
    run_id: str
    span_id: str
    stage: str
    request: dict[str, Any]
    inspection: dict[str, Any]
    attribution: dict[str, Any]
    normalized_detail_facts: list[dict[str, Any]]
    attribution_aggregates: list[dict[str, Any]]
    llm_interpretations: list[dict[str, Any]]
    validated_findings: list[dict[str, Any]]
    strategy: dict[str, Any]
    review: dict[str, Any]
    attribution_round: int
    detail_call_quotas: list[dict[str, Any]]
    search_term_summary: dict[str, Any]
    keyword_summary: dict[str, Any]
    targeting_summary: dict[str, Any]
    ad_group_summary: dict[str, Any]
    final_result: dict[str, Any]
    resume_next: str

from __future__ import annotations

from typing import Any, TypedDict


class ListingAgentState(TypedDict, total=False):
    request_id: str
    request: dict[str, Any]
    validated_request: dict[str, Any]
    missing_fields: list[str]
    clarification_question: str | None

    new_keyword_evidence: list[dict[str, Any]]
    keyword_evidence: list[dict[str, Any]]
    selected_keywords: list[dict[str, Any]]
    excluded_keywords: list[dict[str, Any]]
    valid_keyword_count: int
    keyword_gaps: dict[str, int]
    seen_keywords: list[str]
    executed_query_fingerprints: list[str]

    supplement_search_round: int
    max_supplement_search_rounds: int
    research_status: str

    draft: dict[str, Any]
    validation: dict[str, Any]
    revision_round: int
    max_revision_rounds: int

    artifacts: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    final_result: dict[str, Any]

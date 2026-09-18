from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AmazonOpsState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    conversation_history: list[dict[str, Any]]
    current_user_message: str
    request_id: str
    owner_id: str
    user_context: dict[str, Any]
    shop_directory: list[dict[str, Any]]
    system_capabilities: dict[str, Any]
    current_time: str
    image_attachments: list[str]
    team_knowledge: list[dict[str, Any]]

    understanding: dict[str, Any]
    intent: dict[str, Any]
    scope: dict[str, Any]
    route: str
    answer_source: str
    risk_level: str
    missing_fields: list[str]
    clarification_question: str | None

    plan: dict[str, Any]
    pending_tasks: list[dict[str, Any]]
    round: int
    called_agents: Annotated[list[str], operator.add]

    specialist_results: Annotated[list[dict[str, Any]], operator.add]
    data_artifacts: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    audit_events: Annotated[list[dict[str, Any]], operator.add]
    competitor_data_modules: dict[str, Any]
    competitor_processing_errors: Annotated[list[dict[str, Any]], operator.add]
    competitor_report_sections: list[dict[str, Any]]
    competitor_report: dict[str, Any]

    final_response: dict[str, Any]
    resume_next: str

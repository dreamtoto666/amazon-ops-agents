from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AmazonOpsState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    request_id: str
    user_context: dict[str, Any]
    shop_directory: list[dict[str, Any]]
    system_capabilities: dict[str, Any]
    current_time: str

    understanding: dict[str, Any]
    intent: dict[str, Any]
    scope: dict[str, Any]
    route: str
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

    final_response: dict[str, Any]

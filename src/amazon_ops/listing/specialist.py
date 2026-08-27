from __future__ import annotations

import json
from typing import Any

from amazon_ops.interfaces import SpecialistAgent
from amazon_ops.models import (
    AgentTask,
    DataArtifact,
    Finding,
    QueryScope,
    SpecialistName,
    SpecialistResult,
)

from .graph import ListingWorkflowServices, build_listing_agent_graph


class ListingSpecialistAgent(SpecialistAgent):
    name = SpecialistName.LISTING_CONTENT.value

    def __init__(self, services: ListingWorkflowServices) -> None:
        self.graph = build_listing_agent_graph(services=services)

    def invoke(
        self,
        task: AgentTask,
        scope: QueryScope,
        state: dict,
    ) -> SpecialistResult:
        request = self._listing_request(scope, state)
        result = self.graph.invoke({"request_id": task.task_id, "request": request})
        final = result.get("final_result", {})
        final_status = str(final.get("status", "failed"))
        if final_status == "needs_input":
            return SpecialistResult(
                task_id=task.task_id,
                agent=SpecialistName.LISTING_CONTENT,
                status="needs_input",
                summary=str(
                    final.get("clarification_question")
                    or "请继续补充完整商品信息。"
                ),
            )

        artifacts = [
            DataArtifact(
                artifact_id=str(item.get("call_id") or item.get("query_ref") or "mcp-call"),
                source=str(item.get("provider") or "keyword_mcp"),
                tool=str(item.get("tool") or "keyword_research"),
                query={"query_ref": item.get("query_ref")},
                fetched_at=item.get("fetched_at"),
            )
            for item in result.get("artifacts", [])
        ]
        if final_status != "completed":
            return SpecialistResult(
                task_id=task.task_id,
                agent=SpecialistName.LISTING_CONTENT,
                status=final_status,
                summary=str(final.get("summary") or "Listing Agent 未完成交付。"),
                artifacts=artifacts,
                errors=list(final.get("errors", [])),
            )

        evidence_refs = [
            reference
            for item in final.get("keyword_evidence", [])
            for reference in item.get("evidence_refs", [])
        ]
        deliverable = {
            "type": "listing_draft",
            "task_id": task.task_id,
            "draft": final.get("draft"),
            "validation": final.get("validation"),
            "keywords": [
                {
                    "keyword_id": item.get("keyword_id"),
                    "keyword": item.get("keyword"),
                    "tier": item.get("tier"),
                    "evidence_refs": item.get("evidence_refs", []),
                }
                for item in final.get("keyword_evidence", [])
            ],
        }
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.LISTING_CONTENT,
            status="completed",
            summary=str(final.get("summary") or "Listing 草稿已生成并通过质检。"),
            findings=[
                Finding(
                    finding="Listing 草稿已基于有效关键词证据生成。",
                    severity="info",
                    confidence=0.9,
                    evidence_refs=list(dict.fromkeys(evidence_refs)),
                )
            ],
            artifacts=artifacts,
            deliverables=[deliverable],
        )

    def _listing_request(self, scope: QueryScope, state: dict) -> dict[str, Any]:
        explicit = state.get("listing_request")
        if not explicit:
            explicit = state.get("user_context", {}).get("listing_request")
        if isinstance(explicit, dict):
            return explicit

        message_payload = self._json_message_payload(state.get("messages", []))
        if message_payload is not None:
            return message_payload

        request: dict[str, Any] = {}
        if scope.marketplaces:
            request["marketplace"] = scope.marketplaces[0]
        if scope.asins:
            request["current_asin"] = scope.asins[0]
            request["competitor_asins"] = scope.asins[1:6]
        if scope.keywords:
            request["seed_keywords"] = scope.keywords[:3]
        user_context = state.get("user_context", {})
        for field in ("language", "category", "product_brief", "current_listing"):
            if field in user_context:
                request[field] = user_context[field]
        return request

    @staticmethod
    def _json_message_payload(messages: list[Any]) -> dict[str, Any] | None:
        if not messages:
            return None
        message = messages[-1]
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
        if not isinstance(content, str):
            return None
        raw = content.strip()
        if raw.startswith("```json") and raw.endswith("```"):
            raw = raw[7:-3].strip()
        if not raw.startswith("{"):
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

"""Structured LLM planner for the competitor-research capability catalogue."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from .competitor_research_catalog import CAPABILITY_BY_TOOL, competitor_research_catalog
from .llm import StructuredLLM
from .models import AgentTask, QueryScope
from .prompts import COMPETITOR_RESEARCH_ROUTER_SYSTEM_PROMPT


# Tools whose ``dependencies`` require confirmed identifiers from the scope.
ID_DEPENDENT_TOOLS = ("inspect_campaign", "inspect_ad_group")
# Catalogue ``dependencies`` name confirmed scope inputs rather than earlier tools,
# so they are enforced as scope preconditions instead of batch ordering.
SCOPE_DEPENDENCY_FIELDS = {
    "confirmed_campaign_id": "campaign_ids",
    "confirmed_ad_group_id": "ad_group_ids",
}


class CompetitorResearchPlan(BaseModel):
    """A declarative plan; execution arguments are never LLM supplied."""

    batches: list[list[str]] = Field(min_length=1)
    explicit_drilldown_requested: bool = False
    rationale: str = ""


class CompetitorResearchRouter:
    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def plan(self, task: AgentTask, scope: QueryScope) -> CompetitorResearchPlan | None:
        context = json.dumps(
            {
                "user_objective": task.objective,
                "confirmed_scope": {
                    "country": scope.marketplaces[0] if scope.marketplaces else None,
                    "own_asin": scope.own_asin,
                    "competitor_asins": scope.competitor_asins,
                    "campaign_ids": scope.campaign_ids,
                    "ad_group_ids": scope.ad_group_ids,
                },
                "capabilities": competitor_research_catalog(),
            },
            ensure_ascii=False,
        )
        try:
            return self._llm.complete(
                system_prompt=COMPETITOR_RESEARCH_ROUTER_SYSTEM_PROMPT,
                context=context,
                output_model=CompetitorResearchPlan,
                max_tokens=1000,
            )
        except Exception:
            return None


def _scope_satisfies(tool: str, scope: QueryScope) -> bool:
    """Fail closed when a declared dependency has no confirmed scope input."""

    for name in CAPABILITY_BY_TOOL[tool].dependencies:
        field = SCOPE_DEPENDENCY_FIELDS.get(name)
        if field is None or not getattr(scope, field, None):
            return False
    return True


def validate_competitor_research_plan(plan: CompetitorResearchPlan, scope: QueryScope) -> list[list[str]] | None:
    """Reject unknown, repeated, empty, unordered, and unauthorized plan steps."""
    seen: set[str] = set()
    batches: list[list[str]] = []
    for batch in plan.batches:
        if not batch:
            return None
        validated_batch: list[str] = []
        for tool in batch:
            if tool not in CAPABILITY_BY_TOOL or tool in seen:
                return None
            if tool in ID_DEPENDENT_TOOLS and not plan.explicit_drilldown_requested:
                return None
            if not _scope_satisfies(tool, scope):
                return None
            seen.add(tool)
            validated_batch.append(tool)
        # A batch runs concurrently, so a tool the catalogue marks as
        # non-parallelizable must be the only member of its batch.
        if len(validated_batch) > 1 and any(
            not CAPABILITY_BY_TOOL[tool].parallelizable for tool in validated_batch
        ):
            return None
        batches.append(validated_batch)
    return batches or None

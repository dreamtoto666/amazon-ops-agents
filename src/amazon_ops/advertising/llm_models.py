from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


InspectionHypothesisCategory = Literal[
    "traffic", "cost", "conversion", "product", "inventory", "listing"
]


class InspectionHypothesisDecision(BaseModel):
    anomaly_id: str
    category: InspectionHypothesisCategory
    statement: str = Field(min_length=8, max_length=240)
    priority: int = Field(ge=1, le=3)
    required_tools: list[Literal[
        "ad_campaign_group_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_search_term_report",
    ]] = Field(min_length=1, max_length=3)

    @field_validator("category", mode="before")
    @classmethod
    def normalize_sales_category(cls, value: Any) -> Any:
        """Normalize DeepSeek's common metric label to the hypothesis taxonomy.

        Sales is an outcome metric, not a root-cause category.  In the
        inspection taxonomy it belongs to ``conversion``.  Keep every other
        unknown value invalid so schema validation remains a useful guardrail.
        """
        if isinstance(value, str) and value.strip().lower() == "sales":
            return "conversion"
        return value


class InspectionHypothesisLLMOutput(BaseModel):
    hypotheses: list[InspectionHypothesisDecision] = Field(min_length=1, max_length=90)
    warnings: list[str] = Field(default_factory=list, max_length=10)


AttributionCategory = Literal[
    "traffic",
    "cost",
    "conversion",
    "budget",
    "structure",
    "product",
    "inventory",
    "listing",
    "unknown",
]

AttributionTool = Literal[
    "ad_campaign_group_report",
    "ad_campaign_keyword_report",
    "ad_campaign_targeting_report",
    "ad_campaign_search_term_report",
]


class AttributionDecision(BaseModel):
    anomaly_id: str
    category: AttributionCategory
    statement: str = Field(min_length=8, max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)
    finding_ids: list[str] = Field(default_factory=list, max_length=3)
    contributing_finding_ids: list[str] = Field(default_factory=list, max_length=2)
    missing_evidence: list[str] = Field(default_factory=list, max_length=5)


class AttributionCandidateFinding(BaseModel):
    campaign_id: str
    anomaly_id: str
    object_type: Literal["search_term", "keyword", "targeting", "ad_group", "unknown"]
    object_name: str | None = Field(default=None, max_length=300)
    metrics: dict[str, float | int | None] = Field(default_factory=dict, max_length=12)
    evidence_refs: list[str] = Field(default_factory=list, max_length=10)
    judgment_type: Literal["direct", "supporting", "signal", "insufficient"]
    confidence: float = Field(ge=0, le=1)
    depends_on_baseline: bool = False


class CrossReportExplanation(BaseModel):
    campaign_id: str
    anomaly_id: str
    explanation: str = Field(min_length=8, max_length=800)
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)


class AttributionLLMOutput(BaseModel):
    # Candidate interpretation is deliberately separate from confirmed facts.
    candidate_findings: list[AttributionCandidateFinding] = Field(default_factory=list, max_length=15)
    cross_report_explanations: list[CrossReportExplanation] = Field(default_factory=list, max_length=10)
    suspected_patterns: list[str] = Field(default_factory=list, max_length=12)
    missing_evidence: list[str] = Field(default_factory=list, max_length=12)
    suggested_follow_up_tools: list[AttributionTool] = Field(default_factory=list, max_length=4)
    # Kept temporarily for compatibility with already-recorded model fixtures;
    # the validator still constrains these choices to deterministic findings.
    decisions: list[AttributionDecision] = Field(default_factory=list, max_length=10)
    needs_more_evidence: bool = False
    requested_tools: list[AttributionTool] = Field(default_factory=list, max_length=4)
    warnings: list[str] = Field(default_factory=list, max_length=10)


StrategyAction = Literal[
    "observe",
    "adjust_budget",
    "adjust_bid",
    "pause_entity",
    "add_negative",
    "move_search_term",
    "optimize_listing",
    "check_inventory",
]


class StrategyDecision(BaseModel):
    cause_id: str
    action_type: StrategyAction
    title: str = Field(min_length=4, max_length=160)
    rationale: str = Field(min_length=8, max_length=800)
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str | None = Field(default=None, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=10)


class StrategyLLMOutput(BaseModel):
    decisions: list[StrategyDecision] = Field(min_length=1, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=10)

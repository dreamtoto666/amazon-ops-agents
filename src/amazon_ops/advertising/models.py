from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AdEntityType(str, Enum):
    PORTFOLIO = "portfolio"
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    PRODUCT_AD = "product_ad"
    KEYWORD = "keyword"
    TARGET = "target"
    SEARCH_TERM = "search_term"


class AnomalyType(str, Enum):
    SPEND_SPIKE = "spend_spike"
    SALES_DROP = "sales_drop"
    ORDERS_DROP = "orders_drop"
    ACOS_RISE = "acos_rise"
    ROAS_DROP = "roas_drop"
    CPC_RISE = "cpc_rise"
    CTR_DROP = "ctr_drop"
    CVR_DROP = "cvr_drop"
    ZERO_ORDER_WASTE = "zero_order_waste"
    BUDGET_CONSTRAINED = "budget_constrained"
    DELIVERY_DROP = "delivery_drop"


class AdGoal(BaseModel):
    target_acos: float | None = Field(default=None, ge=0)
    target_roas: float | None = Field(default=None, ge=0)
    min_daily_sales: float | None = Field(default=None, ge=0)
    growth_priority: Literal["profit", "balanced", "scale"] = "balanced"


class AdShop(BaseModel):
    profile_id: str
    sid: int | None = None
    store_id: int | None = None
    alias: str
    country: str | None = None
    marketplace_id: str | None = None
    account_type: str | None = None


class DiagnosticPeriod(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "DiagnosticPeriod":
        if self.end < self.start:
            raise ValueError("period end must not be earlier than start")
        return self


class DiagnosticThresholds(BaseModel):
    min_spend: float = Field(default=10, ge=0)
    min_clicks: int = Field(default=5, ge=0)
    relative_change: float = Field(default=0.30, gt=0)
    zero_order_clicks: int = Field(default=12, ge=1)
    confidence_floor: float = Field(default=0.65, ge=0, le=1)


class AdDiagnosticRequest(BaseModel):
    profile_ids: list[str] = Field(min_length=1, max_length=50)
    current_period: DiagnosticPeriod
    baseline_period: DiagnosticPeriod | None = None
    timezone: str = "Asia/Shanghai"
    currency: str | None = None
    campaign_ids: list[str] = Field(default_factory=list, max_length=500)
    asins: list[str] = Field(default_factory=list, max_length=500)
    sponsored_types: list[Literal["sp", "sb", "sd"]] = Field(default_factory=list)
    goal: AdGoal = Field(default_factory=AdGoal)
    thresholds: DiagnosticThresholds = Field(default_factory=DiagnosticThresholds)
    trigger: Literal["manual", "scheduled"] = "manual"
    schedule_id: str | None = None


class AdEntityRef(BaseModel):
    entity_type: AdEntityType
    entity_id: str
    name: str | None = None
    profile_id: str
    campaign_id: str | None = None
    ad_group_id: str | None = None
    asin: str | None = None
    sku: str | None = None


class AdMetricSnapshot(BaseModel):
    entity: AdEntityRef
    period: Literal["current", "baseline"]
    impressions: float = 0
    clicks: float = 0
    spend: float = 0
    sales: float = 0
    orders: float = 0
    ad_units: float = 0
    ctr: float | None = None
    cpc: float | None = None
    cvr: float | None = None
    acos: float | None = None
    roas: float | None = None
    budget: float | None = None


class SearchTermEvidence(BaseModel):
    """Raw Lingxing search-term row, retained only for operator review."""

    term: str
    spend: float = Field(ge=0)
    sales: float = Field(ge=0)
    orders: float = Field(ge=0)
    acos: float | None = Field(default=None, ge=0)


class TargetEvidence(BaseModel):
    kind: Literal["keyword", "targeting"]
    text: str
    spend: float = Field(ge=0)
    sales: float = Field(ge=0)
    orders: float = Field(ge=0)
    bid: float | None = Field(default=None, ge=0)


class TargetPeriodComparison(BaseModel):
    campaign_id: str
    target_key: str
    current_cpc: float | None = Field(default=None, ge=0)
    baseline_cpc: float | None = Field(default=None, ge=0)
    current_cvr: float | None = Field(default=None, ge=0)
    baseline_cvr: float | None = Field(default=None, ge=0)
    current_bid: float | None = Field(default=None, ge=0)
    baseline_bid: float | None = Field(default=None, ge=0)


class AttributionFinding(BaseModel):
    """A compact, anomaly-specific fact derived from a read-only detail report."""

    finding_id: str
    anomaly_id: str
    campaign_id: str
    category: Literal["traffic", "cost", "conversion", "budget", "structure"]
    statement: str
    contribution: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)
    object_ref: str | None = None
    evidence_level: Literal["direct", "supporting", "signal", "insufficient"] = "direct"


class NormalizedDetailFact(BaseModel):
    """One deduplicated, traceable detail row associated with an anomaly.

    Business thresholds never remove a fact. Missing values are explicit so the
    interpretation and validation layers can distinguish no-data from zero.
    """

    fact_id: str
    campaign_id: str
    anomaly_id: str
    report_type: str
    period: Literal["current", "baseline"]
    object_type: Literal["search_term", "keyword", "targeting", "ad_group", "unknown"]
    object_name: str | None = None
    spend: float | None = None
    sales: float | None = None
    orders: float | None = None
    impressions: float | None = None
    clicks: float | None = None
    acos: float | None = None
    roas: float | None = None
    cpc: float | None = None
    ctr: float | None = None
    cvr: float | None = None
    bid: float | None = None
    match_type: str | None = None
    targeting_type: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    evidence_ref: str
    artifact_id: str | None = None
    trace_id: str


class AttributionAggregate(BaseModel):
    """LLM-sized aggregate view derived from complete normalized facts."""

    campaign_id: str
    anomaly_id: str
    report_type: str
    period: Literal["current", "baseline"]
    object_type: str
    total_objects: int
    total_spend: float = 0
    total_sales: float = 0
    total_orders: float = 0
    top_spend: list[dict[str, Any]] = Field(default_factory=list)
    top_zero_order_spend: list[dict[str, Any]] = Field(default_factory=list)
    top_acos: list[dict[str, Any]] = Field(default_factory=list)
    by_match_type: list[dict[str, Any]] = Field(default_factory=list)
    long_tail: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class LLMInterpretation(BaseModel):
    campaign_id: str
    anomaly_id: str
    candidate_findings: list[dict[str, Any]] = Field(default_factory=list)
    cross_report_explanations: list[str] = Field(default_factory=list)
    suspected_patterns: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    suggested_follow_up_tools: list[str] = Field(default_factory=list)
    rejected_candidates: list[str] = Field(default_factory=list)


class DetailCallQuota(BaseModel):
    """One auditable per-report budget bucket for an attribution round."""

    round: int = Field(ge=1, le=2)
    tool: str
    used: int = Field(default=0, ge=0, le=30)
    limit: int = Field(default=30, ge=1, le=30)
    failed: int = Field(default=0, ge=0, le=30)

    @model_validator(mode="after")
    def validate_usage(self) -> "DetailCallQuota":
        if self.used > self.limit or self.failed > self.used:
            raise ValueError("detail call quota usage is invalid")
        return self


class CampaignReportAnomalySummary(BaseModel):
    """Anomaly facts a single campaign obtained from one detail report type."""

    campaign_id: str
    anomaly_ids: list[str] = Field(default_factory=list)
    status: Literal["finding_detected", "no_direct_finding"] = "no_direct_finding"
    evidence_refs: list[str] = Field(default_factory=list)
    findings: list[AttributionFinding] = Field(default_factory=list, max_length=10)


class DetailReportSummary(BaseModel):
    """Compact, per-campaign anomaly State snapshot for one detail report type."""

    tool: str
    campaign_anomalies: list[CampaignReportAnomalySummary] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DiagnosticEvidence(BaseModel):
    evidence_id: str
    trace_id: str
    span_id: str
    stage: str
    source: str = "lingxing_mcp"
    tool: str
    query: dict[str, Any]
    fetched_at: datetime
    record_count: int = Field(ge=0)
    entity_refs: list[str] = Field(default_factory=list)
    high_spend_zero_order_terms: list[SearchTermEvidence] = Field(default_factory=list)
    high_spend_zero_order_targets: list[TargetEvidence] = Field(default_factory=list)
    target_period_comparisons: list[TargetPeriodComparison] = Field(default_factory=list)
    artifact_id: str | None = None


class DetectedAnomaly(BaseModel):
    anomaly_id: str
    anomaly_type: AnomalyType
    entity: AdEntityRef
    metric: str
    current_value: float | None = None
    baseline_value: float | None = None
    absolute_change: float | None = None
    relative_change: float | None = None
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(min_length=1)


class InspectionHypothesis(BaseModel):
    """A deterministic, evidence-backed candidate explanation from inspection."""

    hypothesis_id: str
    anomaly_id: str
    category: Literal["traffic", "cost", "conversion", "product", "inventory", "listing"]
    statement: str
    priority: int = Field(ge=1, le=3)
    required_tools: list[str] = Field(min_length=1, max_length=3)


class ProblemCause(BaseModel):
    cause_id: str
    anomaly_ids: list[str] = Field(min_length=1)
    category: Literal[
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
    statement: str
    confidence: float = Field(ge=0, le=1)
    role: Literal["primary", "contributing"] = "primary"
    finding_refs: list[str] = Field(default_factory=list)
    verified: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class StrategyRecommendation(BaseModel):
    recommendation_id: str
    cause_ids: list[str] = Field(min_length=1)
    action_type: Literal[
        "observe",
        "adjust_budget",
        "adjust_bid",
        "pause_entity",
        "add_negative",
        "move_search_term",
        "optimize_listing",
        "check_inventory",
    ]
    target: AdEntityRef
    title: str
    rationale: str
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str | None = None
    risk_level: Literal["read_only", "low_risk_write", "high_risk_write"]
    evidence_refs: list[str] = Field(min_length=1)


class TodoDetail(BaseModel):
    """Expanded, auditable context shown when an operator opens a todo."""

    diagnosis: str
    metric_summary: str
    attribution: str
    checked_sources: list[str] = Field(default_factory=list)
    recommendation: str
    expected_effect: str | None = None


class OperationsTodo(BaseModel):
    todo_id: str
    recommendation_id: str
    profile_id: str
    title: str
    description: str
    priority: Literal["low", "medium", "high", "urgent"]
    status: Literal["pending", "needs_review", "approved", "rejected", "done"] = "pending"
    action_type: str
    target: AdEntityRef
    proposed_change: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(min_length=1)
    detail: TodoDetail | None = None
    approval_required: bool = True
    due_at: datetime | None = None
    dedupe_key: str


class DataInspectionResult(BaseModel):
    snapshots: list[AdMetricSnapshot] = Field(default_factory=list)
    anomalies: list[DetectedAnomaly] = Field(default_factory=list)
    hypotheses: list[InspectionHypothesis] = Field(default_factory=list)
    evidence: list[DiagnosticEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AttributionResult(BaseModel):
    causes: list[ProblemCause] = Field(default_factory=list)
    evidence: list[DiagnosticEvidence] = Field(default_factory=list)
    findings: list[AttributionFinding] = Field(default_factory=list)
    needs_more_evidence: bool = False
    requested_tools: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    detail_call_quotas: list[DetailCallQuota] = Field(default_factory=list)
    report_summaries: dict[str, DetailReportSummary] = Field(default_factory=dict)
    normalized_detail_facts: list[NormalizedDetailFact] = Field(default_factory=list)
    attribution_aggregates: list[AttributionAggregate] = Field(default_factory=list)
    llm_interpretations: list[LLMInterpretation] = Field(default_factory=list)
    validated_findings: list[AttributionFinding] = Field(default_factory=list)


class StrategyResult(BaseModel):
    recommendations: list[StrategyRecommendation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ReviewTodoResult(BaseModel):
    todos: list[OperationsTodo] = Field(default_factory=list)
    rejected_recommendation_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AdDiagnosticResult(BaseModel):
    trace_id: str
    run_id: str
    span_id: str
    stage: str
    status: Literal["completed", "no_anomaly", "needs_review", "failed"]
    summary: str
    anomalies: list[DetectedAnomaly] = Field(default_factory=list)
    causes: list[ProblemCause] = Field(default_factory=list)
    recommendations: list[StrategyRecommendation] = Field(default_factory=list)
    todos: list[OperationsTodo] = Field(default_factory=list)
    evidence: list[DiagnosticEvidence] = Field(default_factory=list)
    llm_interpretations: list[LLMInterpretation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

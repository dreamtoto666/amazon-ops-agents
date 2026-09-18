from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Domain(str, Enum):
    STORE = "store"
    PRODUCT = "product"
    ADVERTISING = "advertising"
    INVENTORY = "inventory"
    PROFIT = "profit"
    KEYWORD = "keyword"
    COMPETITOR = "competitor"
    FOLLOW_SALE = "follow_sale"
    LISTING = "listing"
    REPORT = "report"
    SYSTEM = "system"


class Action(str, Enum):
    QUERY = "query"
    OVERVIEW = "overview"
    COMPARE = "compare"
    DIAGNOSE = "diagnose"
    RECOMMEND = "recommend"
    MONITOR = "monitor"
    CREATE = "create"
    UPDATE = "update"
    EXPLAIN = "explain"


class RiskLevel(str, Enum):
    READ_ONLY = "read_only"
    LOW_RISK_WRITE = "low_risk_write"
    HIGH_RISK_WRITE = "high_risk_write"
    UNSUPPORTED = "unsupported"


class RequestRoute(str, Enum):
    EXECUTE = "execute"
    CLARIFY = "clarify"
    RESPOND = "respond"
    APPROVAL = "approval"
    UNSUPPORTED = "unsupported"


class SpecialistName(str, Enum):
    SALES_PROFIT = "sales_profit"
    ADVERTISING = "advertising"
    INVENTORY = "inventory"
    MARKET_RISK = "market_risk"
    LISTING_CONTENT = "listing_content"
    COMPETITOR_ADVERTISING = "competitor_advertising"


SpecialistStatus = Literal[
    "completed",
    "degraded",
    "failed",
    "needs_input",
    "unavailable",
]


class DateRange(BaseModel):
    start: date
    end: date


class AnalysisPeriod(BaseModel):
    current: DateRange
    baseline: DateRange | None = None
    granularity: str = "day"


class QueryScope(BaseModel):
    shop_ids: list[str] = Field(default_factory=list)
    marketplaces: list[str] = Field(default_factory=list)
    asins: list[str] = Field(default_factory=list)
    # Kept separate from ``asins`` so a comparison never guesses which ASIN is
    # the operator's product and which ones belong to competitors.
    own_asin: str | None = None
    competitor_asins: list[str] = Field(default_factory=list)
    mskus: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)
    campaign_ids: list[str] = Field(default_factory=list)
    ad_group_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    period: AnalysisPeriod | None = None
    currency: str | None = None

    def has_product_target(self) -> bool:
        return bool(self.asins or self.mskus or self.skus)


class UserIntent(BaseModel):
    domain: Domain
    action: Action
    secondary_domains: list[Domain] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class UnderstandRequestResult(BaseModel):
    intent: UserIntent
    scope: QueryScope
    route: RequestRoute
    risk_level: RiskLevel
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    normalized_request: str
    response_mode: Literal["chat", "competitor_report"] = "chat"
    # Old checkpoints did not carry this field. Defaulting to live preserves
    # their conservative behavior of querying operational data.
    answer_source: Literal["history", "live", "general"] = "live"


class AgentTask(BaseModel):
    task_id: str
    agent: SpecialistName
    objective: str
    priority: int = 1
    depends_on: list[str] = Field(default_factory=list)
    reason: str


class RoutePlan(BaseModel):
    route: RequestRoute = RequestRoute.EXECUTE
    execution_mode: str = "parallel"
    tasks: list[AgentTask] = Field(default_factory=list)
    max_rounds: int = 1


class DataArtifact(BaseModel):
    artifact_id: str
    source: str = "lingxing_mcp"
    tool: str
    query: dict[str, Any] = Field(default_factory=dict)
    fetched_at: str | None = None


ProcessedDataStatus = Literal["available", "partial", "unavailable"]
class CompetitorDataModule(BaseModel):
    """One evidence-bounded data module passed to the competitor report agent."""

    status: ProcessedDataStatus
    records: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    missing_reasons: list[str] = Field(default_factory=list)


AsinRole = Literal["own", "competitor"]


class VariantTrafficRecord(BaseModel):
    variant_asin: str = Field(min_length=1, max_length=32)
    variant_attributes: Any = None
    total_traffic_ratio: float | None = None
    natural_traffic_ratio: float | None = None
    ad_traffic_ratio: float | None = None
    sp_ratio: float | None = None
    sp_recommend_ratio: float | None = None
    sb_ratio: float | None = None
    sbv_ratio: float | None = None


class TrafficScoreRatio(BaseModel):
    """One Sif traffic channel's raw score and share."""

    score: float | None = None
    ratio: float | None = None


class AdvertisingTrafficDistribution(BaseModel):
    sp: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)
    sp_recommend: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)
    sb: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)
    sbv: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)


class TrafficKeywordLookupRecord(BaseModel):
    asin_role: AsinRole
    parent_asin: str = Field(min_length=1, max_length=32)
    listing_natural_traffic: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)
    listing_ad_traffic: TrafficScoreRatio = Field(default_factory=TrafficScoreRatio)
    advertising_traffic_distribution: AdvertisingTrafficDistribution = Field(
        default_factory=AdvertisingTrafficDistribution
    )
    variants: list[VariantTrafficRecord] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class TrafficKeywordReverseLookupRecord(BaseModel):
    asin_role: AsinRole
    parent_asin: str = Field(min_length=1, max_length=32)
    keyword: str = Field(min_length=1)
    total_traffic_ratio: float
    natural_traffic_ratio: float
    ad_traffic_ratio: float
    growth_period_change: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class MultiVariantOrganicVariantRecord(BaseModel):
    variant_asin: str = Field(min_length=1, max_length=32)
    natural_traffic_ratio: float
    natural_position_days: int | None = None
    average_natural_rank: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class MultiVariantOrganicPositionRecord(BaseModel):
    asin_role: AsinRole
    parent_asin: str = Field(min_length=1, max_length=32)
    keyword: str = Field(min_length=1)
    natural_traffic: float | None = None
    natural_traffic_ratio: float
    multi_organic_extra_natural_traffic: float | None = None
    variants: list[MultiVariantOrganicVariantRecord] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class RecommendationPlacementRecord(BaseModel):
    asin_role: AsinRole
    parent_asin: str = Field(min_length=1, max_length=32)
    period: str = Field(min_length=1)
    placement_name: str = Field(min_length=1)
    traffic_ratio: float
    campaign_ids: list[str] = Field(default_factory=list)
    campaign_count: int | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class TrafficKeywordLookupModule(CompetitorDataModule):
    records: list[TrafficKeywordLookupRecord] = Field(default_factory=list)


class TrafficKeywordReverseLookupModule(CompetitorDataModule):
    records: list[TrafficKeywordReverseLookupRecord] = Field(default_factory=list)


class MultiVariantOrganicPositionModule(CompetitorDataModule):
    records: list[MultiVariantOrganicPositionRecord] = Field(default_factory=list)


class RecommendationPlacementModule(CompetitorDataModule):
    records: list[RecommendationPlacementRecord] = Field(default_factory=list)


class CompetitorDataModules(BaseModel):
    """The four fixed modules requested for a parent-ASIN comparison."""

    own_parent_asin: str = Field(min_length=1, max_length=32)
    competitor_parent_asin: str = Field(min_length=1, max_length=32)
    marketplace: str = Field(min_length=1, max_length=16)
    traffic_keyword_lookup: TrafficKeywordLookupModule
    traffic_keyword_reverse_lookup: TrafficKeywordReverseLookupModule
    multi_variant_organic_position: MultiVariantOrganicPositionModule
    recommendation_placement: RecommendationPlacementModule


class Hypothesis(BaseModel):
    description: str
    requires_agent: SpecialistName | None = None
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    finding: str
    severity: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class RecommendedAction(BaseModel):
    action: str
    risk_level: RiskLevel = RiskLevel.READ_ONLY


class SpecialistResult(BaseModel):
    task_id: str
    agent: SpecialistName
    status: SpecialistStatus = "completed"
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    artifacts: list[DataArtifact] = Field(default_factory=list)
    deliverables: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class FinalResponse(BaseModel):
    answer: str
    confirmed_findings: list[Finding] = Field(default_factory=list)
    open_hypotheses: list[Hypothesis] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    deliverables: list[dict[str, Any]] = Field(default_factory=list)


ReportSectionStatus = Literal["available", "partial", "unavailable"]


class CompetitorReportSection(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    status: ReportSectionStatus
    content: str = Field(min_length=1, max_length=5000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    missing_reasons: list[str] = Field(default_factory=list, max_length=10)


class CompetitorAdvertisingReport(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    sections: list[CompetitorReportSection] = Field(default_factory=list, max_length=4)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    confirmed_findings: list[Finding] = Field(default_factory=list, max_length=30)
    open_hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=30)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list, max_length=30)

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any

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
    mskus: list[str] = Field(default_factory=list)
    skus: list[str] = Field(default_factory=list)
    campaign_ids: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    period: AnalysisPeriod | None = None
    currency: str | None = None

    def has_product_target(self) -> bool:
        return bool(self.asins or self.mskus or self.skus)


class UserIntent(BaseModel):
    domain: Domain
    action: Action
    secondary_domains: list[Domain] = Field(default_factory=list)
    primary_agent: SpecialistName | None = None
    supporting_agents: list[SpecialistName] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class UnderstandRequestResult(BaseModel):
    intent: UserIntent
    scope: QueryScope
    route: RequestRoute
    risk_level: RiskLevel
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    normalized_request: str


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
    status: str = "completed"
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

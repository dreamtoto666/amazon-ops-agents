from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class KeywordTier(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    LONG_TAIL = "long_tail"
    BACKEND_ONLY = "backend_only"


class ProductFact(BaseModel):
    name: str
    value: str
    source: str
    status: Literal["confirmed", "unverified"] = "confirmed"
    public_use_allowed: bool = True
    evidence_ref: str | None = None


class ProductBrief(BaseModel):
    product_name: str = Field(min_length=1)
    product_type: str = Field(min_length=1)
    brand: str = Field(min_length=1)
    model_name: str | None = None
    variant_attributes: dict[str, str] = Field(default_factory=dict)
    core_features: list[ProductFact] = Field(min_length=3)
    materials: list[ProductFact] = Field(min_length=1)
    specifications: list[ProductFact] = Field(min_length=2)
    target_audiences: list[str] = Field(min_length=1)
    use_cases: list[str] = Field(min_length=2)
    package_contents: list[str] = Field(min_length=1)
    certifications: list[ProductFact] = Field(default_factory=list)
    safety_warnings: list[str] = Field(default_factory=list)
    prohibited_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_confirmed_public_facts(self) -> "ProductBrief":
        required = [*self.core_features, *self.materials, *self.specifications]
        invalid = [
            fact.name
            for fact in required
            if fact.status != "confirmed" or not fact.public_use_allowed
        ]
        if invalid:
            names = "、".join(invalid)
            raise ValueError(f"核心商品事实必须已确认且允许公开使用：{names}")
        return self


class ListingTaskRequest(BaseModel):
    marketplace: str = Field(min_length=2)
    language: str = Field(min_length=2)
    category: str = Field(min_length=1)
    product_brief: ProductBrief
    content_request: set[
        Literal["title", "subtitle", "bullet_points", "description", "search_terms"]
    ] = Field(
        default_factory=lambda: {
            "title",
            "subtitle",
            "bullet_points",
            "description",
            "search_terms",
        }
    )
    current_asin: str | None = None
    competitor_asins: list[str] = Field(default_factory=list, max_length=5)
    seed_keywords: list[str] = Field(default_factory=list, max_length=3)
    current_listing: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_research_entry(self) -> "ListingTaskRequest":
        if not (self.current_asin or self.competitor_asins or self.seed_keywords):
            raise ValueError("current_asin、competitor_asins 或 seed_keywords 至少提供一项")
        return self


class KeywordObservation(BaseModel):
    observation_id: str
    source: Literal["seller_sprite", "sif"]
    tool: str
    query_ref: str
    fetched_at: str
    data_period: str | None = None
    metrics: dict[str, int | float | str | bool | None] = Field(default_factory=dict)


class KeywordPlacement(BaseModel):
    field: Literal["title", "subtitle", "bullet_points", "description", "search_terms"]
    index: int | None = Field(default=None, ge=1, le=5)
    occurrences: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_index(self) -> "KeywordPlacement":
        if self.field == "bullet_points" and self.index is None:
            raise ValueError("bullet_points placement 必须指定 1 到 5 的 index")
        if self.field != "bullet_points" and self.index is not None:
            raise ValueError("只有 bullet_points placement 可以指定 index")
        return self


class KeywordEvidence(BaseModel):
    keyword_id: str
    keyword: str = Field(min_length=1)
    normalized_keyword: str = Field(min_length=1)
    marketplace: str
    relevance: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    score: float = Field(ge=0, le=1)
    observations: list[KeywordObservation] = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    selection_reason: str | None = None
    exclusion_reason: str | None = None
    policy_flags: list[str] = Field(default_factory=list)
    tier: KeywordTier | None = None
    placements: list[KeywordPlacement] = Field(default_factory=list)

    @property
    def sources(self) -> set[str]:
        return {observation.source for observation in self.observations}

    @property
    def is_valid(self) -> bool:
        return (
            self.relevance >= 0.7
            and bool(self.evidence_refs)
            and not self.exclusion_reason
            and not self.policy_flags
        )


class KeywordResearchBatch(BaseModel):
    evidence: list[KeywordEvidence] = Field(default_factory=list)
    executed_query_fingerprints: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)


class ListingDraft(BaseModel):
    title: str = Field(min_length=1)
    subtitle: str = Field(min_length=1)
    bullet_points: list[str] = Field(min_length=5, max_length=5)
    description: str = Field(min_length=1)
    search_terms: str = Field(min_length=1)


class CopyIssue(BaseModel):
    code: str
    field: str
    message: str
    severity: Literal["warning", "error"] = "error"


class CopyValidationReport(BaseModel):
    passed: bool
    issues: list[CopyIssue] = Field(default_factory=list)
    keyword_coverage: float = Field(ge=0, le=1)


class ListingContentResult(BaseModel):
    status: Literal[
        "completed",
        "needs_input",
        "insufficient_keyword_evidence",
        "needs_review",
        "failed",
    ]
    summary: str
    draft: ListingDraft | None = None
    keyword_evidence: list[KeywordEvidence] = Field(default_factory=list)
    validation: CopyValidationReport | None = None
    missing_fields: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)

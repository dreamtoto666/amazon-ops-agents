from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .models import (
    CopyValidationReport,
    KeywordEvidence,
    KeywordResearchBatch,
    KeywordTier,
    ListingContentResult,
    ListingDraft,
    ListingTaskRequest,
)
from .state import ListingAgentState


TARGET_KEYWORD_COUNT = 30
MAX_SUPPLEMENT_SEARCH_ROUNDS = 3
MAX_COPY_REVISION_ROUNDS = 1
TIER_COUNTS = {
    KeywordTier.PRIMARY: 2,
    KeywordTier.SECONDARY: 8,
    KeywordTier.LONG_TAIL: 12,
    KeywordTier.BACKEND_ONLY: 8,
}


class KeywordResearcher(Protocol):
    def initial_search(
        self, state: ListingAgentState
    ) -> list[KeywordEvidence] | KeywordResearchBatch: ...

    def supplement_search(
        self, state: ListingAgentState
    ) -> list[KeywordEvidence] | KeywordResearchBatch: ...


class ListingCopywriter(Protocol):
    def generate(self, state: ListingAgentState) -> ListingDraft: ...

    def revise(self, state: ListingAgentState) -> ListingDraft: ...


class ListingCopyValidator(Protocol):
    def validate(self, state: ListingAgentState) -> CopyValidationReport: ...


@dataclass(frozen=True)
class ListingWorkflowServices:
    researcher: KeywordResearcher
    copywriter: ListingCopywriter
    validator: ListingCopyValidator


def _research_update(
    result: list[KeywordEvidence] | KeywordResearchBatch,
    state: ListingAgentState,
) -> dict:
    batch = (
        result
        if isinstance(result, KeywordResearchBatch)
        else KeywordResearchBatch(evidence=result)
    )
    return {
        "new_keyword_evidence": [item.model_dump(mode="json") for item in batch.evidence],
        "executed_query_fingerprints": list(
            dict.fromkeys(
                [
                    *state.get("executed_query_fingerprints", []),
                    *batch.executed_query_fingerprints,
                ]
            )
        ),
        "artifacts": [*state.get("artifacts", []), *batch.artifacts],
        "warnings": [*state.get("warnings", []), *batch.warnings],
    }


def _missing_fields(error: ValidationError) -> list[str]:
    fields: list[str] = []
    for issue in error.errors():
        location = issue.get("loc", ())
        field = ".".join(str(part) for part in location if not isinstance(part, int)) or "request"
        if field not in fields:
            fields.append(field)
    return fields


FIELD_LABELS = {
    "marketplace": "目标站点",
    "language": "文案语言",
    "category": "商品类目",
    "product_brief": "完整商品信息",
    "product_brief.product_name": "商品名称",
    "product_brief.product_type": "商品类型",
    "product_brief.brand": "品牌",
    "product_brief.core_features": "至少 3 项核心功能或卖点",
    "product_brief.materials": "至少 1 项材质信息",
    "product_brief.specifications": "至少 2 项规格信息",
    "product_brief.target_audiences": "目标用户",
    "product_brief.use_cases": "至少 2 个使用场景",
    "product_brief.package_contents": "包装清单",
    "request": "自己的 ASIN、竞品 ASIN 或种子关键词",
}


def _clarification_question(missing_fields: list[str]) -> str:
    labels = [FIELD_LABELS.get(field, field) for field in missing_fields]
    return f"开始关键词研究前，请继续补充：{'、'.join(labels)}。"


def _merge_keyword_evidence(state: ListingAgentState) -> tuple[list[KeywordEvidence], list[KeywordEvidence]]:
    merged: dict[str, KeywordEvidence] = {}
    for raw in [*state.get("keyword_evidence", []), *state.get("new_keyword_evidence", [])]:
        item = KeywordEvidence.model_validate(raw)
        key = item.normalized_keyword.casefold().strip()
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            continue
        observations = {
            observation.observation_id: observation
            for observation in [*existing.observations, *item.observations]
        }
        evidence_refs = list(dict.fromkeys([*existing.evidence_refs, *item.evidence_refs]))
        preferred = item if item.score > existing.score else existing
        merged[key] = preferred.model_copy(
            update={
                "observations": list(observations.values()),
                "evidence_refs": evidence_refs,
                "confidence": max(existing.confidence, item.confidence),
                "score": max(existing.score, item.score),
            }
        )
    valid = sorted((item for item in merged.values() if item.is_valid), key=lambda item: item.score, reverse=True)
    excluded = sorted((item for item in merged.values() if not item.is_valid), key=lambda item: item.score, reverse=True)
    return valid, excluded


def _keyword_gaps(items: list[KeywordEvidence]) -> dict[str, int]:
    dual_source_count = sum(item.sources == {"seller_sprite", "sif"} for item in items)
    return {
        "total": max(0, TARGET_KEYWORD_COUNT - len(items)),
        "primary_dual_source": max(0, TIER_COUNTS[KeywordTier.PRIMARY] - dual_source_count),
    }


def _assign_tiers(items: list[KeywordEvidence]) -> list[KeywordEvidence]:
    if len(items) < TARGET_KEYWORD_COUNT:
        raise ValueError("cannot build keyword plan with fewer than 30 valid keywords")

    primary = [item for item in items if item.sources == {"seller_sprite", "sif"}][
        : TIER_COUNTS[KeywordTier.PRIMARY]
    ]
    if len(primary) < TIER_COUNTS[KeywordTier.PRIMARY]:
        raise ValueError("two primary keywords require both seller_sprite and sif evidence")
    primary_ids = {item.keyword_id for item in primary}
    remaining = [item for item in items if item.keyword_id not in primary_ids]

    planned = [item.model_copy(update={"tier": KeywordTier.PRIMARY}) for item in primary]
    offset = 0
    for tier in (KeywordTier.SECONDARY, KeywordTier.LONG_TAIL, KeywordTier.BACKEND_ONLY):
        count = TIER_COUNTS[tier]
        planned.extend(
            item.model_copy(update={"tier": tier})
            for item in remaining[offset : offset + count]
        )
        offset += count
    return planned


def build_listing_agent_graph(*, services: ListingWorkflowServices):
    def validate_input(state: ListingAgentState) -> dict:
        try:
            request = ListingTaskRequest.model_validate(state.get("request", {}))
        except ValidationError as exc:
            missing = _missing_fields(exc)
            return {
                "missing_fields": missing,
                "clarification_question": _clarification_question(missing),
                "research_status": "waiting_input",
            }
        return {
            "validated_request": request.model_dump(mode="json"),
            "missing_fields": [],
            "clarification_question": None,
            "supplement_search_round": 0,
            "max_supplement_search_rounds": MAX_SUPPLEMENT_SEARCH_ROUNDS,
            "revision_round": 0,
            "max_revision_rounds": MAX_COPY_REVISION_ROUNDS,
            "research_status": "ready",
            "executed_query_fingerprints": [],
            "artifacts": [],
            "warnings": [],
        }

    def after_input(state: ListingAgentState) -> str:
        return "needs_input" if state.get("missing_fields") else "research"

    def request_product_information(state: ListingAgentState) -> dict:
        question = state.get("clarification_question") or "请继续补充完整商品信息。"
        result = ListingContentResult(
            status="needs_input",
            summary="商品信息不足，关键词研究和文案生成尚未开始。",
            missing_fields=state.get("missing_fields", []),
            clarification_question=question,
        )
        return {"final_result": result.model_dump(mode="json")}

    def initial_research(state: ListingAgentState) -> dict:
        update = _research_update(services.researcher.initial_search(state), state)
        update["research_status"] = "initial_completed"
        return update

    def evaluate_keywords(state: ListingAgentState) -> dict:
        valid, excluded = _merge_keyword_evidence(state)
        gaps = _keyword_gaps(valid)
        sufficient = all(value == 0 for value in gaps.values())
        return {
            "keyword_evidence": [item.model_dump(mode="json") for item in valid],
            "selected_keywords": [],
            "excluded_keywords": [item.model_dump(mode="json") for item in excluded],
            "new_keyword_evidence": [],
            "valid_keyword_count": len(valid),
            "keyword_gaps": gaps,
            "seen_keywords": [item.normalized_keyword for item in [*valid, *excluded]],
            "research_status": "sufficient" if sufficient else "insufficient",
        }

    def after_keyword_evaluation(state: ListingAgentState) -> str:
        gaps = state.get("keyword_gaps", {})
        if gaps and all(value == 0 for value in gaps.values()):
            return "build_keyword_plan"
        if state.get("supplement_search_round", 0) < state.get(
            "max_supplement_search_rounds", MAX_SUPPLEMENT_SEARCH_ROUNDS
        ):
            return "supplement_keyword_search"
        return "insufficient_keyword_evidence"

    def supplement_keyword_search(state: ListingAgentState) -> dict:
        update = _research_update(services.researcher.supplement_search(state), state)
        update.update(
            {
                "supplement_search_round": state.get("supplement_search_round", 0) + 1,
                "research_status": "supplementing",
            }
        )
        return update

    def build_keyword_plan(state: ListingAgentState) -> dict:
        evidence = [KeywordEvidence.model_validate(item) for item in state["keyword_evidence"]]
        planned = _assign_tiers(evidence)
        return {
            "selected_keywords": [item.model_dump(mode="json") for item in planned],
            "research_status": "planned",
        }

    def generate_copy(state: ListingAgentState) -> dict:
        draft = services.copywriter.generate(state)
        return {"draft": draft.model_dump(mode="json")}

    def validate_copy(state: ListingAgentState) -> dict:
        report = services.validator.validate(state)
        return {"validation": report.model_dump(mode="json")}

    def after_copy_validation(state: ListingAgentState) -> str:
        report = CopyValidationReport.model_validate(state["validation"])
        if report.passed:
            return "complete"
        if state.get("revision_round", 0) < state.get(
            "max_revision_rounds", MAX_COPY_REVISION_ROUNDS
        ):
            return "revise"
        return "needs_review"

    def revise_copy(state: ListingAgentState) -> dict:
        draft = services.copywriter.revise(state)
        return {
            "draft": draft.model_dump(mode="json"),
            "revision_round": state.get("revision_round", 0) + 1,
        }

    def insufficient_keyword_evidence(state: ListingAgentState) -> dict:
        evidence = [KeywordEvidence.model_validate(item) for item in state.get("keyword_evidence", [])]
        missing = max(0, TARGET_KEYWORD_COUNT - len(evidence))
        primary_missing = state.get("keyword_gaps", {}).get("primary_dual_source", 0)
        details = []
        if missing:
            details.append(f"缺少 {missing} 个有效关键词")
        if primary_missing:
            details.append(f"缺少 {primary_missing} 个双源主关键词")
        shortage = "，".join(details) or "关键词结构不符合要求"
        result = ListingContentResult(
            status="insufficient_keyword_evidence",
            summary=f"三轮补充搜索后仍{shortage}，未生成文案。",
            keyword_evidence=evidence,
        )
        return {"research_status": "exhausted", "final_result": result.model_dump(mode="json")}

    def complete(state: ListingAgentState) -> dict:
        result = ListingContentResult(
            status="completed",
            summary="已基于 30 个有效关键词生成并验证 Listing 草稿。",
            draft=ListingDraft.model_validate(state["draft"]),
            keyword_evidence=[
                KeywordEvidence.model_validate(item) for item in state["selected_keywords"]
            ],
            validation=CopyValidationReport.model_validate(state["validation"]),
        )
        return {"final_result": result.model_dump(mode="json")}

    def needs_review(state: ListingAgentState) -> dict:
        result = ListingContentResult(
            status="needs_review",
            summary="文案自动修订后仍未通过质检，需要人工复核。",
            draft=ListingDraft.model_validate(state["draft"]),
            keyword_evidence=[
                KeywordEvidence.model_validate(item) for item in state["selected_keywords"]
            ],
            validation=CopyValidationReport.model_validate(state["validation"]),
        )
        return {"final_result": result.model_dump(mode="json")}

    graph = StateGraph(ListingAgentState)
    graph.add_node("validate_input", validate_input)
    graph.add_node("request_product_information", request_product_information)
    graph.add_node("initial_research", initial_research)
    graph.add_node("evaluate_keywords", evaluate_keywords)
    graph.add_node("supplement_keyword_search", supplement_keyword_search)
    graph.add_node("build_keyword_plan", build_keyword_plan)
    graph.add_node("generate_copy", generate_copy)
    graph.add_node("validate_copy", validate_copy)
    graph.add_node("revise_copy", revise_copy)
    graph.add_node("insufficient_keyword_evidence", insufficient_keyword_evidence)
    graph.add_node("complete", complete)
    graph.add_node("needs_review", needs_review)

    graph.add_edge(START, "validate_input")
    graph.add_conditional_edges(
        "validate_input",
        after_input,
        {"needs_input": "request_product_information", "research": "initial_research"},
    )
    graph.add_edge("request_product_information", END)
    graph.add_edge("initial_research", "evaluate_keywords")
    graph.add_conditional_edges(
        "evaluate_keywords",
        after_keyword_evaluation,
        {
            "build_keyword_plan": "build_keyword_plan",
            "supplement_keyword_search": "supplement_keyword_search",
            "insufficient_keyword_evidence": "insufficient_keyword_evidence",
        },
    )
    graph.add_edge("supplement_keyword_search", "evaluate_keywords")
    graph.add_edge("insufficient_keyword_evidence", END)
    graph.add_edge("build_keyword_plan", "generate_copy")
    graph.add_edge("generate_copy", "validate_copy")
    graph.add_conditional_edges(
        "validate_copy",
        after_copy_validation,
        {"complete": "complete", "revise": "revise_copy", "needs_review": "needs_review"},
    )
    graph.add_edge("revise_copy", "validate_copy")
    graph.add_edge("complete", END)
    graph.add_edge("needs_review", END)
    return graph.compile()

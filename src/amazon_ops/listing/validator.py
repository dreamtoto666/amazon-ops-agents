from __future__ import annotations

import re

from .models import (
    CopyIssue,
    CopyValidationReport,
    KeywordEvidence,
    ListingDraft,
    ListingTaskRequest,
)
from .state import ListingAgentState


class DeterministicListingValidator:
    """Validate structural and evidence-based rules without another LLM call."""

    def validate(self, state: ListingAgentState) -> CopyValidationReport:
        draft = ListingDraft.model_validate(state.get("draft", {}))
        request = ListingTaskRequest.model_validate(
            state.get("validated_request") or state.get("request") or {}
        )
        selected = [
            KeywordEvidence.model_validate(item)
            for item in state.get("selected_keywords", [])
        ]
        issues: list[CopyIssue] = []

        if len(draft.title) > 200:
            issues.append(
                CopyIssue(
                    code="TITLE_TOO_LONG",
                    field="title",
                    message="主标题超过通用安全上限 200 个字符。",
                )
            )
        if len(draft.bullet_points) != 5:
            issues.append(
                CopyIssue(
                    code="BULLET_COUNT",
                    field="bullet_points",
                    message="五点描述必须恰好包含 5 条。",
                )
            )

        all_copy = " ".join(
            [
                draft.title,
                draft.subtitle,
                *draft.bullet_points,
                draft.description,
                draft.search_terms,
            ]
        ).casefold()
        for claim in request.product_brief.prohibited_claims:
            if claim.casefold().strip() and claim.casefold() in all_copy:
                issues.append(
                    CopyIssue(
                        code="PROHIBITED_CLAIM",
                        field="all",
                        message=f"文案包含禁止声明：{claim}",
                    )
                )

        covered = sum(
            bool(
                re.search(
                    rf"(?<!\w){re.escape(item.normalized_keyword.casefold())}(?!\w)",
                    all_copy,
                )
            )
            for item in selected
        )
        coverage = covered / len(selected) if selected else 0.0
        if selected and coverage < 0.6:
            issues.append(
                CopyIssue(
                    code="KEYWORD_COVERAGE_LOW",
                    field="all",
                    message=f"有效关键词覆盖率仅为 {coverage:.0%}，低于 60%。",
                )
            )

        return CopyValidationReport(
            passed=not any(issue.severity == "error" for issue in issues),
            issues=issues,
            keyword_coverage=coverage,
        )

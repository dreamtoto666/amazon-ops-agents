from __future__ import annotations

from typing import Any
from uuid import uuid4

from amazon_ops.llm import LLMError, StructuredLLM

from .agents import EvidenceBasedProblemAttributionAgent, RuleBasedDataInspectionAgent
from .gateway import AdvertisingDataGateway
from .llm_models import (
    AttributionLLMOutput,
    InspectionHypothesisLLMOutput,
    StrategyLLMOutput,
)
from .models import (
    AdDiagnosticRequest,
    AttributionResult,
    DataInspectionResult,
    InspectionHypothesis,
    LLMInterpretation,
    ProblemCause,
    StrategyRecommendation,
    StrategyResult,
)
from .prompts import (
    ADVERTISING_ATTRIBUTION_SYSTEM_PROMPT,
    ADVERTISING_INSPECTION_HYPOTHESIS_SYSTEM_PROMPT,
    ADVERTISING_STRATEGY_SYSTEM_PROMPT,
    build_attribution_context,
    build_inspection_hypothesis_context,
    build_strategy_context,
)
from .state import AdvertisingDiagnosticState


class DeepSeekDataInspectionAgent:
    """Keeps metrics deterministic; uses DeepSeek only for candidate hypotheses."""

    MAX_LLM_ANOMALIES = 30
    MAX_ANOMALIES_PER_CALL = 10

    def __init__(self, gateway: AdvertisingDataGateway, llm: StructuredLLM) -> None:
        self.inspector = RuleBasedDataInspectionAgent(gateway)
        self.llm = llm

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> DataInspectionResult:
        inspection = self.inspector.invoke(request, state)
        if not inspection.anomalies:
            return inspection
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        selected = sorted(
            inspection.anomalies,
            key=lambda item: (severity_rank[item.severity], item.confidence),
            reverse=True,
        )[: self.MAX_LLM_ANOMALIES]
        hypotheses_by_anomaly: dict[str, list[InspectionHypothesis]] = {}
        model_warnings: list[str] = []
        for start in range(0, len(selected), self.MAX_ANOMALIES_PER_CALL):
            batch = selected[start : start + self.MAX_ANOMALIES_PER_CALL]
            batch_ids = {item.anomaly_id for item in batch}
            try:
                output = self.llm.complete(
                    system_prompt=ADVERTISING_INSPECTION_HYPOTHESIS_SYSTEM_PROMPT,
                    context=build_inspection_hypothesis_context(
                        request, inspection, anomalies=batch
                    ),
                    output_model=InspectionHypothesisLLMOutput,
                    max_tokens=2400,
                )
            except LLMError as exc:
                # Hypotheses are advisory only. A malformed model response must
                # not prevent deterministic anomaly detection and the safe
                # rule-based hypotheses from reaching attribution.
                if not (isinstance(exc.code, str) and exc.code.startswith("DEEPSEEK_INVALID_")):
                    raise
                model_warnings.append(
                    "DeepSeek 候选假设格式无效，本批异常已改用规则化假设继续归因。"
                )
                continue
            model_warnings.extend(output.warnings)
            for decision in output.hypotheses:
                if decision.anomaly_id not in batch_ids:
                    raise LLMError(
                        "DeepSeek inspection referenced an invalid anomaly",
                        code="DEEPSEEK_INVALID_INSPECTION_HYPOTHESIS",
                    )
                bucket = hypotheses_by_anomaly.setdefault(decision.anomaly_id, [])
                if len(bucket) == 2:
                    continue
                bucket.append(
                    InspectionHypothesis(
                        hypothesis_id=f"hypothesis-{decision.anomaly_id}-{len(bucket) + 1}",
                        anomaly_id=decision.anomaly_id,
                        category=decision.category,
                        statement=decision.statement,
                        priority=decision.priority,
                        required_tools=list(dict.fromkeys(decision.required_tools)),
                    )
                )
        # A model may omit a low-priority anomaly. Keep its safe rule-based
        # fallback so every detected anomaly remains eligible for verification.
        fallback_by_anomaly: dict[str, list[InspectionHypothesis]] = {}
        for hypothesis in inspection.hypotheses:
            fallback_by_anomaly.setdefault(hypothesis.anomaly_id, []).append(hypothesis)
        merged: list[InspectionHypothesis] = []
        for anomaly in inspection.anomalies:
            generated = hypotheses_by_anomaly.get(anomaly.anomaly_id)
            if generated:
                merged.extend(sorted(generated, key=lambda item: item.priority))
            else:
                merged.extend(fallback_by_anomaly.get(anomaly.anomaly_id, []))
        warnings = [*inspection.warnings, *model_warnings]
        if len(inspection.anomalies) > self.MAX_LLM_ANOMALIES:
            warnings.append(
                f"异常较多，DeepSeek 优先生成严重程度最高的 {self.MAX_LLM_ANOMALIES} 个异常的候选假设。"
            )
        return inspection.model_copy(update={"hypotheses": merged, "warnings": warnings})


class DeepSeekProblemAttributionAgent:
    """Uses DeepSeek for reasoning after deterministic, bounded evidence retrieval."""

    _UNSUPPORTED_TARGET_COMPARISON_CLAIMS = (
        "竞争加剧",
        "竞争变强",
        "竞争更激烈",
        "转化下降",
        "转化率下降",
    )
    MAX_LLM_ANOMALIES = 20

    def __init__(self, gateway: AdvertisingDataGateway, llm: StructuredLLM) -> None:
        self.evidence_agent = EvidenceBasedProblemAttributionAgent(gateway)
        self.llm = llm

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> AttributionResult:
        inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
        candidate = self.evidence_agent.invoke(request, state)
        # The LLM sees the complete aggregate view even when no fact is yet
        # strong enough to validate. Its output remains a candidate and can
        # only influence the bounded second-round read through allowed tools.
        try:
            output = self.llm.complete(
                system_prompt=ADVERTISING_ATTRIBUTION_SYSTEM_PROMPT,
                context=build_attribution_context(request, inspection, candidate),
                output_model=AttributionLLMOutput,
                max_tokens=4000,
            )
        except LLMError as exc:
            # Attribution facts and fallback causes are deterministic.  The
            # model only improves their narrative; it must not make a long or
            # malformed response prevent the evidence-backed workflow.
            if exc.code not in {"DEEPSEEK_OUTPUT_TRUNCATED", "DEEPSEEK_INVALID_OUTPUT"}:
                raise
            output = AttributionLLMOutput(
                warnings=["DeepSeek 归因解读输出不可用，已保留确定性证据归因供人工复核。"]
            )
        interpretations = self._validate_interpretations(candidate, output)

        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        selected = sorted(
            inspection.anomalies,
            key=lambda item: (severity_rank[item.severity], item.confidence),
            reverse=True,
        )[: self.MAX_LLM_ANOMALIES]
        allowed_anomalies = {item.anomaly_id: item for item in selected}
        candidate_by_anomaly = {
            anomaly_id: cause
            for cause in candidate.causes
            if cause.role == "primary"
            for anomaly_id in cause.anomaly_ids
        }
        findings_by_id = {item.finding_id: item for item in candidate.findings}
        candidate_causes_by_anomaly: dict[str, list[ProblemCause]] = {}
        for item in candidate.causes:
            for anomaly_id in item.anomaly_ids:
                candidate_causes_by_anomaly.setdefault(anomaly_id, []).append(item)
        allowed_evidence = {
            item.evidence_id for item in [*inspection.evidence, *candidate.evidence]
        }
        causes: list[ProblemCause] = []
        seen_anomalies: set[str] = set()
        for decision in output.decisions:
            anomaly = allowed_anomalies.get(decision.anomaly_id)
            if anomaly is None or decision.anomaly_id in seen_anomalies:
                raise LLMError(
                    "DeepSeek advertising attribution referenced an invalid anomaly",
                    code="DEEPSEEK_INVALID_AD_ATTRIBUTION",
                )
            deterministic_cause = candidate_by_anomaly.get(decision.anomaly_id)
            if deterministic_cause is None:
                raise LLMError(
                    "Advertising attribution has no deterministic evidence candidate",
                    code="DEEPSEEK_INVALID_AD_ATTRIBUTION",
                )
            finding_ids = list(dict.fromkeys(decision.finding_ids))
            direct_findings = [
                findings_by_id[item]
                for item in finding_ids
                if item in findings_by_id
                and findings_by_id[item].anomaly_id == decision.anomaly_id
            ]
            if not direct_findings:
                # A model may not convert the fact IDs correctly. Keep the
                # deterministic, evidence-grounded result instead of a fluent
                # but unsupported generic reason.
                continue
            evidence_refs = list(dict.fromkeys([
                *decision.evidence_refs,
                *(ref for item in direct_findings for ref in item.evidence_refs),
            ]))
            if (
                not evidence_refs
                or any(ref not in allowed_evidence for ref in evidence_refs)
            ):
                raise LLMError(
                    "DeepSeek advertising attribution referenced invalid evidence",
                    code="DEEPSEEK_INVALID_AD_EVIDENCE",
                )
            seen_anomalies.add(decision.anomaly_id)
            statement = decision.statement
            primary_finding = direct_findings[0]
            if (
                primary_finding.object_ref
                and primary_finding.object_ref not in statement
            ):
                statement = primary_finding.statement
            if "投放目标" in statement and any(
                claim in statement for claim in self._UNSUPPORTED_TARGET_COMPARISON_CLAIMS
            ):
                statement = deterministic_cause.statement
            causes.append(
                ProblemCause(
                    cause_id=f"cause-{uuid4().hex[:16]}",
                    anomaly_ids=[decision.anomaly_id],
                    category=decision.category,
                    statement=statement,
                    confidence=(
                        min(decision.confidence, 0.65)
                        if deterministic_cause.missing_evidence
                        else decision.confidence
                    ),
                    role="primary",
                    verified=True,
                    finding_refs=[item.finding_id for item in direct_findings[:1]],
                    evidence_refs=evidence_refs,
                    # Evidence availability is determined by the read-only MCP
                    # result and its campaign reference, not by the LLM's
                    # narrative.  This prevents a generic “missing search
                    # terms” statement from being attached to a covered cause.
                    missing_evidence=deterministic_cause.missing_evidence,
                )
            )

            allowed_contributing = [
                findings_by_id[item]
                for item in decision.contributing_finding_ids
                if item in findings_by_id
                and findings_by_id[item].anomaly_id == decision.anomaly_id
                and item != primary_finding.finding_id
            ][:2]
            if not allowed_contributing:
                # Keep deterministic auxiliary factors visible even when the
                # model elects to narrate only the primary finding.
                allowed_contributing = [
                    findings_by_id[item.finding_refs[0]]
                    for item in candidate_causes_by_anomaly.get(decision.anomaly_id, [])
                    if item.role == "contributing"
                    and item.finding_refs
                    and item.finding_refs[0] in findings_by_id
                    and item.finding_refs[0] != primary_finding.finding_id
                ][:2]
            for finding in allowed_contributing:
                if finding.object_ref and finding.object_ref == primary_finding.object_ref:
                    continue
                causes.append(ProblemCause(
                    cause_id=f"cause-{uuid4().hex[:16]}",
                    anomaly_ids=[decision.anomaly_id],
                    category=finding.category,
                    statement=finding.statement,
                    confidence=finding.confidence,
                    role="contributing",
                    verified=True,
                    finding_refs=[finding.finding_id],
                    evidence_refs=finding.evidence_refs,
                ))

        # Preserve deterministic, verified causes when DeepSeek omits an
        # anomaly or fails the fact-reference contract.
        for anomaly_id in allowed_anomalies:
            if anomaly_id in seen_anomalies:
                continue
            causes.extend(candidate_causes_by_anomaly.get(anomaly_id, [])[:3])

        if not causes:
            raise LLMError(
                "DeepSeek advertising attribution returned no usable causes",
                code="DEEPSEEK_EMPTY_AD_ATTRIBUTION",
            )
        attribution_round = state.get("attribution_round", 0)
        warnings = [*candidate.warnings, *output.warnings]
        if any(
            "投放目标" in decision.statement
            and any(claim in decision.statement for claim in self._UNSUPPORTED_TARGET_COMPARISON_CLAIMS)
            for decision in output.decisions
        ):
            warnings.append("已将缺少目标级对比证据的竞争或转化下降表述降级为待验证假设。")
        if len(inspection.anomalies) > len(selected):
            warnings.append(
                f"本次异常较多，DeepSeek 优先归因严重程度最高的 {len(selected)} 个异常。"
            )
        return AttributionResult(
            causes=causes,
            evidence=candidate.evidence,
            findings=candidate.findings,
            needs_more_evidence=(candidate.needs_more_evidence or output.needs_more_evidence)
            and attribution_round < 1,
            requested_tools=list(dict.fromkeys([
                *candidate.requested_tools, *output.suggested_follow_up_tools,
                *output.requested_tools,
            ])),
            warnings=warnings,
            detail_call_quotas=candidate.detail_call_quotas,
            report_summaries=candidate.report_summaries,
            normalized_detail_facts=candidate.normalized_detail_facts,
            attribution_aggregates=candidate.attribution_aggregates,
            llm_interpretations=interpretations,
            validated_findings=candidate.validated_findings,
        )

    @staticmethod
    def _validate_interpretations(
        candidate: AttributionResult, output: AttributionLLMOutput
    ) -> list[LLMInterpretation]:
        """Keep LLM synthesis, but never let it manufacture an auditable fact."""
        fact_index = {
            (item.campaign_id, item.anomaly_id, item.object_type, item.object_name): item
            for item in candidate.normalized_detail_facts
        }
        buckets: dict[tuple[str, str], LLMInterpretation] = {}
        for item in output.candidate_findings:
            key = (item.campaign_id, item.anomaly_id)
            interpretation = buckets.setdefault(key, LLMInterpretation(
                campaign_id=item.campaign_id, anomaly_id=item.anomaly_id
            ))
            fact = fact_index.get((item.campaign_id, item.anomaly_id, item.object_type, item.object_name))
            evidence_ok = bool(item.evidence_refs) and fact is not None and set(item.evidence_refs).issubset({fact.evidence_ref})
            metrics_ok = fact is not None and all(
                getattr(fact, metric, None) == value
                for metric, value in item.metrics.items()
                if metric in {"spend", "sales", "orders", "impressions", "clicks", "acos", "roas", "cpc", "ctr", "cvr", "bid"}
            )
            if evidence_ok and metrics_ok:
                interpretation.candidate_findings.append(item.model_dump(mode="json"))
            else:
                interpretation.rejected_candidates.append(
                    f"候选对象“{item.object_name or item.object_type}”未通过同活动事实或数值校验。"
                )
        for item in output.cross_report_explanations:
            key = (item.campaign_id, item.anomaly_id)
            interpretation = buckets.setdefault(key, LLMInterpretation(
                campaign_id=item.campaign_id, anomaly_id=item.anomaly_id
            ))
            known_refs = {
                fact.evidence_ref for fact in candidate.normalized_detail_facts
                if fact.campaign_id == item.campaign_id and fact.anomaly_id == item.anomaly_id
            }
            if item.evidence_refs and set(item.evidence_refs).issubset(known_refs):
                interpretation.cross_report_explanations.append(item.explanation)
            else:
                interpretation.rejected_candidates.append("跨报告解释引用了不存在或跨活动的证据。")
        # Global signals are retained only as non-actionable interpretation
        # notes; they cannot enter validated_findings or the strategy input.
        for interpretation in buckets.values():
            interpretation.suspected_patterns.extend(output.suspected_patterns[:10])
            interpretation.missing_evidence.extend(output.missing_evidence[:10])
            interpretation.suggested_follow_up_tools.extend(output.suggested_follow_up_tools)
        return list(buckets.values())


class DeepSeekStrategyRecommendationAgent:
    """Generates proposals with DeepSeek, then enforces deterministic guardrails."""

    _READ_ONLY_ACTIONS = {"observe", "optimize_listing", "check_inventory"}
    _LOW_RISK_ACTIONS = {"adjust_bid"}

    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> StrategyResult:
        inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
        attribution = AttributionResult.model_validate(state.get("attribution", {}))
        if not any(
            item.verified and item.role == "primary" for item in attribution.causes
        ):
            return StrategyResult(
                warnings=["未取得已验证的归因结论，未生成广告调整建议，等待人工复核。"]
            )
        output = self.llm.complete(
            system_prompt=ADVERTISING_STRATEGY_SYSTEM_PROMPT,
            context=build_strategy_context(request, inspection, attribution),
            output_model=StrategyLLMOutput,
            max_tokens=6000,
        )
        cause_by_id = {
            item.cause_id: item
            for item in attribution.causes[:30]
            if item.verified and item.role == "primary"
        }
        anomaly_by_id = {item.anomaly_id: item for item in inspection.anomalies}
        recommendations: list[StrategyRecommendation] = []
        warnings = list(output.warnings)
        seen_causes: set[str] = set()

        for decision in output.decisions:
            cause = cause_by_id.get(decision.cause_id)
            if cause is None or decision.cause_id in seen_causes:
                warnings.append("一条策略建议引用了无效或重复的归因，已忽略该建议。")
                continue
            evidence_refs = list(dict.fromkeys(decision.evidence_refs))
            if not evidence_refs or any(ref not in cause.evidence_refs for ref in evidence_refs):
                warnings.append("一条策略建议引用了未验证证据，已降级为待人工复核。")
                continue
            anomaly = next(
                (anomaly_by_id[item] for item in cause.anomaly_ids if item in anomaly_by_id),
                None,
            )
            if anomaly is None:
                warnings.append("一条策略建议没有可验证的广告活动目标，已忽略该建议。")
                continue

            action_type = decision.action_type
            title = decision.title
            proposed_change = dict(decision.proposed_change)
            if cause.missing_evidence and action_type not in self._READ_ONLY_ACTIONS:
                action_type = "observe"
                title = "补齐证据后再决定广告调整"
                proposed_change = {"review_after_days": 3}
                warnings.append(f"{decision.cause_id} 证据不足，写操作建议已降级为观察。")
            proposed_change, action_type, title = self._guard_change(
                action_type, proposed_change, title
            )
            risk_level = (
                "read_only"
                if action_type in self._READ_ONLY_ACTIONS
                else "low_risk_write"
                if action_type in self._LOW_RISK_ACTIONS
                else "high_risk_write"
            )
            seen_causes.add(decision.cause_id)
            recommendations.append(
                StrategyRecommendation(
                    recommendation_id=f"recommendation-{uuid4().hex[:16]}",
                    cause_ids=[decision.cause_id],
                    action_type=action_type,
                    target=anomaly.entity,
                    title=title,
                    rationale=decision.rationale,
                    proposed_change=proposed_change,
                    expected_effect=decision.expected_effect,
                    risk_level=risk_level,
                    evidence_refs=evidence_refs,
                )
            )
        return StrategyResult(recommendations=recommendations, warnings=warnings)

    @staticmethod
    def _guard_change(
        action_type: str,
        proposed_change: dict[str, Any],
        title: str,
    ) -> tuple[dict[str, Any], str, str]:
        if action_type == "adjust_bid":
            value = DeepSeekStrategyRecommendationAgent._number(
                proposed_change.get("bid_change_percent")
            )
            if value is None or value == 0:
                return {"review_after_days": 3}, "observe", "竞价建议缺少安全幅度，转为观察"
            return {"bid_change_percent": max(-30, min(30, value))}, action_type, title
        if action_type == "adjust_budget":
            value = DeepSeekStrategyRecommendationAgent._number(
                proposed_change.get("budget_change_percent")
            )
            if value is None or value == 0:
                return {"review_after_days": 3}, "observe", "预算建议缺少安全幅度，转为观察"
            return {"budget_change_percent": max(-20, min(30, value))}, action_type, title
        if action_type == "observe":
            days = DeepSeekStrategyRecommendationAgent._number(
                proposed_change.get("review_after_days")
            )
            return {"review_after_days": max(1, min(14, days or 3))}, action_type, title
        return proposed_change, action_type, title

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

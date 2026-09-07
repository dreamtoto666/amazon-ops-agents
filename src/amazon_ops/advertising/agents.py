from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from .gateway import AdvertisingDataGateway, AdvertisingReport
from .models import (
    AdDiagnosticRequest,
    AdEntityRef,
    AdEntityType,
    AdMetricSnapshot,
    AnomalyType,
    AttributionResult,
    AttributionAggregate,
    AttributionFinding,
    CampaignReportAnomalySummary,
    DataInspectionResult,
    DetectedAnomaly,
    DetailCallQuota,
    DetailReportSummary,
    DiagnosticEvidence,
    InspectionHypothesis,
    NormalizedDetailFact,
    OperationsTodo,
    ProblemCause,
    ReviewTodoResult,
    SearchTermEvidence,
    StrategyRecommendation,
    StrategyResult,
    TargetEvidence,
    TargetPeriodComparison,
    TodoDetail,
)
from .state import AdvertisingDiagnosticState


class RuleBasedDataInspectionAgent:
    """Fetch campaign reports and detect anomalies with auditable rules."""

    _DEFAULT_TARGET_ACOS = {"profit": 0.35, "balanced": 0.50, "scale": 0.70}
    _DEFAULT_MIN_CTR = {"profit": 0.0025, "balanced": 0.0025, "scale": 0.0020}
    _MIN_CTR_IMPRESSIONS = 1000
    _HYPOTHESIS_TEMPLATES = {
        AnomalyType.ZERO_ORDER_WASTE: [
            ("conversion", "存在高消耗无订单流量，需要定位具体搜索词或投放目标", "ad_campaign_search_term_report"),
            ("product", "需排除广告商品的投放对象匹配不当", "ad_campaign_targeting_report"),
        ],
        AnomalyType.ACOS_RISE: [
            ("cost", "广告效率偏低，需要验证高成本关键词或投放目标", "ad_campaign_keyword_report"),
            ("conversion", "需验证是否由高消耗无转化流量导致", "ad_campaign_search_term_report"),
        ],
        AnomalyType.ROAS_DROP: [("conversion", "广告产出偏低，需要验证转化流量", "ad_campaign_search_term_report")],
        AnomalyType.CPC_RISE: [("cost", "点击成本偏高，需要验证关键词或投放目标竞价", "ad_campaign_keyword_report")],
        AnomalyType.CTR_DROP: [("traffic", "点击率偏低，需要验证投放相关性", "ad_campaign_targeting_report")],
        AnomalyType.CVR_DROP: [("conversion", "转化率偏低，需要定位无转化流量", "ad_campaign_search_term_report")],
        AnomalyType.SPEND_SPIKE: [("cost", "花费异常增加，需要定位高消耗对象", "ad_campaign_keyword_report")],
        AnomalyType.SALES_DROP: [("conversion", "广告销售下降，需要验证搜索词和投放产出", "ad_campaign_search_term_report")],
        AnomalyType.ORDERS_DROP: [("conversion", "广告订单下降，需要验证搜索词和投放产出", "ad_campaign_search_term_report")],
        AnomalyType.DELIVERY_DROP: [("traffic", "投放量下降，需要验证广告组投放状态", "ad_campaign_group_report")],
        AnomalyType.BUDGET_CONSTRAINED: [("traffic", "可能受预算或广告组设置约束", "ad_campaign_group_report")],
    }

    def __init__(self, gateway: AdvertisingDataGateway) -> None:
        self.gateway = gateway

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> DataInspectionResult:
        current = self._campaign_report(request, request.current_period)
        current_evidence = self._evidence(current, period="current", state=state)
        current_snapshots = self._snapshots(current.rows, period="current")
        baseline_snapshots: list[AdMetricSnapshot] = []
        baseline_evidence: DiagnosticEvidence | None = None
        if request.baseline_period:
            baseline = self._campaign_report(request, request.baseline_period)
            baseline_evidence = self._evidence(baseline, period="baseline", state=state)
            baseline_snapshots = self._snapshots(baseline.rows, period="baseline")
        baseline_by_entity = {
            self._entity_key(item): item for item in baseline_snapshots
        }
        anomalies: list[DetectedAnomaly] = []
        for snapshot in current_snapshots:
            previous = baseline_by_entity.get(self._entity_key(snapshot))
            anomalies.extend(
                self._detect(
                    request=request,
                    current=snapshot,
                    baseline=previous,
                    evidence_refs=[
                        current_evidence.evidence_id,
                        *(
                            [baseline_evidence.evidence_id]
                            if baseline_evidence
                            else []
                        ),
                    ],
                )
            )
        warnings: list[str] = []
        if not request.baseline_period:
            warnings.append("当前周期体检模式：未启用基准周期趋势对比。")
        if current.total > len(current.rows):
            warnings.append("广告活动数量超过单次查询上限，本次先分析花费最高的 200 个活动。")
        return DataInspectionResult(
            snapshots=[*current_snapshots, *baseline_snapshots],
            anomalies=anomalies,
            hypotheses=self._hypotheses(anomalies),
            evidence=[
                current_evidence,
                *([baseline_evidence] if baseline_evidence else []),
            ],
            warnings=warnings,
        )

    def _campaign_report(self, request: AdDiagnosticRequest, period: DiagnosticPeriod) -> AdvertisingReport:
        kwargs: dict[str, Any] = {"profile_ids": request.profile_ids, "period": period, "campaign_ids": request.campaign_ids}
        if request.asins:
            kwargs["asins"] = request.asins
        try:
            return self.gateway.campaign_report(**kwargs)
        except TypeError as exc:
            # Compatibility for test doubles and non-migrated read-only MCP
            # gateways. They are never used when a selected product scope exists.
            if request.asins:
                raise RuntimeError("当前广告数据源无法验证所选产品范围。") from exc
            raise

    @classmethod
    def _hypotheses(cls, anomalies: list[DetectedAnomaly]) -> list[InspectionHypothesis]:
        hypotheses: list[InspectionHypothesis] = []
        for anomaly in anomalies:
            for priority, (category, statement, tool) in enumerate(
                cls._HYPOTHESIS_TEMPLATES.get(anomaly.anomaly_type, []), start=1
            ):
                hypotheses.append(InspectionHypothesis(
                    hypothesis_id=f"hypothesis-{anomaly.anomaly_id}-{priority}",
                    anomaly_id=anomaly.anomaly_id,
                    category=category,
                    statement=statement,
                    priority=priority,
                    required_tools=[tool],
                ))
        return hypotheses

    @staticmethod
    def _entity_key(snapshot: AdMetricSnapshot) -> tuple[str, str]:
        return snapshot.entity.profile_id, snapshot.entity.entity_id

    def _snapshots(
        self, rows: list[dict[str, Any]], *, period: str
    ) -> list[AdMetricSnapshot]:
        snapshots: list[AdMetricSnapshot] = []
        for row in rows:
            campaign_id = self._text(row.get("campaign_id"))
            profile_id = self._text(row.get("profile_id"))
            if not campaign_id or not profile_id:
                continue
            impressions = self._number(row.get("impressions"))
            clicks = self._number(row.get("clicks"))
            spend = self._number(row.get("spends"))
            sales = self._number(row.get("sales"))
            orders = self._number(row.get("orders"))
            snapshots.append(
                AdMetricSnapshot(
                    entity=AdEntityRef(
                        entity_type=AdEntityType.CAMPAIGN,
                        entity_id=campaign_id,
                        name=self._text(row.get("name")),
                        profile_id=profile_id,
                        campaign_id=campaign_id,
                    ),
                    period=period,
                    impressions=impressions,
                    clicks=clicks,
                    spend=spend,
                    sales=sales,
                    orders=orders,
                    ad_units=self._number(row.get("ad_units")),
                    ctr=self._ratio(clicks, impressions, row.get("ctr")),
                    cpc=self._ratio(spend, clicks, row.get("cpc")),
                    cvr=self._ratio(orders, clicks, row.get("cvr")),
                    acos=self._ratio(spend, sales, row.get("acos")),
                    roas=self._ratio(sales, spend, row.get("roas")),
                    budget=self._optional_number(row.get("daily_budget") or row.get("budget")),
                )
            )
        return snapshots

    def _detect(
        self,
        *,
        request: AdDiagnosticRequest,
        current: AdMetricSnapshot,
        baseline: AdMetricSnapshot | None,
        evidence_refs: list[str],
    ) -> list[DetectedAnomaly]:
        threshold = request.thresholds
        anomalies: list[DetectedAnomaly] = []
        sample_ok = current.spend >= threshold.min_spend or current.clicks >= threshold.min_clicks
        if (
            current.spend >= threshold.min_spend
            and current.clicks >= threshold.zero_order_clicks
            and current.orders == 0
        ):
            anomalies.append(
                self._anomaly(
                    current,
                    AnomalyType.ZERO_ORDER_WASTE,
                    "orders",
                    current.orders,
                    baseline.orders if baseline else None,
                    evidence_refs,
                    severity="high" if current.spend < threshold.min_spend * 3 else "critical",
                    confidence=0.92,
                )
            )
        if not baseline:
            self._detect_current_period_efficiency(
                request=request,
                current=current,
                threshold=threshold,
                evidence_refs=evidence_refs,
                anomalies=anomalies,
            )
            return anomalies
        if not sample_ok:
            return anomalies

        relative = threshold.relative_change
        rules = [
            (AnomalyType.SPEND_SPIKE, "spend", current.spend, baseline.spend, "rise"),
            (AnomalyType.SALES_DROP, "sales", current.sales, baseline.sales, "drop"),
            (AnomalyType.ORDERS_DROP, "orders", current.orders, baseline.orders, "drop"),
            (AnomalyType.ACOS_RISE, "acos", current.acos, baseline.acos, "rise"),
            (AnomalyType.ROAS_DROP, "roas", current.roas, baseline.roas, "drop"),
            (AnomalyType.CPC_RISE, "cpc", current.cpc, baseline.cpc, "rise"),
            (AnomalyType.CTR_DROP, "ctr", current.ctr, baseline.ctr, "drop"),
            (AnomalyType.CVR_DROP, "cvr", current.cvr, baseline.cvr, "drop"),
            (
                AnomalyType.DELIVERY_DROP,
                "impressions",
                current.impressions,
                baseline.impressions,
                "drop",
            ),
        ]
        for anomaly_type, metric, current_value, baseline_value, direction in rules:
            if current_value is None or baseline_value is None or baseline_value <= 0:
                continue
            change = (current_value - baseline_value) / baseline_value
            triggered = change >= relative if direction == "rise" else change <= -relative
            if not triggered:
                continue
            magnitude = abs(change)
            anomalies.append(
                self._anomaly(
                    current,
                    anomaly_type,
                    metric,
                    current_value,
                    baseline_value,
                    evidence_refs,
                    severity=self._severity(magnitude),
                    confidence=0.86 if current.clicks >= threshold.min_clicks * 2 else 0.72,
                )
            )
        return anomalies

    def _detect_current_period_efficiency(
        self,
        *,
        request: AdDiagnosticRequest,
        current: AdMetricSnapshot,
        threshold: Any,
        evidence_refs: list[str],
        anomalies: list[DetectedAnomaly],
    ) -> None:
        """Apply absolute guardrails when no comparison period is requested.

        ROAS is calculated for every snapshot but is the inverse of ACOS, so it
        is shown as supporting context instead of creating a duplicate alert.
        CPC is deliberately display-only until a store-specific ceiling exists.
        """
        goal = request.goal.growth_priority
        target_acos = request.goal.target_acos or self._DEFAULT_TARGET_ACOS[goal]
        min_ctr = self._DEFAULT_MIN_CTR[goal]

        if (
            current.spend >= threshold.min_spend
            and current.sales > 0
            and current.acos is not None
            and current.acos >= target_acos
        ):
            magnitude = current.acos / target_acos - 1
            anomalies.append(
                self._anomaly(
                    current,
                    AnomalyType.ACOS_RISE,
                    "acos",
                    current.acos,
                    None,
                    evidence_refs,
                    severity=self._severity(magnitude),
                    confidence=0.86 if current.clicks >= threshold.min_clicks * 2 else 0.72,
                )
            )

        if (
            current.impressions >= self._MIN_CTR_IMPRESSIONS
            and current.ctr is not None
            and current.ctr < min_ctr
        ):
            magnitude = (min_ctr - current.ctr) / min_ctr
            anomalies.append(
                self._anomaly(
                    current,
                    AnomalyType.CTR_DROP,
                    "ctr",
                    current.ctr,
                    None,
                    evidence_refs,
                    severity=self._severity(magnitude),
                    confidence=0.82,
                )
            )

    @staticmethod
    def _anomaly(
        snapshot: AdMetricSnapshot,
        anomaly_type: AnomalyType,
        metric: str,
        current_value: float | None,
        baseline_value: float | None,
        evidence_refs: list[str],
        *,
        severity: str,
        confidence: float,
    ) -> DetectedAnomaly:
        absolute_change = None
        relative_change = None
        if current_value is not None and baseline_value is not None:
            absolute_change = current_value - baseline_value
            if baseline_value:
                relative_change = absolute_change / baseline_value
        return DetectedAnomaly(
            anomaly_id=f"anomaly-{uuid4().hex[:16]}",
            anomaly_type=anomaly_type,
            entity=snapshot.entity,
            metric=metric,
            current_value=current_value,
            baseline_value=baseline_value,
            absolute_change=absolute_change,
            relative_change=relative_change,
            severity=severity,
            confidence=confidence,
            evidence_refs=evidence_refs,
        )

    @staticmethod
    def _severity(magnitude: float) -> str:
        if magnitude >= 1:
            return "critical"
        if magnitude >= 0.6:
            return "high"
        if magnitude >= 0.4:
            return "medium"
        return "low"

    @staticmethod
    def _evidence(
        report: AdvertisingReport,
        *,
        period: str,
        state: AdvertisingDiagnosticState,
    ) -> DiagnosticEvidence:
        entity_refs = [
            str(row["campaign_id"])
            for row in report.rows
            if row.get("campaign_id") is not None
        ]
        return DiagnosticEvidence(
            evidence_id=f"evidence-{uuid4().hex[:16]}",
            trace_id=str(state["trace_id"]),
            span_id=str(state["span_id"]),
            stage=str(state["stage"]),
            tool=report.tool,
            query={**report.arguments, "period_role": period},
            fetched_at=report.trace.finished_at,
            record_count=len(entity_refs),
            entity_refs=entity_refs,
            high_spend_zero_order_terms=RuleBasedDataInspectionAgent._search_term_examples(report),
            high_spend_zero_order_targets=RuleBasedDataInspectionAgent._target_examples(report),
            artifact_id=report.trace.call_id,
        )

    @classmethod
    def _search_term_examples(cls, report: AdvertisingReport) -> list[SearchTermEvidence]:
        if report.tool != "ad_campaign_search_term_report":
            return []
        examples: list[SearchTermEvidence] = []
        for row in report.rows:
            term = cls._text(
                row.get("search_term")
                or row.get("search_terms")
                or row.get("customer_search_term")
                or row.get("query")
                or row.get("term")
            )
            spend = cls._number(row.get("spends") or row.get("spend"))
            sales = cls._number(row.get("sales"))
            orders = cls._number(row.get("orders"))
            if not term or spend <= 0 or sales > 0 or orders > 0:
                continue
            examples.append(
                SearchTermEvidence(
                    term=term,
                    spend=spend,
                    sales=sales,
                    orders=orders,
                    acos=None,
                )
            )
        return sorted(examples, key=lambda item: item.spend, reverse=True)[:5]

    @classmethod
    def _target_examples(cls, report: AdvertisingReport) -> list[TargetEvidence]:
        kind = (
            "keyword"
            if report.tool == "ad_campaign_keyword_report"
            else "targeting"
            if report.tool == "ad_campaign_targeting_report"
            else None
        )
        if kind is None:
            return []
        examples: list[TargetEvidence] = []
        for row in report.rows:
            text = cls._text(
                row.get("keyword")
                or row.get("keyword_text")
                or row.get("targeting")
                or row.get("targeting_text")
                or row.get("target")
                or row.get("target_name")
            )
            spend = cls._number(row.get("spends") or row.get("spend"))
            sales = cls._number(row.get("sales"))
            orders = cls._number(row.get("orders"))
            if not text or spend <= 0 or sales > 0 or orders > 0:
                continue
            examples.append(
                TargetEvidence(
                    kind=kind,
                    text=text,
                    spend=spend,
                    sales=sales,
                    orders=orders,
                    bid=cls._optional_number(row.get("bid") or row.get("bidding")),
                )
            )
        return sorted(examples, key=lambda item: item.spend, reverse=True)[:5]

    @classmethod
    def _target_period_comparisons(
        cls, current: AdvertisingReport, baseline: AdvertisingReport
    ) -> list[TargetPeriodComparison]:
        baseline_by_key = {
            key: row
            for row in baseline.rows
            if (key := cls._target_key(row)) is not None
        }
        comparisons: list[TargetPeriodComparison] = []
        for row in current.rows:
            key = cls._target_key(row)
            previous = baseline_by_key.get(key) if key else None
            campaign_id = cls._text(row.get("campaign_id"))
            if not key or not previous or not campaign_id:
                continue
            clicks = cls._number(row.get("clicks"))
            spend = cls._number(row.get("spends") or row.get("spend"))
            orders = cls._number(row.get("orders"))
            previous_clicks = cls._number(previous.get("clicks"))
            previous_spend = cls._number(previous.get("spends") or previous.get("spend"))
            previous_orders = cls._number(previous.get("orders"))
            comparisons.append(
                TargetPeriodComparison(
                    campaign_id=campaign_id,
                    target_key=key,
                    current_cpc=cls._ratio(spend, clicks, None),
                    baseline_cpc=cls._ratio(previous_spend, previous_clicks, None),
                    current_cvr=cls._ratio(orders, clicks, None),
                    baseline_cvr=cls._ratio(previous_orders, previous_clicks, None),
                    current_bid=cls._optional_number(row.get("bid") or row.get("bidding")),
                    baseline_bid=cls._optional_number(previous.get("bid") or previous.get("bidding")),
                )
            )
        return comparisons

    @classmethod
    def _target_key(cls, row: dict[str, Any]) -> str | None:
        target = cls._text(
            row.get("targeting_id")
            or row.get("target_id")
            or row.get("keyword_id")
            or row.get("targeting")
            or row.get("targeting_text")
            or row.get("target")
        )
        campaign_id = cls._text(row.get("campaign_id"))
        return f"{campaign_id}:{target}" if campaign_id and target else None

    @classmethod
    def _ratio(cls, numerator: float, denominator: float, fallback: Any) -> float | None:
        if denominator > 0:
            return numerator / denominator
        return cls._optional_number(fallback)

    @classmethod
    def _number(cls, value: Any) -> float:
        return cls._optional_number(value) or 0.0

    @staticmethod
    def _optional_number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            if isinstance(value, str):
                cleaned = value.strip().replace(",", "")
                if not cleaned:
                    return None
                if cleaned.endswith("%"):
                    return float(cleaned[:-1]) / 100
                return float(cleaned)
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class EvidenceBasedProblemAttributionAgent:
    # A run investigates the same priority campaigns that DeepSeek receives for
    # attribution. Each report type gets an independent budget in each round.
    MAX_FOCUS_CAMPAIGNS = 30
    DETAIL_CALL_LIMIT = 30
    BATCH_SIZE = 5
    MAX_REPORT_CONCURRENCY = 4
    DETAIL_TOOLS = (
        "ad_campaign_search_term_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_group_report",
    )

    def __init__(self, gateway: AdvertisingDataGateway) -> None:
        self.gateway = gateway

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> AttributionResult:
        inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
        previous = AttributionResult.model_validate(state.get("attribution", {}))
        selected = self._focus_anomalies(inspection.anomalies)
        hypotheses_by_anomaly: dict[str, list[InspectionHypothesis]] = {}
        for hypothesis in inspection.hypotheses:
            hypotheses_by_anomaly.setdefault(hypothesis.anomaly_id, []).append(hypothesis)
        primary_requests: dict[str, list[str]] = {}
        for anomaly in selected:
            campaign_id = anomaly.entity.campaign_id
            hypotheses = sorted(
                hypotheses_by_anomaly.get(anomaly.anomaly_id, []),
                key=lambda item: item.priority,
            )
            if campaign_id and hypotheses:
                primary_requests.setdefault(hypotheses[0].required_tools[0], []).append(campaign_id)

        # First cover every priority campaign with the most relevant report,
        # then use the independent report budgets to deepen the same campaigns.
        secondary_requests: dict[str, list[str]] = {}
        primary_pairs = {
            (tool, campaign_id)
            for tool, campaign_ids in primary_requests.items()
            for campaign_id in campaign_ids
        }
        for anomaly in selected:
            campaign_id = anomaly.entity.campaign_id
            if not campaign_id:
                continue
            # Four complementary reports can all provide direct evidence. Do
            # not invent a request merely to fill a quota: each is only a
            # second-priority request after the anomaly's primary report.
            for tool in self.DETAIL_TOOLS:
                if (tool, campaign_id) not in primary_pairs:
                    secondary_requests.setdefault(tool, []).append(campaign_id)

        # The second round is deliberately a gap-fill. It never repeats a
        # completed query and only touches unresolved campaigns or dimensions
        # the model explicitly requested after seeing round-one facts.
        follow_up_requests: dict[str, list[str]] = {}
        selected_campaign_ids = [
            anomaly.entity.campaign_id
            for anomaly in selected
            if anomaly.entity.campaign_id
        ]
        previous_findings_by_campaign = {item.campaign_id for item in previous.findings}
        unresolved_campaign_ids = [
            anomaly.entity.campaign_id
            for anomaly in selected
            if anomaly.entity.campaign_id
            and anomaly.entity.campaign_id not in previous_findings_by_campaign
        ]
        if state.get("attribution_round", 0) > 0:
            gap_campaign_ids = unresolved_campaign_ids or selected_campaign_ids
            requested_tools = [tool for tool in previous.requested_tools if tool in self.DETAIL_TOOLS]
            for tool in requested_tools or self.DETAIL_TOOLS:
                follow_up_requests[tool] = gap_campaign_ids

        previously_queried = self._queried_pairs(previous.evidence)

        # Attribution may take a second evidence round.  Preserve the first
        # round's records so the next round validates against them rather than
        # treating already-fetched data as missing.
        detail_evidence: list[DiagnosticEvidence] = list(previous.evidence)
        findings: list[AttributionFinding] = list(previous.findings)
        normalized_facts: list[NormalizedDetailFact] = list(previous.normalized_detail_facts)
        warnings: list[str] = list(previous.warnings)
        attribution_round = state.get("attribution_round", 0)
        ledger = self._quota_ledger(state)
        round_number = attribution_round + 1
        # Persist zero-use buckets too: operators can distinguish “not needed”
        # from a report type that was accidentally omitted from scheduling.
        for tool in self.DETAIL_TOOLS:
            self._quota_bucket(ledger, round_number, tool)
        request_sets = (
            (primary_requests, secondary_requests, {})
            if attribution_round == 0
            else ({}, {}, follow_up_requests)
        )
        batches = self._detail_batches(*request_sets, previously_queried)
        current_reports = self._execute_detail_batches(
            batches=batches,
            request=request,
            state=state,
            ledger=ledger,
            round_number=round_number,
            warnings=warnings,
        )
        for tool, campaign_ids, report in current_reports:
            evidence = RuleBasedDataInspectionAgent._evidence(report, period="current", state=state)
            normalized_facts.extend(self._normalize_detail_facts(
                report=report, evidence=evidence, period="current", anomalies=inspection.anomalies
            ))
            if tool == "ad_campaign_targeting_report" and request.baseline_period is not None:
                baseline = self._fetch_detail_report(
                    tool=tool,
                    campaign_ids=campaign_ids,
                    request=request,
                    period=request.baseline_period,
                    ledger=ledger,
                    round_number=round_number,
                    warnings=warnings,
                )
                if baseline is not None:
                    baseline_evidence = RuleBasedDataInspectionAgent._evidence(baseline, period="baseline", state=state)
                    detail_evidence.append(baseline_evidence)
                    normalized_facts.extend(self._normalize_detail_facts(
                        report=baseline, evidence=baseline_evidence, period="baseline", anomalies=inspection.anomalies
                    ))
                    evidence = evidence.model_copy(update={
                        "target_period_comparisons": RuleBasedDataInspectionAgent._target_period_comparisons(report, baseline)
                    })
                else:
                    warnings.append("投放目标基准周期明细未取得，趋势性结论已降级为待验证假设。")
            detail_evidence.append(evidence)
            findings.extend(self._findings_from_report(
                report=report, evidence=evidence, anomalies=inspection.anomalies, request=request
            ))

        if len(inspection.anomalies) > len(selected):
            warnings.append(
                f"本轮明细下钻优先覆盖 {len(selected)} 个重点广告活动，"
                f"每类报告每轮最多调用 {self.DETAIL_CALL_LIMIT} 次领星明细接口。"
            )

        normalized_facts = self._deduplicate_facts(normalized_facts)
        aggregates = self._build_aggregates(normalized_facts)

        causes: list[ProblemCause] = []
        findings_by_anomaly: dict[str, list[AttributionFinding]] = {}
        for finding in findings:
            findings_by_anomaly.setdefault(finding.anomaly_id, []).append(finding)
        unresolved: list[DetectedAnomaly] = []
        for anomaly in inspection.anomalies:
            campaign_id = anomaly.entity.campaign_id
            hypotheses = sorted(
                hypotheses_by_anomaly.get(anomaly.anomaly_id, []),
                key=lambda item: item.priority,
            )
            selected_hypothesis = hypotheses[0] if hypotheses else None
            verified = sorted(
                findings_by_anomaly.get(anomaly.anomaly_id, []),
                key=lambda item: (item.contribution, item.confidence),
                reverse=True,
            )
            if not verified:
                unresolved.append(anomaly)
                missing = [
                    f"需要复核 {tool} 明细"
                    for hypothesis in hypotheses
                    for tool in hypothesis.required_tools
                ] or ["未取得可直接支持原因的广告明细"]
                causes.append(ProblemCause(
                    cause_id=f"cause-{uuid4().hex[:16]}",
                    anomaly_ids=[anomaly.anomaly_id],
                    category=selected_hypothesis.category if selected_hypothesis else "unknown",
                    statement="未取得可直接支持该异常原因的明细证据，暂不输出归因结论。",
                    confidence=0.0,
                    verified=False,
                    evidence_refs=list(anomaly.evidence_refs),
                    missing_evidence=list(dict.fromkeys(missing)),
                ))
                continue
            primary = verified[0]
            causes.append(ProblemCause(
                cause_id=f"cause-{uuid4().hex[:16]}",
                anomaly_ids=[anomaly.anomaly_id],
                category=primary.category,
                statement=primary.statement,
                confidence=primary.confidence,
                role="primary",
                verified=True,
                finding_refs=[primary.finding_id],
                evidence_refs=primary.evidence_refs,
            ))
            used_objects = {primary.object_ref}
            for finding in verified[1:]:
                if len([item for item in causes if anomaly.anomaly_id in item.anomaly_ids]) >= 3:
                    break
                if finding.object_ref in used_objects:
                    continue
                used_objects.add(finding.object_ref)
                causes.append(ProblemCause(
                    cause_id=f"cause-{uuid4().hex[:16]}",
                    anomaly_ids=[anomaly.anomaly_id],
                    category=finding.category,
                    statement=finding.statement,
                    confidence=finding.confidence,
                    role="contributing",
                    verified=True,
                    finding_refs=[finding.finding_id],
                    evidence_refs=finding.evidence_refs,
                ))
        follow_up_tools = list(dict.fromkeys(
            tool
            for anomaly in unresolved
            for hypothesis in hypotheses_by_anomaly.get(anomaly.anomaly_id, [])
            for tool in hypothesis.required_tools
            if tool not in primary_requests
        ))
        return AttributionResult(
            causes=causes,
            evidence=detail_evidence,
            findings=findings,
            needs_more_evidence=bool(unresolved and attribution_round == 0),
            requested_tools=follow_up_tools,
            warnings=warnings,
            detail_call_quotas=self._quota_entries(ledger),
            report_summaries=self._report_summaries(
                evidence=detail_evidence,
                findings=findings,
                warnings=warnings,
            ),
            normalized_detail_facts=normalized_facts,
            attribution_aggregates=aggregates,
            validated_findings=findings,
        )

    @classmethod
    def _report_summaries(
        cls,
        *,
        evidence: list[DiagnosticEvidence],
        findings: list[AttributionFinding],
        warnings: list[str],
    ) -> dict[str, DetailReportSummary]:
        summaries: dict[str, DetailReportSummary] = {}
        for tool in cls.DETAIL_TOOLS:
            related_evidence = [item for item in evidence if item.tool == tool]
            campaign_evidence: dict[str, list[DiagnosticEvidence]] = {}
            for item in related_evidence:
                campaign_ids = [value for value in item.entity_refs if value]
                fallback_campaign_ids = item.query.get("campaign_id")
                if isinstance(fallback_campaign_ids, str):
                    campaign_ids.append(fallback_campaign_ids)
                elif isinstance(fallback_campaign_ids, list):
                    campaign_ids.extend(str(value) for value in fallback_campaign_ids if value)
                for campaign_id in set(campaign_ids):
                    campaign_evidence.setdefault(campaign_id, []).append(item)

            campaign_anomalies: list[CampaignReportAnomalySummary] = []
            for campaign_id, items in sorted(campaign_evidence.items()):
                evidence_refs = {item.evidence_id for item in items}
                campaign_findings = sorted(
                    [
                        item for item in findings
                        if item.campaign_id == campaign_id
                        and any(ref in evidence_refs for ref in item.evidence_refs)
                    ],
                    key=lambda item: (item.contribution, item.confidence),
                    reverse=True,
                )[:10]
                campaign_anomalies.append(CampaignReportAnomalySummary(
                    campaign_id=campaign_id,
                    anomaly_ids=sorted({item.anomaly_id for item in campaign_findings}),
                    status="finding_detected" if campaign_findings else "no_direct_finding",
                    evidence_refs=sorted(evidence_refs),
                    findings=campaign_findings,
                ))
            summaries[tool] = DetailReportSummary(
                tool=tool,
                campaign_anomalies=campaign_anomalies,
                warnings=[warning for warning in warnings if tool in warning],
            )
        return summaries

    @classmethod
    def _normalize_detail_facts(
        cls,
        *,
        report: AdvertisingReport,
        evidence: DiagnosticEvidence,
        period: str,
        anomalies: list[DetectedAnomaly],
    ) -> list[NormalizedDetailFact]:
        """Keep every parsable business row; thresholds are applied only later."""
        object_types = {
            "ad_campaign_search_term_report": "search_term",
            "ad_campaign_keyword_report": "keyword",
            "ad_campaign_targeting_report": "targeting",
            "ad_campaign_group_report": "ad_group",
        }
        object_type = object_types.get(report.tool, "unknown")
        anomalies_by_campaign: dict[str, list[DetectedAnomaly]] = {}
        for anomaly in anomalies:
            if anomaly.entity.campaign_id:
                anomalies_by_campaign.setdefault(anomaly.entity.campaign_id, []).append(anomaly)
        queried = evidence.query.get("campaign_id")
        fallback = queried[0] if isinstance(queried, list) and len(queried) == 1 else queried if isinstance(queried, str) else None
        facts: list[NormalizedDetailFact] = []
        for index, row in enumerate(report.rows):
            campaign_id = RuleBasedDataInspectionAgent._text(row.get("campaign_id")) or fallback
            if not campaign_id or campaign_id not in anomalies_by_campaign:
                continue
            object_name = RuleBasedDataInspectionAgent._text(
                row.get("search_term") or row.get("search_terms") or row.get("customer_search_term")
                or row.get("keyword") or row.get("keyword_text") or row.get("targeting")
                or row.get("targeting_text") or row.get("target") or row.get("target_name")
                or row.get("ad_group_name") or row.get("ad_group_id")
            )
            values = {
                "spend": RuleBasedDataInspectionAgent._optional_number(row.get("spends") or row.get("spend")),
                "sales": RuleBasedDataInspectionAgent._optional_number(row.get("sales")),
                "orders": RuleBasedDataInspectionAgent._optional_number(row.get("orders")),
                "impressions": RuleBasedDataInspectionAgent._optional_number(row.get("impressions")),
                "clicks": RuleBasedDataInspectionAgent._optional_number(row.get("clicks")),
                "bid": RuleBasedDataInspectionAgent._optional_number(row.get("bid") or row.get("bidding")),
            }
            # A row carrying neither an object nor any usable business value is
            # malformed, rather than merely low-performing.
            if object_name is None and all(value is None for value in values.values()):
                continue
            spend, sales, orders, impressions, clicks, bid = (values[key] for key in values)
            acos = spend / sales if spend is not None and sales and sales > 0 else None
            roas = sales / spend if sales is not None and spend and spend > 0 else None
            cpc = spend / clicks if spend is not None and clicks and clicks > 0 else None
            ctr = clicks / impressions if clicks is not None and impressions and impressions > 0 else None
            cvr = orders / clicks if orders is not None and clicks and clicks > 0 else None
            missing = [key for key, value in {
                **values, "acos": acos, "roas": roas, "cpc": cpc, "ctr": ctr, "cvr": cvr
            }.items() if value is None]
            for anomaly in anomalies_by_campaign[campaign_id]:
                facts.append(NormalizedDetailFact(
                    fact_id=f"fact-{evidence.evidence_id}-{index}-{anomaly.anomaly_id}",
                    campaign_id=campaign_id, anomaly_id=anomaly.anomaly_id, report_type=report.tool,
                    period=period, object_type=object_type, object_name=object_name,
                    spend=spend, sales=sales, orders=orders, impressions=impressions, clicks=clicks,
                    acos=acos, roas=roas, cpc=cpc, ctr=ctr, cvr=cvr, bid=bid,
                    match_type=RuleBasedDataInspectionAgent._text(row.get("match_type") or row.get("matchType")),
                    targeting_type=RuleBasedDataInspectionAgent._text(row.get("targeting_type") or row.get("target_type")),
                    missing_fields=missing, evidence_ref=evidence.evidence_id,
                    artifact_id=evidence.artifact_id, trace_id=evidence.trace_id,
                ))
        return facts

    @staticmethod
    def _deduplicate_facts(facts: list[NormalizedDetailFact]) -> list[NormalizedDetailFact]:
        deduplicated: list[NormalizedDetailFact] = []
        seen: set[tuple[Any, ...]] = set()
        for fact in facts:
            key = (fact.campaign_id, fact.anomaly_id, fact.report_type, fact.period, fact.object_name,
                   fact.spend, fact.sales, fact.orders, fact.impressions, fact.clicks, fact.bid, fact.match_type)
            if key not in seen:
                seen.add(key)
                deduplicated.append(fact)
        return deduplicated

    @classmethod
    def _build_aggregates(cls, facts: list[NormalizedDetailFact]) -> list[AttributionAggregate]:
        groups: dict[tuple[str, str, str, str, str], list[NormalizedDetailFact]] = {}
        for fact in facts:
            groups.setdefault((fact.campaign_id, fact.anomaly_id, fact.report_type, fact.period, fact.object_type), []).append(fact)
        aggregates: list[AttributionAggregate] = []
        for key, items in sorted(groups.items()):
            def item_view(item: NormalizedDetailFact) -> dict[str, Any]:
                return {"object_name": item.object_name, "spend": item.spend, "sales": item.sales,
                        "orders": item.orders, "acos": item.acos, "evidence_ref": item.evidence_ref}
            by_spend = sorted(items, key=lambda item: item.spend or 0, reverse=True)
            zero_order = [item for item in by_spend if item.orders == 0 and (item.spend or 0) > 0]
            by_acos = sorted([item for item in items if item.acos is not None], key=lambda item: item.acos or 0, reverse=True)
            tail = by_spend[10:]
            match_groups: dict[str, list[NormalizedDetailFact]] = {}
            for item in items:
                if item.match_type:
                    match_groups.setdefault(item.match_type, []).append(item)
            aggregates.append(AttributionAggregate(
                campaign_id=key[0], anomaly_id=key[1], report_type=key[2], period=key[3], object_type=key[4],
                total_objects=len(items), total_spend=sum(item.spend or 0 for item in items),
                total_sales=sum(item.sales or 0 for item in items), total_orders=sum(item.orders or 0 for item in items),
                top_spend=[item_view(item) for item in by_spend[:10]],
                top_zero_order_spend=[item_view(item) for item in zero_order[:10]],
                top_acos=[item_view(item) for item in by_acos[:10]],
                by_match_type=[{"match_type": match, "objects": len(group), "spend": sum(item.spend or 0 for item in group), "orders": sum(item.orders or 0 for item in group)} for match, group in sorted(match_groups.items())],
                long_tail={"objects": len(tail), "spend": sum(item.spend or 0 for item in tail), "orders": sum(item.orders or 0 for item in tail), "sales": sum(item.sales or 0 for item in tail)},
                evidence_refs=sorted({item.evidence_ref for item in items}),
            ))
        return aggregates

    @classmethod
    def _findings_from_report(
        cls,
        *,
        report: AdvertisingReport,
        evidence: DiagnosticEvidence,
        anomalies: list[DetectedAnomaly],
        request: AdDiagnosticRequest,
    ) -> list[AttributionFinding]:
        """Extract only directly auditable facts; raw report rows never reach the LLM."""
        anomalies_by_campaign: dict[str, list[DetectedAnomaly]] = {}
        for anomaly in anomalies:
            if anomaly.entity.campaign_id:
                anomalies_by_campaign.setdefault(anomaly.entity.campaign_id, []).append(anomaly)
        fallback_campaigns = evidence.query.get("campaign_id")
        if isinstance(fallback_campaigns, str):
            fallback_campaigns = [fallback_campaigns]
        fallback_campaign = fallback_campaigns[0] if isinstance(fallback_campaigns, list) and len(fallback_campaigns) == 1 else None
        target_acos = request.goal.target_acos or 0.50
        findings: list[AttributionFinding] = []
        for index, row in enumerate(report.rows):
            campaign_id = RuleBasedDataInspectionAgent._text(row.get("campaign_id")) or fallback_campaign
            if not campaign_id or campaign_id not in anomalies_by_campaign:
                continue
            spend = RuleBasedDataInspectionAgent._number(row.get("spends") or row.get("spend"))
            sales = RuleBasedDataInspectionAgent._number(row.get("sales"))
            orders = RuleBasedDataInspectionAgent._number(row.get("orders"))
            clicks = RuleBasedDataInspectionAgent._number(row.get("clicks"))
            acos = spend / sales if sales > 0 else None
            text = RuleBasedDataInspectionAgent._text(
                row.get("search_term") or row.get("search_terms") or row.get("customer_search_term")
                or row.get("keyword") or row.get("keyword_text") or row.get("targeting")
                or row.get("targeting_text") or row.get("target") or row.get("target_name")
            )
            for anomaly in anomalies_by_campaign[campaign_id]:
                fact: tuple[str, str, float, float, str | None] | None = None
                if text and spend >= request.thresholds.min_spend and orders == 0 and sales == 0 and report.tool in {
                    "ad_campaign_search_term_report", "ad_campaign_keyword_report", "ad_campaign_targeting_report"
                }:
                    label = "搜索词" if report.tool == "ad_campaign_search_term_report" else "关键词" if report.tool == "ad_campaign_keyword_report" else "投放目标"
                    fact = (
                        "conversion",
                        f"{label}“{text}”花费 {spend:.2f}、订单 0、销售额 0，是当前异常的直接无转化消耗证据。",
                        min(1.0, spend / max(request.thresholds.min_spend * 3, 1)),
                        0.92,
                        text,
                    )
                elif text and acos is not None and acos >= target_acos and report.tool in {
                    "ad_campaign_keyword_report", "ad_campaign_targeting_report"
                }:
                    label = "关键词" if report.tool == "ad_campaign_keyword_report" else "投放目标"
                    fact = (
                        "cost",
                        f"{label}“{text}”花费 {spend:.2f}、销售额 {sales:.2f}，ACOS 为 {acos * 100:.1f}%，高于目标 {target_acos * 100:.1f}%。",
                        min(1.0, acos / max(target_acos, 0.01) - 1),
                        0.84,
                        text,
                    )
                elif report.tool == "ad_campaign_group_report":
                    budget = RuleBasedDataInspectionAgent._optional_number(row.get("daily_budget") or row.get("budget"))
                    if budget and spend >= budget * 0.9 and anomaly.anomaly_type in {
                        AnomalyType.DELIVERY_DROP, AnomalyType.BUDGET_CONSTRAINED
                    }:
                        fact = (
                            "budget",
                            f"广告组当前花费 {spend:.2f}，已接近日预算 {budget:.2f}，预算约束可能限制后续投放。",
                            min(1.0, spend / budget),
                            0.80,
                            RuleBasedDataInspectionAgent._text(row.get("ad_group_id")),
                        )
                if fact:
                    category, statement, contribution, confidence, object_ref = fact
                    findings.append(AttributionFinding(
                        finding_id=f"finding-{evidence.evidence_id}-{index}-{anomaly.anomaly_id}",
                        anomaly_id=anomaly.anomaly_id,
                        campaign_id=campaign_id,
                        category=category,
                        statement=statement,
                        contribution=contribution,
                        confidence=confidence,
                        evidence_refs=[evidence.evidence_id],
                        object_ref=object_ref,
                    ))
        for comparison in evidence.target_period_comparisons:
            for anomaly in anomalies_by_campaign.get(comparison.campaign_id, []):
                statements: list[tuple[str, str, float]] = []
                if comparison.baseline_cpc and comparison.current_cpc and comparison.current_cpc >= comparison.baseline_cpc * 1.2:
                    statements.append(("cost", f"投放目标“{comparison.target_key}”的 CPC 从 {comparison.baseline_cpc:.2f} 升至 {comparison.current_cpc:.2f}。", 0.86))
                if comparison.baseline_cvr and comparison.current_cvr is not None and comparison.current_cvr <= comparison.baseline_cvr * 0.7:
                    statements.append(("conversion", f"投放目标“{comparison.target_key}”的转化率从 {comparison.baseline_cvr * 100:.1f}% 降至 {comparison.current_cvr * 100:.1f}%。", 0.86))
                if comparison.baseline_bid and comparison.current_bid and comparison.current_bid >= comparison.baseline_bid * 1.1:
                    statements.append(("cost", f"投放目标“{comparison.target_key}”的竞价从 {comparison.baseline_bid:.2f} 上调至 {comparison.current_bid:.2f}。", 0.80))
                for position, (category, statement, confidence) in enumerate(statements):
                    findings.append(AttributionFinding(
                        finding_id=f"finding-{evidence.evidence_id}-comparison-{position}-{anomaly.anomaly_id}",
                        anomaly_id=anomaly.anomaly_id,
                        campaign_id=comparison.campaign_id,
                        category=category,
                        statement=statement,
                        contribution=0.8,
                        confidence=confidence,
                        evidence_refs=[evidence.evidence_id],
                        object_ref=comparison.target_key,
                    ))
        return findings

    @classmethod
    def _focus_anomalies(cls, anomalies: list[DetectedAnomaly]) -> list[DetectedAnomaly]:
        severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        ranked = sorted(
            anomalies,
            key=lambda item: (
                severity_rank.get(item.severity, 0),
                abs(item.relative_change or 0),
                abs(item.absolute_change or 0),
                item.confidence,
            ),
            reverse=True,
        )
        selected: list[DetectedAnomaly] = []
        seen_campaigns: set[str] = set()
        for anomaly in ranked:
            campaign_id = anomaly.entity.campaign_id
            if not campaign_id or campaign_id in seen_campaigns:
                continue
            selected.append(anomaly)
            seen_campaigns.add(campaign_id)
            if len(selected) == cls.MAX_FOCUS_CAMPAIGNS:
                break
        return selected

    @classmethod
    def _detail_batches(
        cls,
        primary: dict[str, list[str]],
        secondary: dict[str, list[str]],
        follow_up: dict[str, list[str]],
        previously_queried: set[tuple[str, str]],
    ) -> list[tuple[str, list[str]]]:
        batches: list[tuple[str, list[str]]] = []
        seen_pairs = set(previously_queried)
        for request_set in (primary, secondary, follow_up):
            for tool in cls.DETAIL_TOOLS:
                campaign_ids = request_set.get(tool, [])
                ids = [
                    campaign_id
                    for campaign_id in dict.fromkeys(campaign_ids)
                    if (tool, campaign_id) not in seen_pairs
                ]
                if tool == "ad_campaign_search_term_report":
                    for campaign_id in ids:
                        batches.append((tool, [campaign_id]))
                        seen_pairs.add((tool, campaign_id))
                    continue
                for start in range(0, len(ids), cls.BATCH_SIZE):
                    batch = ids[start : start + cls.BATCH_SIZE]
                    batches.append((tool, batch))
                    seen_pairs.update((tool, campaign_id) for campaign_id in batch)
        return batches

    @classmethod
    def _quota_ledger(cls, state: AdvertisingDiagnosticState) -> dict[tuple[int, str], dict[str, int | str]]:
        ledger: dict[tuple[int, str], dict[str, int | str]] = {}
        for raw in state.get("detail_call_quotas", []):
            try:
                item = DetailCallQuota.model_validate(raw)
            except Exception:
                continue
            ledger[(item.round, item.tool)] = item.model_dump()
        return ledger

    @classmethod
    def _quota_bucket(
        cls, ledger: dict[tuple[int, str], dict[str, int | str]], round_number: int, tool: str
    ) -> dict[str, int | str]:
        return ledger.setdefault(
            (round_number, tool),
            {"round": round_number, "tool": tool, "used": 0, "limit": cls.DETAIL_CALL_LIMIT, "failed": 0},
        )

    @classmethod
    def _reserve_call(
        cls, ledger: dict[tuple[int, str], dict[str, int | str]], round_number: int, tool: str
    ) -> bool:
        bucket = cls._quota_bucket(ledger, round_number, tool)
        if int(bucket["used"]) >= int(bucket["limit"]):
            return False
        bucket["used"] = int(bucket["used"]) + 1
        return True

    @classmethod
    def _quota_entries(cls, ledger: dict[tuple[int, str], dict[str, int | str]]) -> list[DetailCallQuota]:
        return [
            DetailCallQuota.model_validate(ledger[key])
            for key in sorted(ledger)
        ]

    def _fetch_detail_report(
        self,
        *,
        tool: str,
        campaign_ids: list[str],
        request: AdDiagnosticRequest,
        period,
        ledger: dict[tuple[int, str], dict[str, int | str]],
        round_number: int,
        warnings: list[str],
    ) -> AdvertisingReport | None:
        if not self._reserve_call(ledger, round_number, tool):
            return None
        try:
            return self.gateway.attribution_report(
                tool=tool, profile_ids=request.profile_ids, period=period, campaign_ids=campaign_ids
            )
        except Exception:
            bucket = self._quota_bucket(ledger, round_number, tool)
            bucket["failed"] = int(bucket["failed"]) + 1
            warnings.append(f"{tool} 下钻查询未成功，相关结论已降级为待验证假设。")
            return None

    def _execute_detail_batches(
        self,
        *,
        batches: list[tuple[str, list[str]]],
        request: AdDiagnosticRequest,
        state: AdvertisingDiagnosticState,
        ledger: dict[tuple[int, str], dict[str, int | str]],
        round_number: int,
        warnings: list[str],
    ) -> list[tuple[str, list[str], AdvertisingReport]]:
        """Run one ordered batch per report type at a time.

        This preserves priority within a report while allowing different report
        types to make progress concurrently. A quota is reserved immediately
        before submission, so a timeout or failure still records the actual
        MCP attempt and can never overrun its type's hard limit.
        """
        pending = {tool: [] for tool in self.DETAIL_TOOLS}
        for tool, campaign_ids in batches:
            pending.setdefault(tool, []).append(campaign_ids)
        completed: list[tuple[str, list[str], AdvertisingReport]] = []
        with ThreadPoolExecutor(max_workers=self.MAX_REPORT_CONCURRENCY) as executor:
            while any(pending.values()):
                wave: list[tuple[str, list[str], object]] = []
                for tool in self.DETAIL_TOOLS:
                    if not pending.get(tool) or not self._reserve_call(ledger, round_number, tool):
                        continue
                    campaign_ids = pending[tool].pop(0)
                    try:
                        future = executor.submit(
                            self.gateway.attribution_report,
                            tool=tool,
                            profile_ids=request.profile_ids,
                            period=request.current_period,
                            campaign_ids=campaign_ids,
                        )
                    except RuntimeError as exc:
                        # A process shutdown can race an in-flight legacy
                        # diagnostic.  Do not leak a Python/LangGraph
                        # traceback or continue creating work in that state.
                        if "interpreter shutdown" in str(exc):
                            raise RuntimeError("广告巡检服务正在重启，请稍后重新发起查询。") from exc
                        raise
                    wave.append((tool, campaign_ids, future))
                if not wave:
                    break
                for tool, campaign_ids, future in wave:
                    try:
                        completed.append((tool, campaign_ids, future.result()))
                    except Exception:
                        bucket = self._quota_bucket(ledger, round_number, tool)
                        bucket["failed"] = int(bucket["failed"]) + 1
                        warnings.append(f"{tool} 下钻查询未成功，相关结论已降级为待验证假设。")
        return completed

    @staticmethod
    def _queried_pairs(evidence: list[DiagnosticEvidence]) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for item in evidence:
            campaign_ids = item.query.get("campaign_id")
            if isinstance(campaign_ids, str):
                pairs.add((item.tool, campaign_ids))
            elif isinstance(campaign_ids, list):
                pairs.update(
                    (item.tool, str(campaign_id))
                    for campaign_id in campaign_ids
                    if campaign_id is not None
                )
        return pairs

    @staticmethod
    def _explain(anomaly: DetectedAnomaly, has_detail: bool) -> tuple[str, str]:
        suffix = "，下钻明细已取得" if has_detail else "，需要运营人员结合明细复核"
        if anomaly.anomaly_type in {AnomalyType.CPC_RISE, AnomalyType.SPEND_SPIKE, AnomalyType.ACOS_RISE}:
            return "cost", f"成本侧指标相较基准周期恶化{suffix}。"
        if anomaly.anomaly_type in {
            AnomalyType.CVR_DROP,
            AnomalyType.SALES_DROP,
            AnomalyType.ORDERS_DROP,
            AnomalyType.ZERO_ORDER_WASTE,
            AnomalyType.ROAS_DROP,
        }:
            return "conversion", f"点击后的成交效率或产出下降{suffix}。"
        if anomaly.anomaly_type == AnomalyType.BUDGET_CONSTRAINED:
            return "budget", f"广告投放受到预算约束{suffix}。"
        return "traffic", f"流量获取或投放量出现明显变化{suffix}。"


class GuardrailedStrategyRecommendationAgent:
    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> StrategyResult:
        inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
        attribution = AttributionResult.model_validate(state.get("attribution", {}))
        anomaly_by_id = {item.anomaly_id: item for item in inspection.anomalies}
        recommendations: list[StrategyRecommendation] = []
        for cause in attribution.causes:
            if not cause.verified or cause.role != "primary":
                continue
            anomaly = next(
                (anomaly_by_id[item] for item in cause.anomaly_ids if item in anomaly_by_id),
                None,
            )
            if anomaly is None:
                continue
            action_type, title, proposed_change, risk = self._strategy(
                anomaly, request.goal.growth_priority, bool(cause.missing_evidence)
            )
            recommendations.append(
                StrategyRecommendation(
                    recommendation_id=f"recommendation-{uuid4().hex[:16]}",
                    cause_ids=[cause.cause_id],
                    action_type=action_type,
                    target=anomaly.entity,
                    title=title,
                    rationale=cause.statement,
                    proposed_change=proposed_change,
                    expected_effect="降低异常继续扩大的风险，并在复查周期验证指标是否恢复。",
                    risk_level=risk,
                    evidence_refs=cause.evidence_refs,
                )
            )
        return StrategyResult(recommendations=recommendations)

    @staticmethod
    def _strategy(
        anomaly: DetectedAnomaly, goal: str, evidence_missing: bool
    ) -> tuple[str, str, dict[str, Any], str]:
        if evidence_missing:
            return "observe", "补查明细后再决定广告调整", {"review_after_days": 3}, "read_only"
        if anomaly.anomaly_type == AnomalyType.ZERO_ORDER_WASTE:
            return "add_negative", "复核高消耗无单流量并考虑否定", {}, "high_risk_write"
        if anomaly.anomaly_type in {AnomalyType.CPC_RISE, AnomalyType.ACOS_RISE, AnomalyType.ROAS_DROP}:
            adjustment = -10 if goal == "scale" else -15
            return (
                "adjust_bid",
                f"将异常投放竞价下调 {abs(adjustment)}%",
                {"bid_change_percent": adjustment},
                "low_risk_write",
            )
        if anomaly.anomaly_type == AnomalyType.BUDGET_CONSTRAINED:
            adjustment = 15 if goal == "scale" else 10
            return (
                "adjust_budget",
                f"复核后将高效活动预算提高 {adjustment}%",
                {"budget_change_percent": adjustment},
                "high_risk_write",
            )
        if anomaly.anomaly_type in {AnomalyType.CVR_DROP, AnomalyType.SALES_DROP, AnomalyType.ORDERS_DROP}:
            return "optimize_listing", "检查商品页、价格与库存对转化的影响", {}, "read_only"
        return "observe", "保持设置并在 3 天后复查", {"review_after_days": 3}, "read_only"


class ApprovalRequiredReviewTodoAgent:
    _SUMMARY_MAX_LENGTH = 96
    _DETAIL_MAX_LENGTH = 400

    def invoke(
        self, request: AdDiagnosticRequest, state: AdvertisingDiagnosticState
    ) -> ReviewTodoResult:
        inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
        strategy = StrategyResult.model_validate(state.get("strategy", {}))
        attribution = AttributionResult.model_validate(state.get("attribution", {}))
        anomaly_by_entity = {
            (item.entity.profile_id, item.entity.entity_id): item
            for item in inspection.anomalies
        }
        anomalies_by_campaign: dict[tuple[str, str], list[DetectedAnomaly]] = {}
        for item in inspection.anomalies:
            if item.entity.campaign_id:
                anomalies_by_campaign.setdefault(
                    (item.entity.profile_id, item.entity.campaign_id), []
                ).append(item)
        snapshots_by_campaign = {
            (item.entity.profile_id, item.entity.campaign_id or item.entity.entity_id, item.period): item
            for item in inspection.snapshots
        }
        cause_by_id = {item.cause_id: item for item in attribution.causes}
        evidence_by_id = {item.evidence_id: item for item in attribution.evidence}
        todos: list[OperationsTodo] = []
        rejected: list[str] = []
        seen: set[str] = set()
        for recommendation in strategy.recommendations:
            if not recommendation.evidence_refs:
                rejected.append(recommendation.recommendation_id)
                continue
            key = (
                f"{recommendation.target.profile_id}:"
                f"{recommendation.action_type}:{recommendation.target.entity_id}"
            )
            if key in seen:
                rejected.append(recommendation.recommendation_id)
                continue
            seen.add(key)
            anomaly = anomaly_by_entity.get(
                (recommendation.target.profile_id, recommendation.target.entity_id)
            )
            cause = next(
                (cause_by_id[cause_id] for cause_id in recommendation.cause_ids if cause_id in cause_by_id),
                None,
            )
            campaign_anomaly_ids = {
                item.anomaly_id
                for item in anomalies_by_campaign.get(
                    (recommendation.target.profile_id, recommendation.target.campaign_id or ""),
                    [],
                )
            }
            campaign_causes = [
                item for item in attribution.causes
                if item.verified and campaign_anomaly_ids.intersection(item.anomaly_ids)
            ]
            priority = self._priority(anomaly.severity if anomaly else "medium")
            detail_evidence = {
                item.evidence_id: item
                for item in [
                    *(
                        evidence_by_id[item]
                        for item in recommendation.evidence_refs
                        if item in evidence_by_id
                    ),
                    *(
                        item
                        for item in attribution.evidence
                        if recommendation.target.campaign_id
                        and recommendation.target.campaign_id in item.entity_refs
                    ),
                ]
            }
            todos.append(
                OperationsTodo(
                    todo_id=f"todo-{uuid4().hex[:16]}",
                    recommendation_id=recommendation.recommendation_id,
                    profile_id=recommendation.target.profile_id,
                    title=recommendation.title,
                    description=self._summary(anomaly, cause, recommendation),
                    priority=priority,
                    status="needs_review",
                    action_type=recommendation.action_type,
                    target=recommendation.target,
                    proposed_change=recommendation.proposed_change,
                    evidence_refs=recommendation.evidence_refs,
                    detail=self._detail(
                        anomaly,
                        cause,
                        recommendation,
                        snapshots_by_campaign.get((
                            recommendation.target.profile_id,
                            recommendation.target.campaign_id or recommendation.target.entity_id,
                            "current",
                        )),
                        snapshots_by_campaign.get((
                            recommendation.target.profile_id,
                            recommendation.target.campaign_id or recommendation.target.entity_id,
                            "baseline",
                        )),
                        list(detail_evidence.values()),
                        campaign_causes,
                    ),
                    approval_required=True,
                    due_at=datetime.now(timezone.utc) + timedelta(days=1 if priority in {"urgent", "high"} else 3),
                    dedupe_key=key,
                )
            )
        return ReviewTodoResult(
            todos=todos,
            rejected_recommendation_ids=rejected,
        )

    @staticmethod
    def _detail(
        anomaly: DetectedAnomaly | None,
        cause: ProblemCause | None,
        recommendation: StrategyRecommendation,
        current_snapshot: AdMetricSnapshot | None,
        baseline_snapshot: AdMetricSnapshot | None,
        evidence: list[DiagnosticEvidence],
        campaign_causes: list[ProblemCause],
    ) -> TodoDetail:
        target_name = recommendation.target.name or recommendation.target.entity_id
        if anomaly is None:
            diagnosis = f"已识别到“{target_name}”需要运营复核。"
            metric_summary = "异常指标的具体数值需要在诊断记录中复核。"
        else:
            metric_label = {
                "acos": "ACOS",
                "roas": "ROAS",
                "ctr": "CTR",
                "cpc": "CPC",
                "spend": "花费",
                "sales": "广告销售额",
                "orders": "订单量",
                "cvr": "转化率",
            }.get(anomaly.metric, anomaly.metric)
            diagnosis = f"“{target_name}”出现{metric_label}异常，严重程度为{anomaly.severity}。"
            metric_summary = ApprovalRequiredReviewTodoAgent._metric_summary(
                anomaly,
                metric_label,
                current_snapshot=current_snapshot,
                baseline_snapshot=baseline_snapshot,
            )
        campaign_id = recommendation.target.campaign_id
        relevant_evidence = [
            item
            for item in evidence
            if campaign_id and campaign_id in item.entity_refs
        ]
        contributing = [
            item.statement for item in campaign_causes
            if item.role == "contributing" and (cause is None or item.cause_id != cause.cause_id)
        ][:2]
        coverage = {
            "ad_campaign_search_term_report": "搜索词",
            "ad_campaign_keyword_report": "关键词",
            "ad_campaign_targeting_report": "投放目标",
            "ad_campaign_group_report": "广告组",
        }
        checked_sources = sorted({
            coverage[item.tool]
            for item in relevant_evidence
            if item.tool in coverage
        })
        attribution_parts = [
            f"主因：{cause.statement if cause else recommendation.rationale}",
            *( [f"辅助因素：{'；'.join(contributing)}"] if contributing else []),
        ]
        return TodoDetail(
            diagnosis=diagnosis,
            metric_summary=metric_summary,
            attribution=ApprovalRequiredReviewTodoAgent._fit_detail_parts_to_limit(
                attribution_parts, ApprovalRequiredReviewTodoAgent._DETAIL_MAX_LENGTH
            ),
            recommendation=(
                f"{recommendation.title}；建议动作："
                f"{ApprovalRequiredReviewTodoAgent._change_summary(recommendation.proposed_change)}。"
            ),
            expected_effect=recommendation.expected_effect,
            checked_sources=checked_sources,
        )

    @classmethod
    def _summary(
        cls,
        anomaly: DetectedAnomaly | None,
        cause: ProblemCause | None,
        recommendation: StrategyRecommendation,
    ) -> str:
        metric = ""
        if anomaly is not None:
            metric_label = {
                "acos": "ACOS",
                "roas": "ROAS",
                "ctr": "CTR",
                "cpc": "CPC",
                "spend": "花费",
                "sales": "广告销售额",
                "orders": "订单量",
                "cvr": "转化率",
            }.get(anomaly.metric, anomaly.metric)
            metric = f"{metric_label}异常；"
        reason = cause.statement if cause else recommendation.rationale
        return cls._truncate_text(f"原因摘要：{metric}{reason}", cls._SUMMARY_MAX_LENGTH)

    @classmethod
    def _fit_detail_parts_to_limit(cls, parts: list[str], limit: int) -> str:
        result = ""
        for part in parts:
            separator = "\n" if result else ""
            remaining = limit - len(result) - len(separator)
            if remaining <= 0:
                break
            if len(part) <= remaining:
                result += separator + part
                continue
            result += separator + cls._truncate_text(part, remaining)
            break
        return result

    @staticmethod
    def _truncate_text(value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        if limit <= 1:
            return value[:limit]
        return f"{value[:limit - 1]}…"

    @staticmethod
    def _target_period_findings(
        comparisons: list[TargetPeriodComparison],
    ) -> list[str]:
        findings: list[str] = []
        cpc_rises = [
            item
            for item in comparisons
            if item.baseline_cpc and item.current_cpc
            and item.current_cpc >= item.baseline_cpc * 1.2
        ]
        if cpc_rises:
            findings.append(
                f"{len(cpc_rises)} 个投放目标的 CPC 较基准周期上升至少 20%，需要结合竞价和流量结构验证成本上升原因"
            )
        cvr_drops = [
            item
            for item in comparisons
            if item.baseline_cvr and item.current_cvr is not None
            and item.current_cvr <= item.baseline_cvr * 0.7
        ]
        if cvr_drops:
            findings.append(
                f"{len(cvr_drops)} 个投放目标的转化率较基准周期下降至少 30%，需要继续核查商品与流量匹配情况"
            )
        bid_rises = [
            item
            for item in comparisons
            if item.baseline_bid and item.current_bid
            and item.current_bid >= item.baseline_bid * 1.1
        ]
        if bid_rises:
            findings.append(
                f"{len(bid_rises)} 个投放目标的竞价较基准周期上调至少 10%"
            )
        return findings

    @staticmethod
    def _related_findings(
        anomalies: list[DetectedAnomaly], *, primary: DetectedAnomaly | None
    ) -> list[str]:
        labels = {
            "acos": "ACOS",
            "roas": "ROAS",
            "ctr": "CTR",
            "cpc": "CPC",
            "cvr": "转化率",
            "spend": "花费",
            "sales": "广告销售额",
            "orders": "订单量",
            "impressions": "曝光量",
        }
        findings: list[str] = []
        for item in anomalies:
            if primary and item.anomaly_id == primary.anomaly_id:
                continue
            label = labels.get(item.metric, item.metric)
            value = ApprovalRequiredReviewTodoAgent._metric_value(item.metric, item.current_value)
            findings.append(f"同时检测到{label}异常，当前值为{value}（严重程度：{item.severity}）")
        return list(dict.fromkeys(findings))[:4]

    @staticmethod
    def _metric_summary(
        anomaly: DetectedAnomaly,
        label: str,
        *,
        current_snapshot: AdMetricSnapshot | None,
        baseline_snapshot: AdMetricSnapshot | None,
    ) -> str:
        if current_snapshot is None:
            text = f"当前周期 {label}：{ApprovalRequiredReviewTodoAgent._metric_value(anomaly.metric, anomaly.current_value)}"
            if anomaly.baseline_value is not None:
                text += f"；基准周期：{ApprovalRequiredReviewTodoAgent._metric_value(anomaly.metric, anomaly.baseline_value)}"
            return text + "。"

        current_metrics = ApprovalRequiredReviewTodoAgent._snapshot_metric_values(current_snapshot)
        text = f"当前周期：{'；'.join(current_metrics)}"
        if baseline_snapshot is not None:
            baseline_metrics = ApprovalRequiredReviewTodoAgent._snapshot_metric_values(baseline_snapshot)
            text += f"。基准周期：{'；'.join(baseline_metrics)}"
        return text + "。"

    @staticmethod
    def _snapshot_metric_values(snapshot: AdMetricSnapshot) -> list[str]:
        metrics = [
            ("曝光量", "impressions", snapshot.impressions),
            ("点击量", "clicks", snapshot.clicks),
            ("CTR", "ctr", snapshot.ctr),
            ("花费", "spend", snapshot.spend),
            ("CPC", "cpc", snapshot.cpc),
            ("订单量", "orders", snapshot.orders),
            ("广告销售额", "sales", snapshot.sales),
            ("CVR", "cvr", snapshot.cvr),
            ("ACOS", "acos", snapshot.acos),
            ("ROAS", "roas", snapshot.roas),
        ]
        values = [
            f"{label}：{ApprovalRequiredReviewTodoAgent._metric_value(metric, value)}"
            for label, metric, value in metrics
        ]
        if snapshot.budget is not None:
            values.append(
                f"日预算：{ApprovalRequiredReviewTodoAgent._metric_value('budget', snapshot.budget)}"
            )
        return values

    @staticmethod
    def _metric_value(metric: str, value: float | None) -> str:
        if value is None:
            return "—"
        if metric in {"acos", "ctr", "cvr"}:
            return f"{value * 100:.1f}%"
        if metric in {"impressions", "clicks", "orders", "ad_units"}:
            return f"{value:,.0f}"
        return f"{value:.2f}"

    @staticmethod
    def _change_summary(change: dict[str, Any]) -> str:
        if isinstance(change.get("bid_change_percent"), (int, float)):
            return f"竞价调整 {change['bid_change_percent']:+g}%"
        if isinstance(change.get("budget_change_percent"), (int, float)):
            return f"预算调整 {change['budget_change_percent']:+g}%"
        if isinstance(change.get("review_after_days"), (int, float)):
            return f"{change['review_after_days']:g} 天后复查"
        return "人工复核后确定具体调整"

    @staticmethod
    def _priority(severity: str) -> str:
        return {
            "critical": "urgent",
            "high": "high",
            "medium": "medium",
            "low": "low",
        }.get(severity, "medium")

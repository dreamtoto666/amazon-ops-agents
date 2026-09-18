"""Convert bounded Sif competitor evidence into compact state-tree profiles."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .competitor_research_catalog import CAPABILITY_BY_TOOL
from .llm import StructuredLLM
from .models import CompetitorProfiles, QueryScope, SpecialistResult
from .models import (
    CompetitorDataModules,
    MultiVariantOrganicPositionModule,
    ProcessedDataStatus,
    RecommendationPlacementModule,
    TrafficKeywordLookupModule,
    TrafficKeywordReverseLookupModule,
)
from .prompts import COMPETITOR_DATA_PROCESSOR_SYSTEM_PROMPT


COMPETITOR_PROFILE_MAX_TOKENS = 16_000
PRIVATE_METRIC_TERMS = ("spend", "bid", "acos", "roas", "orders", "cvr", "花费", "竞价", "订单", "转化率")
# Reporting a private metric as unavailable is required by policy, so those
# statements are allowed; only an unsupported assertion is a violation.
UNAVAILABLE_MARKERS = (
    "不可得", "不可用", "不可推断", "未返回", "未提供", "未披露", "无法", "缺少", "缺失",
    "无数据", "不适用", "未知",
    "unknown", "unavailable", "not available", "not provided", "missing", "no data", "cannot", "n/a",
)
_METRIC_VALUE = re.compile(r"\d")
SECTION_BY_TOOL = {
    "inspect_ad_architecture": "ad_architecture",
    "analyze_traffic_structure": "traffic_structure",
    "compare_traffic_keywords": "keyword_coverage",
    "replay_operations_history": "operations_history",
    "inspect_campaign": "campaign_detail",
    "inspect_ad_group": "ad_group_detail",
    "analyze_recommendation_traffic": "recommendation_traffic",
}


class CompetitorDataProcessor:
    """A bounded structured-LLM transformation, separate from report writing."""

    def __init__(self, llm: StructuredLLM, *, max_attempts: int = 3) -> None:
        self._llm = llm
        self._max_attempts = max_attempts

    def process(
        self, *, scope: QueryScope, result: SpecialistResult
    ) -> tuple[CompetitorProfiles | None, dict[str, Any] | None]:
        context, evidence_ids, source_names, truncated_sources = build_processor_context(scope, result)
        if not evidence_ids:
            return None, {"code": "COMPETITOR_PROCESSING_NO_EVIDENCE"}

        last_error: str | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                output = self._llm.complete(
                    system_prompt=COMPETITOR_DATA_PROCESSOR_SYSTEM_PROMPT,
                    context=context,
                    output_model=CompetitorProfiles,
                    max_tokens=COMPETITOR_PROFILE_MAX_TOKENS,
                )
                return (
                    validate_competitor_profiles(
                        output,
                        scope=scope,
                        evidence_ids=evidence_ids,
                        source_names=source_names,
                        truncated_sources=truncated_sources,
                    ),
                    None,
                )
            except Exception as exc:
                last_error = getattr(exc, "code", None) or type(exc).__name__
        return None, {
            "code": "COMPETITOR_PROCESSING_FAILED",
            "attempts": self._max_attempts,
            "last_error": last_error or "UNKNOWN",
        }

    def process_modules(
        self, *, scope: QueryScope, result: SpecialistResult
    ) -> tuple[CompetitorDataModules | None, dict[str, Any] | None]:
        """Deterministically build report modules from returned Sif fields.

        This deliberately does not ask an LLM to interpret business evidence.
        Derived values are calculated only from same-period Sif source values;
        a module whose required source fields are absent stays unavailable.
        """
        own = scope.own_asin
        competitors = list(dict.fromkeys(scope.competitor_asins))
        if not own or len(competitors) != 1 or not scope.marketplaces:
            return None, {"code": "COMPETITOR_MODULE_SCOPE_INVALID"}
        evidence = list(_iter_sif_evidence(result))
        if not evidence:
            return None, {"code": "COMPETITOR_PROCESSING_NO_EVIDENCE"}

        lookup = _traffic_keyword_lookup(evidence, own, competitors[0])
        reverse_lookup = _traffic_keyword_reverse_lookup(evidence, own, competitors[0])
        multi_organic = _multi_variant_organic_position(evidence, own, competitors[0])
        modules = CompetitorDataModules(
            own_parent_asin=own,
            competitor_parent_asin=competitors[0],
            marketplace=scope.marketplaces[0],
            traffic_keyword_lookup=lookup,
            traffic_keyword_reverse_lookup=reverse_lookup,
            multi_variant_organic_position=multi_organic,
            recommendation_placement=RecommendationPlacementModule(
                status="unavailable",
                missing_reasons=["Sif 未返回推荐专栏到直接归属 Campaign 的映射，无法准确计算广告活动数量。"],
            ),
        )
        return modules, None


_ASIN_IN_TEXT = re.compile(r"\b[A-Z0-9]{10}\b")


def _iter_sif_evidence(result: SpecialistResult):
    """Yield only concrete Sif evidence, preserving its query-level ASIN scope."""
    for deliverable in result.deliverables:
        if deliverable.get("type") != "competitor_research":
            continue
        results = deliverable.get("results")
        if not isinstance(results, list):
            continue
        for tool_result in results:
            if not isinstance(tool_result, dict) or not tool_result.get("ok"):
                continue
            for item in tool_result.get("evidence", []):
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("evidence_id"), str) and isinstance(item.get("tool"), str):
                    yield item


def _traffic_keyword_lookup(
    evidence: list[dict[str, Any]], own_asin: str, competitor_asin: str
) -> TrafficKeywordLookupModule:
    """Map parent-ASIN variant distributions without imposing any filter."""
    records: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    evidence_ids_by_parent: dict[str, list[str]] = defaultdict(list)
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    overview_by_parent: dict[str, tuple[dict[str, Any], str]] = {}

    for item in evidence:
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        parent = query.get("asin")
        if parent not in {own_asin, competitor_asin}:
            continue
        payload = _payload_with_keys(item.get("data"), {"asins"})
        if item.get("tool") == "ops_get_listing_keyword_distribution" and isinstance(payload, dict):
            rows = payload.get("asins")
            if isinstance(rows, list):
                by_parent[parent].extend(row for row in rows if isinstance(row, dict))
                evidence_ids.append(item["evidence_id"])
                evidence_ids_by_parent[parent].append(item["evidence_id"])
        payload = _payload_with_keys(item.get("data"), {"overview"})
        if item.get("tool") == "ops_get_listing_traffic_overview" and isinstance(payload, dict):
            overview_by_parent[parent] = (payload, item["evidence_id"])

    for parent, role in ((own_asin, "own"), (competitor_asin, "competitor")):
        rows = by_parent.get(parent, [])
        if not rows:
            continue
        totals = [_number(row.get("total")) for row in rows]
        denominator = sum(value for value in totals if value is not None)
        variants = []
        for row, total in zip(rows, totals, strict=True):
            variant_asin = _asin_value(row.get("asin"))
            if variant_asin is None:
                # The requested JSON needs a real variant identifier. A row
                # without one is evidence of a source gap, never a synthetic ID.
                continue
            variants.append(
                {
                    "variant_asin": variant_asin,
                    "variant_attributes": row.get("dimensionValue"),
                    "total_traffic_ratio": total / denominator if total is not None and denominator > 0 else None,
                    "natural_traffic_ratio": _number(row.get("naturalRatio")),
                    "ad_traffic_ratio": _number(row.get("adRatio")),
                    "sp_ratio": _number(row.get("spRatio")),
                    "sp_recommend_ratio": _number(row.get("spRecRatio")),
                    "sb_ratio": _number(row.get("brandRatio")),
                    "sbv_ratio": _number(row.get("vedioRatio")),
                }
            )
        if not variants:
            continue
        overview, overview_evidence = overview_by_parent.get(parent, ({}, ""))
        overview_data = overview.get("overview") if isinstance(overview.get("overview"), dict) else {}
        records.append(
            {
                "asin_role": role,
                "parent_asin": parent,
                "listing_natural_traffic": overview_data.get("nf"),
                "listing_ad_traffic": overview_data.get("ad"),
                "advertising_traffic_distribution": overview_data.get("ad"),
                "variants": variants,
                "evidence_ids": [
                    *evidence_ids_by_parent[parent],
                    *([overview_evidence] if overview_evidence else []),
                ],
            }
        )

    if len(records) != 2:
        missing = "Sif 未为双方父 ASIN 都返回带变体 ASIN 的 Listing 关键词分布。"
        return TrafficKeywordLookupModule(
            status="partial" if records else "unavailable",
            records=records,
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            missing_reasons=[missing],
        )
    return TrafficKeywordLookupModule(
        status="available",
        records=records,
        evidence_ids=list(dict.fromkeys(evidence_ids)),
    )


def _traffic_keyword_reverse_lookup(
    evidence: list[dict[str, Any]], own_asin: str, competitor_asin: str
) -> TrafficKeywordReverseLookupModule:
    """Return precisely the Sif keyword records whose total share is over 1%."""
    records: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    missing: list[str] = []
    found_by_parent: set[str] = set()

    for item in evidence:
        if item.get("tool") != "market_get_asin_keyword_signals":
            continue
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        parent = query.get("asin")
        if parent not in {own_asin, competitor_asin}:
            continue
        payload = _payload_with_keys(item.get("data"), {"top_keywords"})
        keywords = payload.get("top_keywords") if isinstance(payload, dict) else None
        if not isinstance(keywords, list):
            continue
        found_by_parent.add(parent)
        evidence_ids.append(item["evidence_id"])
        role = "own" if parent == own_asin else "competitor"
        for row in keywords:
            if not isinstance(row, dict):
                continue
            keyword = row.get("keyword")
            total_ratio = _number(row.get("traffic_share"))
            natural_fraction = _number(row.get("natural_ratio"))
            if not isinstance(keyword, str) or total_ratio is None or natural_fraction is None:
                missing.append("Sif 流量词记录缺少 keyword、traffic_share 或 natural_ratio。")
                continue
            # Verified Sif units: traffic_share and natural_ratio are fractions
            # in [0, 1]. This is a unit guard, not an additional business filter.
            if not 0 <= total_ratio <= 1 or not 0 <= natural_fraction <= 1:
                missing.append("Sif 流量词占比单位不符合已核实的 0–1 比例。")
                continue
            if total_ratio <= 0.01:
                continue
            records.append(
                {
                    "asin_role": role,
                    "parent_asin": parent,
                    "keyword": keyword,
                    "total_traffic_ratio": total_ratio,
                    "natural_traffic_ratio": total_ratio * natural_fraction,
                    "ad_traffic_ratio": total_ratio * (1 - natural_fraction),
                    "growth_period_change": None,
                    "evidence_ids": [item["evidence_id"]],
                }
            )

    for parent in (own_asin, competitor_asin):
        if parent not in found_by_parent:
            missing.append(f"Sif 未返回父 ASIN {parent} 的 Listing 流量词信号。")
    if own_asin in found_by_parent:
        missing.append("Sif 未明确标记自有 ASIN 的上升期；growth_period_change 未填写。")
    status: ProcessedDataStatus = "available" if not missing else ("partial" if records else "unavailable")
    return TrafficKeywordReverseLookupModule(
        status=status,
        records=records,
        evidence_ids=list(dict.fromkeys(evidence_ids)),
        missing_reasons=list(dict.fromkeys(missing)),
    )


def _multi_variant_organic_position(
    evidence: list[dict[str, Any]], own_asin: str, competitor_asin: str
) -> MultiVariantOrganicPositionModule:
    """Calculate extra NF traffic from the returned child-ASIN NF scores.

    For a parent/keyword in one identical Sif weekly period:

    ``extra_natural = sum(child_natural_score) - max(child_natural_score)``.

    This is equivalent to Listing keyword natural traffic times the combined
    share of all non-leading variants.  The calculation intentionally uses raw
    scores and raw fractions rather than UI-rounded percentages.
    """
    parents = (own_asin, competitor_asin)
    parent_set = set(parents)
    variants_by_parent: dict[str, set[str]] = defaultdict(set)
    detail_rows: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    evidence_by_parent: dict[str, list[str]] = defaultdict(list)
    incomplete_parents: set[str] = set()
    missing: list[str] = []

    for item in evidence:
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        parent = query.get("asin")
        if item.get("tool") != "ops_get_listing_traffic_structure" or parent not in parent_set:
            continue
        payload = _payload_with_keys(item.get("data"), {"chars"})
        chars = payload.get("chars") if isinstance(payload, dict) else None
        dims = chars.get("dims") if isinstance(chars, dict) else None
        if not isinstance(dims, list):
            continue
        for dimension in dims:
            if not isinstance(dimension, dict) or dimension.get("isVariant") is False:
                # The parent Listing is not a variant: its own detail page is not
                # part of the child-score denominator, and treating it as one
                # makes its incomplete first page look like missing children.
                continue
            asin = _asin_value(dimension.get("val"))
            if asin:
                variants_by_parent[parent].add(asin)

    variant_parent = {
        variant: parent
        for parent, variants in variants_by_parent.items()
        for variant in variants
    }
    for item in evidence:
        if item.get("tool") != "ops_get_asin_traffic_trend_detail":
            continue
        query = item.get("query") if isinstance(item.get("query"), dict) else {}
        variant = query.get("asin")
        parent = variant_parent.get(variant) if isinstance(variant, str) else None
        if parent not in parent_set:
            continue
        payload = _payload_with_keys(item.get("data"), {"details"})
        rows = payload.get("details") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        total = _number(payload.get("total"))
        # A first page is not a complete denominator.  Do not emit a partial
        # percentage as an exact Listing keyword share.
        if total is not None and total > len(rows):
            incomplete_parents.add(parent)
            continue
        evidence_by_parent[parent].append(item["evidence_id"])
        for row in rows:
            if not isinstance(row, dict):
                continue
            keyword = row.get("keyword")
            score = _number(row.get("score"))
            if not isinstance(keyword, str) or score is None or score < 0:
                continue
            nf_info = row.get("pchangeReason", {}).get("nfInfo", {}) if isinstance(row.get("pchangeReason"), dict) else {}
            detail_rows[(parent, keyword)].append(
                {
                    "variant_asin": variant,
                    "score": score,
                    "natural_position_days": _int_number(nf_info.get("inFre")) if isinstance(nf_info, dict) else None,
                    "average_natural_rank": _number(nf_info.get("rankAvg")) if isinstance(nf_info, dict) else None,
                    "evidence_id": item["evidence_id"],
                }
            )

    records: list[dict[str, Any]] = []
    for parent in parents:
        if not variants_by_parent.get(parent):
            missing.append(f"Sif 未返回父 ASIN {parent} 的变体列表，无法计算多自然位额外流量。")
            continue
        if parent in incomplete_parents:
            missing.append(f"父 ASIN {parent} 的变体自然位明细未覆盖完整关键词页，无法准确计算流量占比。")
            continue
        parent_groups = [(keyword, rows) for (group_parent, keyword), rows in detail_rows.items() if group_parent == parent]
        if not parent_groups:
            missing.append(f"Sif 未返回父 ASIN {parent} 的变体自然位明细。")
            continue
        parent_total = sum(sum(row["score"] for row in rows) for _, rows in parent_groups)
        if parent_total <= 0:
            missing.append(f"父 ASIN {parent} 的变体自然流量得分总和为零，无法计算关键词自然流量占比。")
            continue
        for keyword, rows in parent_groups:
            natural_traffic = sum(row["score"] for row in rows)
            natural_ratio = natural_traffic / parent_total
            # This is the user's only requested keyword eligibility rule.
            if natural_ratio <= 0.01:
                continue
            primary = max(row["score"] for row in rows)
            records.append(
                {
                    "asin_role": "own" if parent == own_asin else "competitor",
                    "parent_asin": parent,
                    "keyword": keyword,
                    "natural_traffic": natural_traffic,
                    "natural_traffic_ratio": natural_ratio,
                    "multi_organic_extra_natural_traffic": natural_traffic - primary,
                    "variants": [
                        {
                            "variant_asin": row["variant_asin"],
                            "natural_traffic_ratio": row["score"] / natural_traffic if natural_traffic else 0.0,
                            "natural_position_days": row["natural_position_days"],
                            "average_natural_rank": row["average_natural_rank"],
                            "evidence_ids": [row["evidence_id"]],
                        }
                        for row in sorted(rows, key=lambda row: row["score"], reverse=True)
                    ],
                    "evidence_ids": list(dict.fromkeys(row["evidence_id"] for row in rows)),
                }
            )

    for parent in parents:
        if parent not in evidence_by_parent and parent not in incomplete_parents and variants_by_parent.get(parent):
            missing.append(f"Sif 未返回父 ASIN {parent} 的变体自然位明细。")
    status: ProcessedDataStatus = "available" if not missing else ("partial" if records else "unavailable")
    return MultiVariantOrganicPositionModule(
        status=status,
        records=records,
        evidence_ids=list(dict.fromkeys(item for ids in evidence_by_parent.values() for item in ids)),
        missing_reasons=list(dict.fromkeys(missing)),
    )


def _payload_with_keys(value: Any, keys: set[str]) -> dict[str, Any] | None:
    """Find a structured Sif data object without treating arbitrary text as data."""
    if isinstance(value, dict):
        if keys.issubset(value):
            return value
        for nested_key in ("structuredContent", "data", "result", "payload"):
            found = _payload_with_keys(value.get(nested_key), keys)
            if found is not None:
                return found
        content = value.get("content")
        if isinstance(content, list):
            for part in content:
                found = _payload_with_keys(part, keys)
                if found is not None:
                    return found
        text = value.get("text")
        if text is not None:
            parsed: Any = text
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    parsed = None
            found = _payload_with_keys(parsed, keys)
            if found is not None:
                return found
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().rstrip("%"))
        except ValueError:
            return None
    return None


def _int_number(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _asin_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _ASIN_IN_TEXT.search(value.upper())
    return match.group(0) if match else None


def build_processor_context(
    scope: QueryScope, result: SpecialistResult
) -> tuple[str, set[str], set[str], set[str]]:
    """Collect every returned evidence item, in provider order, without sampling."""
    evidence_ids: set[str] = set()
    source_names: set[str] = set()
    truncated_sources: set[str] = set()
    selected: list[dict[str, Any]] = []

    for deliverable in result.deliverables:
        if deliverable.get("type") != "competitor_research":
            continue
        tools, results = deliverable.get("tools"), deliverable.get("results")
        if not isinstance(tools, list) or not isinstance(results, list):
            continue
        for business_tool, tool_result in zip(tools, results, strict=True):
            if business_tool not in SECTION_BY_TOOL or not isinstance(tool_result, dict) or not tool_result.get("ok"):
                continue
            capability = CAPABILITY_BY_TOOL.get(business_tool)
            if capability is None:
                continue
            source_name = capability.title
            for evidence in tool_result.get("evidence", []):
                if not isinstance(evidence, dict) or not isinstance(evidence.get("evidence_id"), str):
                    continue
                evidence_id = evidence["evidence_id"]
                query = evidence.get("query") if isinstance(evidence.get("query"), dict) else {}
                selected.append(
                    {
                        "section": SECTION_BY_TOOL[business_tool],
                        "source_name": source_name,
                        "evidence_id": evidence_id,
                        "query": query,
                        "data": evidence.get("data"),
                    }
                )
                evidence_ids.add(evidence_id)
                source_names.add(source_name)

    payload = {
        "confirmed_scope": {
            "own_asin": scope.own_asin,
            "competitor_asins": list(dict.fromkeys(scope.competitor_asins)),
            "marketplace": scope.marketplaces[0] if scope.marketplaces else None,
        },
        "source_names": sorted(source_names),
        "truncated_source_names": sorted(truncated_sources),
        "evidence": selected,
    }
    return json.dumps(payload, ensure_ascii=False, default=str), evidence_ids, source_names, truncated_sources


def validate_competitor_profiles(
    profiles: CompetitorProfiles,
    *,
    scope: QueryScope,
    evidence_ids: set[str],
    source_names: set[str],
    truncated_sources: set[str],
) -> CompetitorProfiles:
    """Enforce provenance strictly and downgrade unsupported policy claims.

    Fabricated provenance (unknown source or evidence, duplicated section, scope
    mismatch) fails the attempt.  A claim about a competitor's private metric is
    different: it is a violation of policy rather than a broken reference, so the
    offending fact or note is dropped and the rest of the profile is kept.  That
    keeps a compliant "this metric is unavailable" note while never presenting a
    private metric as data, and it no longer burns all attempts on wording the
    prompt already asks the model to avoid.
    """

    expected = list(dict.fromkeys(scope.competitor_asins))
    if profiles.own_asin != scope.own_asin or profiles.marketplace != (scope.marketplaces[0] if scope.marketplaces else ""):
        raise ValueError("COMPETITOR_PROFILE_SCOPE_MISMATCH")
    if [profile.competitor_asin for profile in profiles.profiles] != expected:
        raise ValueError("COMPETITOR_PROFILE_ASIN_MISMATCH")

    validated_profiles = []
    for profile in profiles.profiles:
        seen_sections: set[str] = set()
        validated_sections = []
        for section in profile.sections:
            if section.section in seen_sections:
                raise ValueError("COMPETITOR_PROFILE_DUPLICATE_SECTION")
            seen_sections.add(section.section)
            if not set(section.source_names).issubset(source_names):
                raise ValueError("COMPETITOR_PROFILE_UNKNOWN_SOURCE")

            kept_facts = []
            for fact in section.facts:
                if not set(fact.source_names).issubset(source_names) or not set(fact.evidence_ids).issubset(evidence_ids):
                    raise ValueError("COMPETITOR_PROFILE_UNKNOWN_EVIDENCE")
                if _names_private_metric(fact.field) or _asserts_private_metric(fact.comparison):
                    continue
                kept_facts.append(fact)

            updated = section.model_copy(
                update={
                    "facts": kept_facts,
                    "key_gaps": [item for item in section.key_gaps if not _asserts_private_metric(item)],
                    "limitations": [item for item in section.limitations if not _asserts_private_metric(item)],
                }
            )
            if section.facts and not kept_facts and updated.status == "available":
                # Nothing usable survived, so do not keep claiming this theme is covered.
                updated = updated.model_copy(update={"status": "unavailable"})
            if set(updated.source_names) & truncated_sources and updated.status == "available":
                updated = updated.model_copy(update={"status": "partial"})
            validated_sections.append(updated)

        if not set(profile.source_names).issubset(source_names):
            raise ValueError("COMPETITOR_PROFILE_UNKNOWN_SOURCE")
        validated_profiles.append(
            profile.model_copy(
                update={
                    "sections": validated_sections,
                    "limitations": [item for item in profile.limitations if not _asserts_private_metric(item)],
                }
            )
        )
    return profiles.model_copy(update={"profiles": validated_profiles})


def _names_private_metric(value: str | None) -> bool:
    """True when a private metric is named at all (a compared field cannot be one)."""

    return isinstance(value, str) and any(term in value.casefold() for term in PRIVATE_METRIC_TERMS)


def _asserts_private_metric(value: str | None) -> bool:
    """True when free text makes a claim about a private metric.

    A value always makes it a claim.  Without a value, only an explicit
    unavailability statement is allowed, because policy requires reporting those
    as unavailable; naming the metric some other way is still treated as a claim.
    """

    if not isinstance(value, str):
        return False
    folded = value.casefold()
    if not any(term in folded for term in PRIVATE_METRIC_TERMS):
        return False
    if _METRIC_VALUE.search(folded):
        return True
    return not any(marker in folded for marker in UNAVAILABLE_MARKERS)

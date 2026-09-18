"""Build deterministic competitor report modules from returned Sif fields."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from .competitor_research_catalog import CAPABILITY_BY_TOOL
from .models import QueryScope, SpecialistResult
from .models import (
    CompetitorDataModules,
    MultiVariantOrganicPositionModule,
    ProcessedDataStatus,
    RecommendationPlacementModule,
    TrafficKeywordLookupModule,
    TrafficKeywordReverseLookupModule,
)


class CompetitorDataProcessor:
    """A bounded structured-LLM transformation, separate from report writing."""

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
        advertising_data = _first_mapping(
            overview.get("ad"),
            overview.get("adChannelBreakdown"),
            overview_data.get("adChannelBreakdown"),
        )
        records.append(
            {
                "asin_role": role,
                "parent_asin": parent,
                "listing_natural_traffic": _traffic_score_ratio(
                    _first_value(overview_data, "nf", "naturalScore", "natural")
                ),
                "listing_ad_traffic": _traffic_score_ratio(
                    _first_value(overview_data, "ad", "adScore", "advertising")
                ),
                "advertising_traffic_distribution": {
                    "sp": _traffic_score_ratio(_first_value(advertising_data, "sp", "spScore")),
                    "sp_recommend": _traffic_score_ratio(
                        _first_value(advertising_data, "recommend", "recSp", "recSpScore")
                    ),
                    "sb": _traffic_score_ratio(_first_value(advertising_data, "sb", "sbScore")),
                    "sbv": _traffic_score_ratio(_first_value(advertising_data, "sbv", "sbvScore")),
                },
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


def _first_mapping(*values: Any) -> dict[str, Any]:
    return next((value for value in values if isinstance(value, dict)), {})


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _traffic_score_ratio(value: Any) -> dict[str, float | None]:
    """Normalize a verified Sif traffic metric without changing its ratio unit."""
    if not isinstance(value, dict):
        return {"score": None, "ratio": None}
    return {
        "score": _number(_first_value(value, "score", "trafficScore")),
        "ratio": _number(_first_value(value, "ratio", "scoreRatio", "trafficRatio")),
    }


def _int_number(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _asin_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _ASIN_IN_TEXT.search(value.upper())
    return match.group(0) if match else None

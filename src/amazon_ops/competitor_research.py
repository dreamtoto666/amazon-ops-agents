"""Bounded, read-only Sif business operations for competitor comparison."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any

from .listing.mcp import MCPGatewayError, MCPProviderClient, MCPToolResult
from .models import DateRange


# Provider concurrency stays bounded on purpose: it is a throttle, not a filter.
# Letting every call run at once provokes provider-side rate limiting, which
# loses evidence outright.
MAX_PARALLEL_CALLS = 2
_ASIN_PATTERN = re.compile(r"\b[A-Z0-9]{10}\b")


@dataclass(frozen=True)
class ObservationPeriod:
    """The date context actually available to a Sif call."""

    start: date
    end: date
    requested: bool
    maturity: str


# These are Sif call contracts, not business-tool contracts.  A business tool
# can compose calls with different window behavior, so provenance is attached
# to each returned evidence item rather than inferred later from its wrapper.
_WINDOW_MODE_BY_SIF_TOOL = {
    "ops_get_asin_traffic_trend_detail": "requested_end_day",
    "ads_get_campaign_contribution_breakdown": "requested_range",
    "ads_get_ad_group_keyword_breakdown": "requested_end_day",
}


def _parsed_content(payload: Any) -> Any:
    """Replace JSON text blocks with their parsed value before any bounding.

    Sif returns one JSON document inside a single text content block. Truncating
    that string by length cuts the JSON syntax itself, so a large payload (a long
    keyword or variant list) could not be decoded at all. Parsing first keeps the
    structure valid and only bounds the individual field values.
    """

    if not isinstance(payload, dict):
        return payload
    content = payload.get("content")
    if not isinstance(content, list):
        return payload
    blocks: list[Any] = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            try:
                blocks.append({**part, "text": json.loads(part["text"])})
                continue
            except json.JSONDecodeError:
                pass
        blocks.append(part)
    return {**payload, "content": blocks}


def _payload_dicts(value: Any) -> list[dict[str, Any]]:
    """Flatten a Sif envelope into every nested dict it contains."""

    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _variant_asins(data: Any) -> list[str]:
    """Read child-variant ASINs from the documented ``chars.dims[]`` list.

    Only ``chars.dims[].val`` identifies a variant, and the provider marks the
    parent Listing itself with ``isVariant: false``.  Scanning the whole payload
    for ASIN-shaped tokens instead used to match payload keys such as
    ``totalScore`` and ``resultType``, so provider quota was spent on invalid
    calls and the real variants were left unfetched.
    """

    for candidate in _payload_dicts(data):
        chars = candidate.get("chars")
        if not isinstance(chars, dict) or not isinstance(chars.get("dims"), list):
            continue
        asins: list[str] = []
        for dimension in chars["dims"]:
            if not isinstance(dimension, dict) or dimension.get("isVariant") is False:
                continue
            match = _ASIN_PATTERN.search(str(dimension.get("val") or "").upper())
            if match:
                asins.append(match.group(0))
        return asins
    return []


class CompetitorResearchService:
    """Compose reviewed Sif read tools into stable research operations."""

    def __init__(self, sif_client: MCPProviderClient) -> None:
        self._sif = sif_client

    def compare_asin_sales(self, *, own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        calls = [("ops_get_asin_sales_list", {"asins": asins, "country": country})]
        calls.extend(("ops_get_asin_sales_trend", {"asin": asin, "country": country}) for asin in asins)
        return self._run("销量对比", calls)

    def analyze_traffic_structure(self, *, own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        calls = []
        for asin in asins:
            calls.extend([
                ("ops_get_listing_traffic_overview", {"asin": asin, "country": country}),
                ("ops_get_listing_traffic_structure", {
                    "asin": asin, "country": country, "pageNum": 1, "pageSize": 40,
                }),
                ("ops_get_asin_traffic_trend", {"asin": asin, "country": country}),
            ])
        base = self._run("流量结构对比", calls)
        detail_calls = self._variant_organic_detail_calls(base, country)
        if not detail_calls:
            return base
        detail = self._run("多变体自然位明细", detail_calls)
        return {
            **base,
            "ok": bool(base["evidence"] or detail["evidence"]),
            "summary": {
                "title": "流量结构与多变体自然位对比",
                "completed_calls": base["summary"]["completed_calls"] + detail["summary"]["completed_calls"],
                "requested_calls": base["summary"]["requested_calls"] + detail["summary"]["requested_calls"],
            },
            "evidence": [*base["evidence"], *detail["evidence"]],
            "errors": [*base["errors"], *detail["errors"]],
            "limitations": list(dict.fromkeys([*base["limitations"], *detail["limitations"]])),
        }

    def compare_traffic_keywords(self, *, own_asin: str, competitor_asins: list[str], country: str, period: DateRange | None = None) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        observation = self._resolve_observation_period(period)
        calls = []
        for asin in asins:
            calls.extend([
                ("market_get_asin_keyword_signals", {
                    "asin": asin, "country": country, "topN": 300,
                    # Parent-ASIN listing search is the agreed source scope;
                    # no child ASIN is supplied by the caller.
                    "listingSearch": True,
                }),
                ("ops_get_listing_keyword_distribution", {
                    "asin": asin, "country": country, "pageNum": 1, "pageSize": 200,
                }),
                ("ops_get_asin_traffic_trend_detail", {
                    "asin": asin, "country": country, "endDay": observation.end.isoformat(),
                    "granularity": "day", "pageNum": 1, "pageSize": 200, "desc": True,
                }),
            ])
        return self._run("流量词与广告词对比", calls, observation=observation)

    def replay_operations_history(self, *, own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        calls = []
        for asin in asins:
            calls.extend([
                ("ops_get_asin_traffic_trend", {"asin": asin, "country": country}),
                ("ads_get_asin_ad_historical_feature_profile", {"asin": asin, "country": country}),
                ("ads_get_asin_ad_traffic_trend", {"asin": asin, "country": country}),
                ("ads_get_asin_campaign_changes", {"asin": asin, "country": country}),
            ])
        return self._run("运营历史复盘", calls)

    def inspect_ad_architecture(self, *, own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        calls = []
        for asin in asins:
            calls.extend([
                ("ads_get_asin_ad_structure", {"asin": asin, "country": country}),
                ("ads_get_asin_campaign_contribution_overview", {
                    "asin": asin, "country": country, "limit": 40,
                }),
            ])
        return self._run("广告架构对比", calls)

    def inspect_campaign(self, *, asin: str, campaign_id: str, country: str, period: DateRange | None = None) -> dict[str, Any]:
        self._require_single(asin, "ASIN_REQUIRED")
        self._require_single(campaign_id, "CAMPAIGN_ID_REQUIRED")
        self._require_country(country)
        observation = self._resolve_observation_period(period)
        calls = [
            ("ads_get_campaign_structure", {"asin": asin, "campaignId": campaign_id, "country": country}),
            ("ads_get_campaign_traffic_trend", {"asin": asin, "campaignId": campaign_id, "country": country}),
            ("ads_get_campaign_contribution_breakdown", {
                "asin": asin, "campaignId": campaign_id, "country": country,
                "start_date": observation.start.isoformat(), "end_date": observation.end.isoformat(),
                "breakdown_by": "keyword", "limit": 100,
            }),
        ]
        return self._run("Campaign 拆解", calls, observation=observation)

    def inspect_ad_group(self, *, asin: str, campaign_id: str, ad_group_id: str, country: str, period: DateRange | None = None) -> dict[str, Any]:
        self._require_single(asin, "ASIN_REQUIRED")
        self._require_single(campaign_id, "CAMPAIGN_ID_REQUIRED")
        self._require_single(ad_group_id, "AD_GROUP_ID_REQUIRED")
        self._require_country(country)
        observation = self._resolve_observation_period(period)
        calls = [
            ("ads_get_ad_group_traffic_trend", {
                "asin": asin, "campaignId": campaign_id, "adGroupId": ad_group_id, "country": country,
            }),
            ("ads_get_ad_group_keyword_breakdown", {
                "asin": asin, "campaignId": campaign_id, "adGroupId": ad_group_id,
                "country": country, "date": observation.end.isoformat(),
            }),
        ]
        return self._run("广告组与广告词拆解", calls, observation=observation)

    def analyze_recommendation_traffic(self, *, own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        self._require_country(country)
        asins = self._comparison_asins(own_asin, competitor_asins)
        return self._run("推荐专栏流量对比", [
            ("ops_get_listing_traffic_overview", {"asin": asin, "country": country}) for asin in asins
        ])

    @staticmethod
    def _variant_organic_detail_calls(result: dict[str, Any], country: str) -> list[tuple[str, dict[str, Any]]]:
        """Expand parent Listing results into same-period, per-variant NF detail calls.

        Variant identities come from the documented ``chars.dims[]`` list so the
        parent Listing itself is never requested as a variant.
        """
        calls: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        week_start = date.today() - timedelta(days=(date.today().weekday() + 1) % 7)
        for evidence in result.get("evidence", []):
            if not isinstance(evidence, dict) or evidence.get("tool") != "ops_get_listing_traffic_structure":
                continue
            data = evidence.get("data")
            for asin in _variant_asins(data):
                if asin in seen:
                    continue
                seen.add(asin)
                calls.append(("ops_get_asin_traffic_trend_detail", {
                    "asin": asin, "country": country, "endDay": week_start.isoformat(),
                    "granularity": "week", "keywordType": "nf", "sortBy": "score",
                    "pageNum": 1, "pageSize": 200, "desc": True,
                }))
        return calls

    @staticmethod
    def _comparison_asins(own_asin: str, competitors: list[str]) -> list[str]:
        own = own_asin.strip()
        unique_competitors = list(dict.fromkeys(item.strip() for item in competitors if item.strip() and item.strip() != own))
        if not own:
            raise ValueError("OWN_ASIN_REQUIRED")
        if not unique_competitors:
            raise ValueError("COMPETITOR_ASIN_REQUIRED")
        return [own, *unique_competitors]

    @staticmethod
    def _require_country(country: str) -> None:
        if not isinstance(country, str) or not country.strip():
            raise ValueError("COUNTRY_REQUIRED")

    @staticmethod
    def _require_single(value: str, code: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(code)

    @staticmethod
    def _resolve_window(period: DateRange | None, today: date | None = None) -> tuple[date, date]:
        """Use the caller's confirmed period, otherwise the last complete week.

        An unusable period is rejected rather than replaced: silently querying a
        different window than the user asked for is exactly what this guards
        against.
        """

        if period is None:
            return CompetitorResearchService._recent_complete_window(today)
        if period.start > period.end:
            raise ValueError("PERIOD_INVALID")
        if period.end > (today or date.today()):
            raise ValueError("PERIOD_IN_FUTURE")
        return period.start, period.end

    @staticmethod
    def _resolve_observation_period(period: DateRange | None, today: date | None = None) -> ObservationPeriod:
        start, end = CompetitorResearchService._resolve_window(period, today)
        current_day = today or date.today()
        return ObservationPeriod(
            start=start,
            end=end,
            requested=period is not None,
            maturity="UNMATURED" if period is not None and end == current_day else "COMPLETE",
        )

    @staticmethod
    def _recent_complete_window(today: date | None = None) -> tuple[date, date]:
        """Return the last seven complete days, ending yesterday.

        Sif data for the current day is still incomplete, so the default window
        ends yesterday.  The previous ``_latest_week_start`` helper returned a
        partial calendar week whose six-day span reached up to a week into the
        future, and returned a different day depending on the weekday of the run.
        """

        end = (today or date.today()) - timedelta(days=1)
        return end - timedelta(days=6), end

    def _run(
        self,
        title: str,
        calls: list[tuple[str, dict[str, Any]]],
        *,
        observation: ObservationPeriod | None = None,
    ) -> dict[str, Any]:
        completed: list[MCPToolResult] = []
        errors: list[dict[str, str]] = []
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS) as executor:
            futures = {executor.submit(self._sif.call, tool, arguments): (tool, arguments) for tool, arguments in calls}
            for future in as_completed(futures):
                tool, arguments = futures[future]
                try:
                    completed.append(future.result())
                except MCPGatewayError as exc:
                    errors.append({"tool": tool, "code": exc.code})
                except Exception:
                    errors.append({"tool": tool, "code": "SIF_TOOL_FAILED"})
        evidence = [
            {
                "evidence_id": result.call_id, "tool": result.tool,
                "query": result.arguments, "fetched_at": result.finished_at.isoformat(),
                "data": _parsed_content(result.payload),
                "observation_window": self._evidence_observation_window(result.tool, observation),
            }
            for result in completed
        ]
        return {
            "ok": bool(completed), "summary": {"title": title, "completed_calls": len(completed), "requested_calls": len(calls)},
            "untrusted_business_data": True,
            "evidence": evidence,
            "observation_window": self._summarize_observation_window(evidence, observation),
            "limitations": [
                "仅包含 Sif 实际返回的公开可见研究数据。",
                "竞品私有花费、竞价、ACOS、ROAS、订单和 CVR 不可从本工具推断。",
            ], "errors": errors,
        }

    @staticmethod
    def _evidence_observation_window(
        tool: str, observation: ObservationPeriod | None,
    ) -> dict[str, Any]:
        mode = _WINDOW_MODE_BY_SIF_TOOL.get(tool, "provider_default")
        requested = (
            {"start": observation.start.isoformat(), "end": observation.end.isoformat()}
            if observation and observation.requested
            else None
        )
        if mode == "requested_range" and observation:
            effective: dict[str, str] | None = {"start": observation.start.isoformat(), "end": observation.end.isoformat()}
        elif mode == "requested_end_day" and observation:
            effective = {"end": observation.end.isoformat()}
        else:
            effective = None
        return {
            "mode": mode,
            "requested": requested,
            "effective": effective,
            "maturity": observation.maturity if observation and mode != "provider_default" else "UNKNOWN",
        }

    @staticmethod
    def _summarize_observation_window(
        evidence: list[dict[str, Any]], observation: ObservationPeriod | None,
    ) -> dict[str, Any]:
        modes = {item["observation_window"]["mode"] for item in evidence}
        if modes == {"requested_range"}:
            alignment = "fully_aligned"
        elif modes == {"provider_default"}:
            alignment = "unaligned"
        else:
            alignment = "partially_aligned"
        return {
            "alignment": alignment,
            "requested": (
                {"start": observation.start.isoformat(), "end": observation.end.isoformat()}
                if observation and observation.requested
                else None
            ),
            "maturity": observation.maturity if observation else "COMPLETE",
        }

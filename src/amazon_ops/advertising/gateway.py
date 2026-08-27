from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from amazon_ops.listing.mcp import (
    MCPProviderClient,
    MCPToolResult,
    lingxing_mcp_config,
)
from amazon_ops.listing.mcp_transport import StreamableHTTPMCPTransport

from .models import AdShop, DiagnosticPeriod


@dataclass(frozen=True)
class AdvertisingReport:
    tool: str
    arguments: dict[str, Any]
    rows: list[dict[str, Any]]
    total: int
    trace: MCPToolResult


class AdvertisingDataGateway(Protocol):
    def list_shops(self) -> list[AdShop]: ...

    def campaign_report(
        self,
        *,
        profile_ids: list[str],
        period: DiagnosticPeriod,
        campaign_ids: list[str] | None = None,
    ) -> AdvertisingReport: ...

    def attribution_report(
        self,
        *,
        tool: str,
        profile_ids: list[str],
        period: DiagnosticPeriod,
        campaign_ids: list[str],
    ) -> AdvertisingReport: ...


def build_lingxing_advertising_client() -> MCPProviderClient:
    return MCPProviderClient(
        config=lingxing_mcp_config(),
        transport=StreamableHTTPMCPTransport(),
    )


class LingxingAdvertisingGateway:
    """Narrow, read-only adapter for the advertising diagnostic workflow."""

    _ATTRIBUTION_ARGUMENTS = {
        "ad_campaign_group_report": {
            "page": 1,
            "length": 100,
            "sort_field": "spends",
            "sort_type": "desc",
            "with_ring": 0,
        },
        "ad_campaign_keyword_report": {
            "page": 1,
            "length": 100,
            "sort_field": "spends",
            "sort_type": "desc",
        },
        "ad_campaign_targeting_report": {
            "page": 1,
            "length": "100",
            "sort_field": "spends",
            "sort_type": "desc",
            "with_ring": 0,
        },
        "ad_campaign_search_term_report": {
            "page": 1,
            "length": 100,
            "sort_field": "spends",
            "sort_type": "desc",
            "with_ring": False,
        },
    }

    def __init__(self, client: MCPProviderClient | None = None) -> None:
        self.client = client or build_lingxing_advertising_client()

    def list_shops(self) -> list[AdShop]:
        trace = self.client.call("ad_auth_shops", {})
        rows, _ = self._extract_rows(trace.payload)
        shops: list[AdShop] = []
        for row in rows:
            profile_id = self._text(row.get("profile_id"))
            alias = self._text(row.get("alias"))
            if not profile_id or not alias:
                continue
            shops.append(
                AdShop(
                    profile_id=profile_id,
                    sid=self._integer(row.get("sid")),
                    store_id=self._integer(row.get("store_id")),
                    alias=alias,
                    country=self._text(row.get("country")),
                    marketplace_id=self._text(row.get("marketplace_string_id")),
                    account_type=self._text(row.get("type")),
                )
            )
        return shops

    def campaign_report(
        self,
        *,
        profile_ids: list[str],
        period: DiagnosticPeriod,
        campaign_ids: list[str] | None = None,
    ) -> AdvertisingReport:
        arguments: dict[str, Any] = {
            "report_date": self._period(period),
            "profile_ids": profile_ids,
            "page": 1,
            "length": 200,
            "sort_field": "spends",
            "sort_type": "desc",
        }
        if campaign_ids:
            # The campaign report only supports name search. Filtering by ID is
            # performed after retrieval; downstream reports receive IDs directly.
            requested = set(campaign_ids)
        else:
            requested = set()
        trace = self.client.call("ad_campaign_report", arguments)
        rows, total = self._extract_rows(trace.payload)
        if requested:
            rows = [row for row in rows if self._text(row.get("campaign_id")) in requested]
        return AdvertisingReport(
            tool="ad_campaign_report",
            arguments=arguments,
            rows=rows,
            total=total,
            trace=trace,
        )

    def attribution_report(
        self,
        *,
        tool: str,
        profile_ids: list[str],
        period: DiagnosticPeriod,
        campaign_ids: list[str],
    ) -> AdvertisingReport:
        if tool not in self._ATTRIBUTION_ARGUMENTS:
            raise ValueError(f"unsupported attribution report: {tool}")
        arguments = {
            **self._ATTRIBUTION_ARGUMENTS[tool],
            "report_date": self._period(period),
            "profile_ids": profile_ids,
        }
        if tool in {"ad_campaign_group_report", "ad_campaign_keyword_report", "ad_campaign_targeting_report"}:
            arguments["campaign_id"] = campaign_ids
        elif campaign_ids:
            # Search-term report accepts a single campaign ID. Bound the first
            # version to the highest-priority anomalous campaign.
            arguments["campaign_id"] = campaign_ids[0]
        trace = self.client.call(tool, arguments)
        rows, total = self._extract_rows(trace.payload)
        return AdvertisingReport(
            tool=tool,
            arguments=arguments,
            rows=rows,
            total=total,
            trace=trace,
        )

    @classmethod
    def _extract_rows(cls, payload: Any) -> tuple[list[dict[str, Any]], int]:
        decoded = cls._decode_payload(payload)
        if not isinstance(decoded, dict):
            return [], 0
        if decoded.get("code") not in {None, 0, 200}:
            raise RuntimeError("领星返回了失败状态。")
        container = decoded.get("data")
        if isinstance(container, dict) and container.get("success") is False:
            raise RuntimeError("领星返回了失败状态。")
        nested = container.get("data") if isinstance(container, dict) else container
        rows = nested if isinstance(nested, list) else []
        total_value = decoded.get("total")
        if isinstance(container, dict):
            total_value = container.get("recordsFiltered", total_value)
        total = cls._integer(total_value) or len(rows)
        return [row for row in rows if isinstance(row, dict)], total

    @staticmethod
    def _decode_payload(payload: Any) -> Any:
        if isinstance(payload, dict):
            content = payload.get("content")
            if isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    if isinstance(text, str):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            continue
            structured = payload.get("structuredContent")
            if structured is not None:
                return structured
        return payload

    @staticmethod
    def _period(period: DiagnosticPeriod) -> str:
        return f"{period.start.isoformat()} - {period.end.isoformat()}"

    @staticmethod
    def _text(value: Any) -> str | None:
        if value is None:
            return None
        result = str(value).strip()
        return result or None

    @staticmethod
    def _integer(value: Any) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

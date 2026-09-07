from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Protocol

from amazon_ops.listing.mcp import (
    MCPGatewayError,
    MCPProvider,
    MCPProviderClient,
    MCPToolResult,
    lingxing_mcp_config,
)
from amazon_ops.listing.mcp_transport import StreamableHTTPMCPTransport

from .models import AdShop, DiagnosticPeriod
from .openapi_transport import LingxingOpenAPIError, LingxingOpenAPITransport


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
        asins: list[str] | None = None,
    ) -> AdvertisingReport: ...

    def attribution_report(
        self,
        *,
        tool: str,
        profile_ids: list[str],
        period: DiagnosticPeriod,
        campaign_ids: list[str],
    ) -> AdvertisingReport: ...


@dataclass(frozen=True)
class LingxingCatalogEntry:
    """A validated, read-only business tool resolved from LingXing's catalog."""

    tool_id: str
    catalog_version: str
    schema_version: str
    tool_version_id: int
    required_fields: frozenset[str]


_ADVERTISING_CATALOG_TOOLS = frozenset(
    {
        "ad_auth_shops",
        "ad_campaign_report",
        "ad_campaign_group_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_search_term_report",
        # Product-scoped SB resolution.  This is deliberately used only before
        # graph construction, never as an Agent-facing diagnostic tool.
        "advertising_analyze_search_term",
    }
)
_CATALOG_REFRESH_CODES = frozenset({"MCP_TOOL_UNAVAILABLE", "MCP_CATALOG_VERSION_STALE"})


def build_lingxing_advertising_client() -> MCPProviderClient:
    return MCPProviderClient(
        config=lingxing_mcp_config(),
        transport=StreamableHTTPMCPTransport(),
    )


class LingxingOpenAPIAdvertisingGateway:
    """Fixed, read-only Open API adapter for core campaign diagnostics.

    Each endpoint is explicit here rather than dynamically discovered.  A
    failed Open API request raises a safe error; this adapter never falls back
    to MCP, preventing mixed report definitions in one diagnostic run.
    """

    _CAMPAIGN_ENDPOINTS = {
        "sp": "/pb/openapi/newad/spCampaignReports",
        "sb": "/pb/openapi/newad/hsaCampaignReports",
        "sd": "/pb/openapi/newad/sdCampaignReports",
    }
    _PRODUCT_AD_ENDPOINTS = (
        "/pb/openapi/newad/spProductAdReports",
        "/pb/openapi/newad/listHsaProductAdReport",
        "/pb/openapi/newad/sdProductAdReports",
    )
    _DETAIL_ENDPOINTS = {
        "ad_campaign_group_report": "/pb/openapi/newad/spAdGroupReports",
        "ad_campaign_keyword_report": "/pb/openapi/newad/spKeywordReports",
        "ad_campaign_targeting_report": "/pb/openapi/newad/spTargetReports",
        "ad_campaign_search_term_report": "/pb/openapi/newad/queryWordReports",
    }

    def __init__(
        self,
        transport: LingxingOpenAPITransport | None = None,
        sb_scope_gateway: "LingxingAdvertisingGateway | None" = None,
    ) -> None:
        self.transport = transport or LingxingOpenAPITransport()
        self._sb_scope_gateway = sb_scope_gateway or LingxingAdvertisingGateway()

    def list_shops(self) -> list[AdShop]:
        payload = self.transport.get("/erp/sc/data/seller/lists")
        self._require_success(payload)
        result = []
        for row in payload.get("data", []):
            if not isinstance(row, dict) or row.get("sid") is None:
                continue
            result.append(AdShop(profile_id=str(row["sid"]), sid=int(row["sid"]), alias=str(row.get("name") or "未命名店铺")))
        return result

    def authorized_advertising_shop_labels(self) -> set[str]:
        """Return only human-facing aliases that have an ad Profile mapping."""
        return {
            shop.alias
            for shop in self._sb_scope_gateway.list_shops()
            if shop.alias and str(shop.profile_id).isdigit()
        }

    def campaign_report(self, *, profile_ids: list[str], period: DiagnosticPeriod, campaign_ids: list[str] | None = None, asins: list[str] | None = None) -> AdvertisingReport:
        rows: list[dict[str, Any]] = []
        queries = []
        for sid in profile_ids:
            for kind, path in self._CAMPAIGN_ENDPOINTS.items():
                for report_date in self._dates(period):
                    report_rows = self._paged(path, {"sid": int(sid), "report_date": report_date, "show_detail": 1})
                    rows.extend(self._normalize_campaign(item, kind, str(sid)) for item in report_rows)
                    queries.append({"endpoint": path, "sid": str(sid), "report_date": report_date})
        scoped_campaigns = self._campaigns_for_asins(profile_ids, period, asins or []) if asins else None
        if campaign_ids or scoped_campaigns is not None:
            requested = set(campaign_ids or [])
            if scoped_campaigns is not None:
                requested = requested & scoped_campaigns if requested else scoped_campaigns
            rows = [row for row in rows if str(row.get("campaign_id")) in requested]
        return AdvertisingReport(tool="openapi_campaign_report", arguments={"queries": queries}, rows=rows, total=len(rows), trace=self._trace("openapi_campaign_report", {"queries": queries}))

    def _campaigns_for_asins(self, profile_ids: list[str], period: DiagnosticPeriod, asins: list[str]) -> set[str]:
        """Resolve selected product ASINs before campaign inspection.

        The campaign report lacks an ASIN field, so querying product-ad reports
        first is the only safe way to avoid ranking unrelated campaigns.
        """
        result: set[str] = set()
        requested = set(asins)
        for sid in profile_ids:
            for report_date in self._dates(period):
                for endpoint in self._PRODUCT_AD_ENDPOINTS:
                    for row in self._paged(endpoint, {"sid": int(sid), "report_date": report_date, "show_detail": 1}):
                        if str(row.get("asin") or row.get("advertised_asin") or "") in requested and row.get("campaign_id") is not None:
                            result.add(str(row["campaign_id"]))
        return result

    def resolve_campaign_ids(
        self,
        *,
        sid: str,
        asins: list[str],
        period: DiagnosticPeriod,
        shop_label: str | None = None,
    ) -> set[str]:
        """Resolve product scope privately across report types.

        SP/SD expose product ASINs in their Open API reports.  SB is resolved
        through LingXing's read-only advertising-shop and campaign-report MCP
        tools.  The ASIN batches and raw MCP rows never leave this method.
        """
        campaign_ids = self._campaigns_for_asins([sid], period, asins)
        campaign_ids.update(
            self._sb_scope_gateway.resolve_sb_campaign_ids(
                sid=sid,
                asins=asins,
                period=period,
                shop_label=shop_label,
            )
        )
        return campaign_ids

    def attribution_report(self, *, tool: str, profile_ids: list[str], period: DiagnosticPeriod, campaign_ids: list[str]) -> AdvertisingReport:
        path = self._DETAIL_ENDPOINTS.get(tool)
        if not path:
            raise LingxingOpenAPIError(f"Open API 尚未迁移所需明细报表：{tool}。")
        rows: list[dict[str, Any]] = []
        requested = set(map(str, campaign_ids))
        for sid in profile_ids:
            for report_date in self._dates(period):
                source_rows = self._paged(path, {"sid": int(sid), "report_date": report_date, "show_detail": 1})
                rows.extend(
                    self._normalize_daily_detail(item, tool, str(sid))
                    for item in source_rows
                    if str(item.get("campaign_id") or "") in requested
                )
        return AdvertisingReport(tool=tool, arguments={"source": "openapi", "campaign_id": campaign_ids}, rows=rows, total=len(rows), trace=self._trace(tool, {"source": "openapi"}))

    def _paged(self, path: str, base: dict[str, Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self.transport.post(path, payload={**base, "offset": offset, "length": 200}, headers={"X-API-VERSION": "2"})
            self._require_success(response)
            page = [row for row in response.get("data", []) if isinstance(row, dict)]
            result.extend(page)
            total = int(response.get("total") or len(page))
            offset += len(page)
            if not page or offset >= total:
                return result

    @staticmethod
    def _normalize_campaign(row: dict[str, Any], kind: str, sid: str) -> dict[str, Any]:
        allowed = {"campaign_id", "profile_id", "impressions", "clicks", "cost", "sales", "orders", "units", "report_date", "targeting_type", "tactic"}
        result = {key: row[key] for key in allowed if key in row}
        result.update({"profile_id": str(row.get("profile_id") or sid), "spends": row.get("cost", 0), "sponsored_type": kind})
        return result

    @staticmethod
    def _normalize_detail(row: dict[str, Any], tool: str, sid: str) -> dict[str, Any]:
        name = row.get("query") if tool.endswith("search_term_report") else row.get("keywordText")
        return {"campaign_id": str(row.get("campaignId") or ""), "profile_id": sid, "search_term" if tool.endswith("search_term_report") else "keyword": name, "spends": row.get("spends", 0), "sales": row.get("sales", 0), "orders": row.get("orders", 0), "impressions": row.get("impressions"), "clicks": row.get("clicks"), "cpc": row.get("cpc"), "ctr": row.get("ctr"), "cvr": row.get("cvr"), "acos": row.get("acos"), "roas": row.get("roas")}

    @staticmethod
    def _normalize_daily_detail(row: dict[str, Any], tool: str, sid: str) -> dict[str, Any]:
        text_keys = {
            "ad_campaign_search_term_report": ("search_term", ("query", "query_word", "customer_search_term")),
            "ad_campaign_keyword_report": ("keyword", ("keyword_text",)),
            "ad_campaign_targeting_report": ("targeting", ("targeting_text", "target_expression", "target")),
        }
        text_field, candidates = text_keys.get(tool, ("ad_group_id", ("ad_group_id",)))
        text = next((row.get(key) for key in candidates if row.get(key) is not None), None)
        return {
            "campaign_id": str(row.get("campaign_id") or ""), "profile_id": str(row.get("profile_id") or sid),
            text_field: str(text) if text is not None else None,
            "ad_group_id": str(row.get("ad_group_id") or "") or None,
            "spends": row.get("cost", 0), "sales": row.get("sales", 0), "orders": row.get("orders", 0),
            "impressions": row.get("impressions"), "clicks": row.get("clicks"), "match_type": row.get("match_type"),
            "bid": row.get("bid"), "budget": row.get("daily_budget") or row.get("budget"),
        }

    @staticmethod
    def _dates(period: DiagnosticPeriod) -> list[str]:
        from datetime import timedelta
        values, current = [], period.start
        while current <= period.end:
            values.append(current.isoformat())
            current += timedelta(days=1)
        return values

    @staticmethod
    def _require_success(payload: dict[str, Any]) -> None:
        if str(payload.get("code")) not in {"0", "200"}:
            raise LingxingOpenAPIError("领星 Open API 广告报表暂不可用；本次不会切换至 MCP 数据源。")

    @staticmethod
    def _trace(tool: str, arguments: dict[str, Any]) -> MCPToolResult:
        now = datetime.now(timezone.utc)
        return MCPToolResult(call_id=f"openapi-{tool}", provider=MCPProvider.LINGXING, tool=tool, arguments=arguments, payload={}, started_at=now, finished_at=now, duration_ms=0)


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
        self._catalog: dict[str, LingxingCatalogEntry] = {}
        self._catalog_lock = RLock()

    def list_shops(self) -> list[AdShop]:
        trace = self._call_catalog_tool("ad_auth_shops", {})
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
        asins: list[str] | None = None,
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
        trace = self._call_catalog_tool("ad_campaign_report", arguments)
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
        trace = self._call_catalog_tool(tool, arguments)
        rows, total = self._extract_rows(trace.payload)
        return AdvertisingReport(
            tool=tool,
            arguments=arguments,
            rows=rows,
            total=total,
            trace=trace,
        )

    def resolve_sb_campaign_ids(
        self,
        *,
        sid: str,
        asins: list[str],
        period: DiagnosticPeriod,
        shop_label: str | None = None,
    ) -> set[str]:
        """Resolve SB campaign IDs through the product-filtered campaign report.

        Search-term rows do not contain stable campaign IDs, so they must not
        be used to infer an activity scope.  The campaign report supports one
        ASIN per query and returns the authoritative SB campaign ID directly.
        All mappings and raw traces remain private to this boundary.
        """
        campaign_ids: set[str] = set()
        profile_id = self._profile_id_for_sid(sid, shop_label=shop_label)
        unique_asins = list(dict.fromkeys(value.strip() for value in asins if value and value.strip()))
        for asin in unique_asins:
            trace = self._call_catalog_tool(
                "ad_campaign_report",
                {
                    "report_date": self._period(period),
                    "profile_ids": [profile_id],
                    "page": 1,
                    "length": 100,
                    "sort_field": "spends",
                    "sort_type": "desc",
                    "asin": asin,
                    "ads_type": [["sb"]],
                },
            )
            rows, _ = self._extract_rows(trace.payload)
            campaign_ids.update(
                campaign_id
                for row in rows
                if (campaign_id := self._text(row.get("campaign_id")))
            )
        return campaign_ids

    def _profile_id_for_sid(self, sid: str, *, shop_label: str | None = None) -> int:
        shops = self.list_shops()
        selected = next(
            (
                shop for shop in shops
                if str(shop.sid) == str(sid) or str(shop.store_id) == str(sid)
            ),
            None,
        )
        if selected is None and shop_label:
            selected = next((shop for shop in shops if shop.alias == shop_label), None)
        if selected is None or not selected.profile_id or not str(selected.profile_id).isdigit():
            raise MCPGatewayError(
                "advertising profile is unavailable for the selected shop",
                provider=MCPProvider.LINGXING,
                tool="ad_auth_shops",
                code="MCP_TOOL_UNAVAILABLE",
            )
        return int(selected.profile_id)

    def _call_catalog_tool(self, tool_id: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Call a read-only LingXing business tool through help/search/action.

        Catalog values come from an external MCP and therefore only select a
        statically allowlisted tool ID.  They never supply executable behavior.
        """

        if tool_id not in _ADVERTISING_CATALOG_TOOLS:
            raise MCPGatewayError(
                f"advertising tool {tool_id} is not allowlisted",
                provider=MCPProvider.LINGXING,
                tool=tool_id,
                code="MCP_TOOL_NOT_ALLOWED",
            )

        for attempt in range(2):
            entry = self._resolve_catalog_tool(tool_id)
            missing = sorted(entry.required_fields.difference(arguments))
            if missing:
                raise MCPGatewayError(
                    f"missing required LingXing parameters: {', '.join(missing)}",
                    provider=MCPProvider.LINGXING,
                    tool=tool_id,
                    code="MCP_INVALID_ARGUMENTS",
                )
            try:
                # Validate serializability without using the retired
                # ``paramsJson`` action field.  The current LingXing catalog
                # protocol requires a JSON object in ``params``.
                json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as exc:
                raise MCPGatewayError(
                    "LingXing parameters are not JSON serializable",
                    provider=MCPProvider.LINGXING,
                    tool=tool_id,
                    code="MCP_INVALID_ARGUMENTS",
                ) from exc

            try:
                trace = self.client.call(
                    "action",
                    {
                        "toolId": entry.tool_id,
                        "catalogVersion": entry.catalog_version,
                        "schemaVersion": entry.schema_version,
                        "toolVersionId": entry.tool_version_id,
                        "params": arguments,
                    },
                )
                self._require_success(trace.payload, tool_id=tool_id)
                return trace
            except MCPGatewayError as exc:
                if attempt == 0 and exc.code in _CATALOG_REFRESH_CODES:
                    self._invalidate_catalog_tool(tool_id)
                    continue
                raise

        raise AssertionError("catalog retry loop must return or raise")

    def _resolve_catalog_tool(self, tool_id: str) -> LingxingCatalogEntry:
        with self._catalog_lock:
            cached = self._catalog.get(tool_id)
        if cached is not None:
            return cached

        help_trace = self.client.call("help", {"query": tool_id, "limit": 50, "offset": 0})
        help_payload = self._require_success(help_trace.payload, tool_id=tool_id)
        help_data = help_payload.get("data")
        if not isinstance(help_data, dict):
            raise self._catalog_error(tool_id, "catalog response has no data")
        catalog_version = self._text(help_data.get("catalogVersion"))
        candidates = help_data.get("tools")
        if not isinstance(candidates, list):
            raise self._catalog_error(tool_id, "catalog response has no tool list")
        selected = next(
            (
                item
                for item in candidates if isinstance(item, dict)
                and item.get("toolId") == tool_id
            ),
            None,
        )
        if not catalog_version or not isinstance(selected, dict):
            raise self._catalog_error(tool_id, "required advertising tool is not available")
        if selected.get("toolType") != "read":
            raise self._catalog_error(tool_id, "advertising tool is not read-only")

        search_trace = self.client.call("search", {"toolId": tool_id})
        search_payload = self._require_success(search_trace.payload, tool_id=tool_id)
        search_data = search_payload.get("data")
        if not isinstance(search_data, dict):
            raise self._catalog_error(tool_id, "tool schema response has no data")
        schema_version = self._text(search_data.get("schemaVersion"))
        tool_version_id = self._integer(search_data.get("toolVersionId"))
        schema = search_data.get("inputSchema")
        if (
            search_data.get("toolId") != tool_id
            or search_data.get("toolType") != "read"
            or not schema_version
            or tool_version_id is None
            or not isinstance(schema, dict)
        ):
            raise self._catalog_error(tool_id, "tool schema is invalid or not read-only")
        required = schema.get("required", [])
        if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
            raise self._catalog_error(tool_id, "tool schema required fields are invalid")
        entry = LingxingCatalogEntry(
            tool_id=tool_id,
            catalog_version=catalog_version,
            schema_version=schema_version,
            tool_version_id=tool_version_id,
            required_fields=frozenset(required),
        )
        with self._catalog_lock:
            self._catalog[tool_id] = entry
        return entry

    def _invalidate_catalog_tool(self, tool_id: str) -> None:
        with self._catalog_lock:
            self._catalog.pop(tool_id, None)

    @classmethod
    def _require_success(cls, payload: Any, *, tool_id: str) -> dict[str, Any]:
        decoded = cls._decode_payload(payload)
        if cls._is_success_response(decoded):
            return decoded
        text = json.dumps(decoded, ensure_ascii=False) if isinstance(decoded, (dict, list)) else str(decoded)
        normalized = text.casefold()
        if "unknown tool" in normalized or "not found" in normalized:
            code = "MCP_TOOL_UNAVAILABLE"
        elif decoded.get("code") in {400, 422} or "must not be null" in normalized:
            code = "MCP_INVALID_ARGUMENTS"
        elif "catalogversion" in normalized or "schemaversion" in normalized or "version" in normalized:
            code = "MCP_CATALOG_VERSION_STALE"
        elif "mcp key" in normalized or "invalid key" in normalized or "unauthorized" in normalized:
            code = "MCP_AUTH_FAILED"
        elif "rate limit" in normalized or "too many" in normalized:
            code = "MCP_RATE_LIMITED"
        else:
            code = "MCP_GATEWAY_ERROR"
        raise MCPGatewayError(
            f"LingXing {tool_id} request failed",
            provider=MCPProvider.LINGXING,
            tool=tool_id,
            code=code,
            retryable=code == "MCP_RATE_LIMITED",
        )

    @staticmethod
    def _catalog_error(tool_id: str, message: str) -> MCPGatewayError:
        return MCPGatewayError(
            message,
            provider=MCPProvider.LINGXING,
            tool=tool_id,
            code="MCP_TOOL_UNAVAILABLE",
        )

    @classmethod
    def _extract_rows(cls, payload: Any) -> tuple[list[dict[str, Any]], int]:
        decoded = cls._decode_payload(payload)
        if not isinstance(decoded, dict):
            return [], 0
        if not cls._is_success_response(decoded):
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
    def _is_success_response(payload: Any) -> bool:
        """Accept LingXing's current explicit success convention.

        The catalog API now returns ``code=1`` together with ``success=true``.
        Keep support for the prior code-only responses, but do not accept a
        standalone ``code=1`` because it is not a documented generic success
        code for every endpoint.
        """

        return isinstance(payload, dict) and (
            payload.get("success") is True
            or payload.get("code") in {None, 0, 200}
        )

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

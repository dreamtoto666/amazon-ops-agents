from __future__ import annotations

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from langsmith import traceable
from pydantic import BaseModel, Field


_MCP_TRACE_CONTEXT: ContextVar[dict[str, str]] = ContextVar("mcp_trace_context", default={})
_SENSITIVE_TRACE_FIELDS = frozenset({"authorization", "cookie", "password", "secret", "token", "x-api-key", "x-mcp-key"})


@contextmanager
def mcp_trace_context(*, run_id: str, trace_id: str | None = None):
    """Attach business identifiers to nested MCP LangSmith spans."""
    context = {"run_id": run_id}
    if trace_id:
        context["trace_id"] = trace_id
    token = _MCP_TRACE_CONTEXT.set(context)
    try:
        yield
    finally:
        _MCP_TRACE_CONTEXT.reset(token)


def _safe_trace_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 3:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).casefold() in _SENSITIVE_TRACE_FIELDS else _safe_trace_value(item, depth=depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, list):
        return [_safe_trace_value(item, depth=depth + 1) for item in value[:10]]
    if isinstance(value, str):
        return value[:240] + ("…" if len(value) > 240 else "")
    return value


def _payload_summary(payload: Any) -> dict[str, Any]:
    summary: dict[str, Any] = {"type": type(payload).__name__}
    candidate = payload
    if isinstance(payload, dict):
        summary["top_level_keys"] = sorted(str(key) for key in payload.keys())[:20]
        content = payload.get("content")
        if isinstance(content, list):
            summary["content_block_count"] = len(content)
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        candidate = json.loads(item["text"])
                    except json.JSONDecodeError:
                        pass
                    break
        candidate = payload.get("structuredContent", candidate)
    if isinstance(candidate, dict):
        data = candidate.get("data", candidate)
        if isinstance(data, dict):
            data = data.get("data", data)
        total_source = candidate if isinstance(candidate, dict) else {}
        for key in ("total", "totalCount", "recordsTotal", "count"):
            total = total_source.get(key)
            if isinstance(total, int) and total >= 0:
                summary["reported_total"] = total
                break
        if isinstance(data, list):
            summary["record_count"] = len(data)
            fields = {
                str(key)
                for item in data[:10]
                if isinstance(item, dict)
                for key in item
            }
            if fields:
                summary["field_summary"] = sorted(fields)[:30]
        elif isinstance(data, dict):
            summary["field_summary"] = sorted(str(key) for key in data.keys())[:30]
    elif isinstance(candidate, list):
        summary["record_count"] = len(candidate)
    return summary


def _trace_tool_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": inputs.get("provider"),
        "tool": inputs.get("tool"),
        "arguments": _safe_trace_value(inputs.get("arguments", {})),
        **_safe_trace_value(inputs.get("trace_context", {})),
    }


def _trace_tool_outputs(result: "MCPToolResult | _MCPToolTraceFailure") -> dict[str, Any]:
    if isinstance(result, _MCPToolTraceFailure):
        return {
            "call_id": result.call_id,
            "provider": result.provider.value,
            "tool": result.tool,
            "duration_ms": result.duration_ms,
            "success": False,
            "error_code": result.error_code,
            "payload_summary": None,
        }
    return {
        "call_id": result.call_id,
        "provider": result.provider.value,
        "tool": result.tool,
        "duration_ms": result.duration_ms,
        "success": not result.is_error,
        "error_code": result.error_code,
        "payload_summary": _payload_summary(result.payload),
    }


class MCPProvider(str, Enum):
    SELLER_SPRITE = "seller_sprite"
    SIF = "sif"
    LINGXING = "lingxing"


SELLER_SPRITE_KEYWORD_TOOLS = frozenset(
    {"asin_detail", "traffic_source", "traffic_keyword", "keyword_order", "keyword_miner"}
)
SIF_KEYWORD_TOOLS = frozenset(
    {
        "market_get_asin_keyword_signals",
        "ops_get_listing_keyword_distribution",
        "market_get_asin_profile",
        "market_get_asin_aba_footprint",
        "market_screen_keyword_opportunities",
        "market_discover_competitors",
        "market_get_keyword_root_competitors",
    }
)
LINGXING_READ_ONLY_TOOLS = frozenset(
    {
        "ad_auth_shops",
        "ad_campaign_group_report",
        "ad_campaign_keyword_report",
        "ad_campaign_product_report",
        "ad_campaign_report",
        "ad_campaign_search_term_report",
        "ad_campaign_targeting_report",
        "ad_portfolio_report_shop",
        "erp_listing",
        "get_custom_indicator_field",
        "get_custom_indicator_list",
        "get_custom_report_by_id",
        "get_custom_report_list",
        "get_fba_stock_list",
        "get_multi_platform_shop_list",
        "get_my_sids",
        "get_profit_report_msku",
        "query_erp_competitive_monitor",
        "query_erp_follow_sale_monitor",
        "query_erp_keyword_ranking_asin",
        "query_erp_keyword_ranking_keyword",
        "query_erp_new_monitor",
        "query_fba_valid_list",
        "query_order_profit_list",
        "query_order_profit_list_gross_profit",
        "query_product_performance_asin_lists",
    }
)


class MCPServerConfig(BaseModel):
    provider: MCPProvider
    endpoint: str = Field(pattern=r"^https://")
    api_key_env: str = Field(min_length=1)
    auth_header: str | None = None
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    max_concurrency: int = Field(default=2, ge=1, le=10)
    allowed_tools: frozenset[str] = Field(min_length=1)


def seller_sprite_mcp_config() -> MCPServerConfig:
    return MCPServerConfig(
        provider=MCPProvider.SELLER_SPRITE,
        endpoint="https://mcp.sellersprite.com/mcp",
        api_key_env="SELLER_SPRITE_MCP_SECRET",
        auth_header="secret-key",
        max_concurrency=2,
        allowed_tools=SELLER_SPRITE_KEYWORD_TOOLS,
    )


def sif_mcp_config(*, auth_header: str = "secret-key") -> MCPServerConfig:
    return MCPServerConfig(
        provider=MCPProvider.SIF,
        endpoint="https://mcp.sif.com/mcp",
        api_key_env="SIF_MCP_SECRET",
        auth_header=auth_header,
        max_concurrency=2,
        allowed_tools=SIF_KEYWORD_TOOLS,
    )


def lingxing_mcp_config() -> MCPServerConfig:
    """Build a LingXing MCP connection restricted to read-only tools."""

    return MCPServerConfig(
        provider=MCPProvider.LINGXING,
        endpoint=os.getenv(
            "LINGXING_MCP_URL",
            "https://openmcp.lingxing.com/mcp-servers/lingxing-mcp",
        ),
        api_key_env="LINGXING_MCP_SECRET",
        auth_header="X-Mcp-Key",
        max_concurrency=1,
        allowed_tools=LINGXING_READ_ONLY_TOOLS,
    )


class MCPToolDefinition(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict)


class MCPToolResult(BaseModel):
    call_id: str
    provider: MCPProvider
    tool: str
    arguments: dict[str, Any]
    payload: Any = None
    is_error: bool = False
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    started_at: datetime
    finished_at: datetime
    duration_ms: int = Field(ge=0)


@dataclass(frozen=True)
class _MCPToolTraceFailure:
    call_id: str
    provider: MCPProvider
    tool: str
    duration_ms: int
    error_code: str
    retryable: bool


class MCPTransport(Protocol):
    """Replaceable transport boundary implemented by the official MCP SDK adapter.

    The implementation resolves ``config.api_key_env`` at call time. Secret
    values must never be returned, logged, or placed in LangGraph state.
    """

    def list_tools(self, config: MCPServerConfig) -> list[MCPToolDefinition]: ...

    def call_tool(
        self,
        config: MCPServerConfig,
        tool: str,
        arguments: dict[str, Any],
    ) -> Any: ...


class MCPGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: MCPProvider,
        tool: str | None = None,
        code: str = "MCP_GATEWAY_ERROR",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.tool = tool
        self.code = code
        self.retryable = retryable


class MCPProviderClient:
    """Safe provider client with tool allowlisting and normalized call traces."""

    def __init__(self, *, config: MCPServerConfig, transport: MCPTransport) -> None:
        self.config = config
        self.transport = transport

    def discover_tools(self) -> list[MCPToolDefinition]:
        discovered = self.transport.list_tools(self.config)
        return [item for item in discovered if item.name in self.config.allowed_tools]

    def call(self, tool: str, arguments: dict[str, Any]) -> MCPToolResult:
        if tool not in self.config.allowed_tools:
            raise MCPGatewayError(
                f"tool {tool} is not allowed for {self.config.provider.value}",
                provider=self.config.provider,
                tool=tool,
                code="MCP_TOOL_NOT_ALLOWED",
            )

        call_id = f"mcp-{uuid4().hex[:16]}"
        @traceable(
            name="mcp_tool_call",
            run_type="tool",
            process_inputs=_trace_tool_inputs,
            process_outputs=_trace_tool_outputs,
        )
        def invoke(
            provider: str,
            tool: str,
            arguments: dict[str, Any],
            trace_context: dict[str, str],
        ) -> MCPToolResult | _MCPToolTraceFailure:
            started_at = datetime.now(timezone.utc)
            started = monotonic()
            try:
                payload = self.transport.call_tool(self.config, tool, dict(arguments))
            except MCPGatewayError as exc:
                return _MCPToolTraceFailure(
                    call_id=call_id,
                    provider=self.config.provider,
                    tool=tool,
                    duration_ms=max(0, round((monotonic() - started) * 1000)),
                    error_code=exc.code,
                    retryable=exc.retryable,
                )
            except (TimeoutError, ConnectionError) as exc:
                return _MCPToolTraceFailure(
                    call_id=call_id,
                    provider=self.config.provider,
                    tool=tool,
                    duration_ms=max(0, round((monotonic() - started) * 1000)),
                    error_code="MCP_TEMPORARY_FAILURE",
                    retryable=True,
                )
            except Exception:
                return _MCPToolTraceFailure(
                    call_id=call_id,
                    provider=self.config.provider,
                    tool=tool,
                    duration_ms=max(0, round((monotonic() - started) * 1000)),
                    error_code="MCP_TOOL_CALL_FAILED",
                    retryable=False,
                )
            return MCPToolResult(
                call_id=call_id,
                provider=self.config.provider,
                tool=tool,
                arguments=dict(arguments),
                payload=payload,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                duration_ms=max(0, round((monotonic() - started) * 1000)),
            )

        result = invoke(self.config.provider.value, tool, dict(arguments), _MCP_TRACE_CONTEXT.get())
        if isinstance(result, _MCPToolTraceFailure):
            raise MCPGatewayError(
                "MCP tool call failed",
                provider=result.provider,
                tool=result.tool,
                code=result.error_code,
                retryable=result.retryable,
            )
        return result


class SellerSpriteMCP:
    """Typed tool port for SellerSprite keyword research."""

    def __init__(self, client: MCPProviderClient) -> None:
        if client.config.provider != MCPProvider.SELLER_SPRITE:
            raise ValueError("SellerSpriteMCP requires a seller_sprite client")
        self.client = client

    def keyword_miner(
        self,
        *,
        marketplace: str,
        keyword: str,
        size: int = 50,
        return_fields: list[str] | None = None,
    ) -> MCPToolResult:
        arguments: dict[str, Any] = {
            "marketplace": marketplace,
            "keyword": keyword,
            "page": 1,
            "size": size,
        }
        if return_fields:
            arguments["returnFields"] = ",".join(return_fields)
        return self.client.call("keyword_miner", {"request": arguments})

    def traffic_keyword(
        self,
        *,
        marketplace: str,
        asin: str,
        size: int = 50,
    ) -> MCPToolResult:
        # Do not set trafficKeywordTypes/conversionKeywordTypes: provider
        # guidance recommends retrieving the complete keyword set.
        return self.client.call(
            "traffic_keyword",
            {
                "request": {
                    "marketplace": marketplace,
                    "asin": asin,
                    "page": 1,
                    "size": size,
                }
            },
        )

    def keyword_order(
        self,
        *,
        marketplace: str,
        asins: list[str],
        date: str,
        reverse_type: str = "W",
    ) -> MCPToolResult:
        return self.client.call(
            "keyword_order",
            {"request": {
                "marketplace": marketplace,
                "asins": asins,
                "date": date,
                "reverseType": reverse_type,
            }},
        )

    def traffic_source(self, *, marketplace: str, query: str) -> MCPToolResult:
        return self.client.call(
            "traffic_source",
            {"request": {"marketplace": marketplace, "q": query}},
        )

    def asin_detail(
        self,
        *,
        marketplace: str,
        asin: str,
        return_fields: list[str] | None = None,
    ) -> MCPToolResult:
        arguments: dict[str, Any] = {"marketplace": marketplace, "asin": asin}
        if return_fields:
            arguments["returnFields"] = ",".join(return_fields)
        return self.client.call("asin_detail", arguments)


class SifMCP:
    """Typed tool port for Sif market and Listing keyword signals."""

    def __init__(self, client: MCPProviderClient) -> None:
        if client.config.provider != MCPProvider.SIF:
            raise ValueError("SifMCP requires a sif client")
        self.client = client

    def call_keyword_tool(self, tool: str, arguments: dict[str, Any]) -> MCPToolResult:
        """Call an allowlisted Sif tool using its discovered input schema.

        Sif tool names are stable, while argument schemas can evolve. The
        integration must call ``discover_tools`` and validate arguments against
        the returned schema before using this method in production.
        """

        return self.client.call(tool, arguments)

    def asin_keyword_signals(self, arguments: dict[str, Any]) -> MCPToolResult:
        return self.call_keyword_tool("market_get_asin_keyword_signals", arguments)

    def listing_keyword_distribution(self, arguments: dict[str, Any]) -> MCPToolResult:
        return self.call_keyword_tool("ops_get_listing_keyword_distribution", arguments)

    def screen_keyword_opportunities(self, arguments: dict[str, Any]) -> MCPToolResult:
        return self.call_keyword_tool("market_screen_keyword_opportunities", arguments)

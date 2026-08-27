from __future__ import annotations

import pytest

from amazon_ops.listing import (
    MCPGatewayError,
    MCPProviderClient,
    MCPToolDefinition,
    SellerSpriteMCP,
    SifMCP,
    lingxing_mcp_config,
    seller_sprite_mcp_config,
    sif_mcp_config,
)
from amazon_ops.listing.mcp import _payload_summary, _trace_tool_inputs


class FakeTransport:
    def __init__(self):
        self.calls = []

    def list_tools(self, config):
        return [
            MCPToolDefinition(name=name, input_schema={"type": "object"})
            for name in [*config.allowed_tools, "unapproved_tool"]
        ]

    def call_tool(self, config, tool, arguments):
        self.calls.append((config.provider.value, tool, arguments))
        return {"data": {"items": [{"keyword": "phone stand", "searches": 1000}]}}


def test_seller_sprite_config_keeps_secret_value_out_of_state():
    config = seller_sprite_mcp_config()
    payload = config.model_dump(mode="json")

    assert payload["endpoint"] == "https://mcp.sellersprite.com/mcp"
    assert payload["auth_header"] == "secret-key"
    assert payload["api_key_env"] == "SELLER_SPRITE_MCP_SECRET"
    assert "secret" not in payload


def test_lingxing_config_uses_streamable_http_header_without_exposing_key():
    config = lingxing_mcp_config()
    payload = config.model_dump(mode="json")

    assert payload["endpoint"] == "https://openmcp.lingxing.com/mcp-servers/lingxing-mcp"
    assert payload["auth_header"] == "X-Mcp-Key"
    assert payload["api_key_env"] == "LINGXING_MCP_SECRET"
    assert payload["max_concurrency"] == 1
    assert "get_fba_stock_list" in payload["allowed_tools"]
    assert "put_campaigns" not in payload["allowed_tools"]


def test_discovery_only_exposes_allowlisted_tools():
    transport = FakeTransport()
    client = MCPProviderClient(config=seller_sprite_mcp_config(), transport=transport)

    tools = client.discover_tools()

    assert tools
    assert "unapproved_tool" not in {tool.name for tool in tools}


def test_seller_sprite_typed_port_builds_keyword_miner_call():
    transport = FakeTransport()
    port = SellerSpriteMCP(
        MCPProviderClient(config=seller_sprite_mcp_config(), transport=transport)
    )

    result = port.keyword_miner(
        marketplace="US",
        keyword="phone stand",
        return_fields=["keyword", "searches", "purchases", "relevancy"],
    )

    assert result.provider.value == "seller_sprite"
    assert result.tool == "keyword_miner"
    request = transport.calls[0][2]["request"]
    assert request["returnFields"] == "keyword,searches,purchases,relevancy"
    assert request["marketplace"] == "US"


def test_sif_port_uses_discovered_schema_arguments_without_guessing_them():
    transport = FakeTransport()
    port = SifMCP(MCPProviderClient(config=sif_mcp_config(), transport=transport))
    arguments = {"marketplace": "US", "asin": "B012345678"}

    result = port.asin_keyword_signals(arguments)

    assert result.provider.value == "sif"
    assert result.tool == "market_get_asin_keyword_signals"
    assert result.arguments == arguments


def test_unapproved_mcp_tool_is_blocked_before_transport_call():
    transport = FakeTransport()
    client = MCPProviderClient(config=seller_sprite_mcp_config(), transport=transport)

    with pytest.raises(MCPGatewayError) as exc_info:
        client.call("delete_listing", {})

    assert exc_info.value.code == "MCP_TOOL_NOT_ALLOWED"
    assert transport.calls == []


def test_temporary_transport_failure_is_marked_retryable():
    class TimeoutTransport(FakeTransport):
        def call_tool(self, config, tool, arguments):
            raise TimeoutError("provider timeout")

    client = MCPProviderClient(
        config=seller_sprite_mcp_config(),
        transport=TimeoutTransport(),
    )

    with pytest.raises(MCPGatewayError) as exc_info:
        client.call("keyword_miner", {"marketplace": "US", "keyword": "phone stand"})

    assert exc_info.value.retryable is True
    assert exc_info.value.code == "MCP_TEMPORARY_FAILURE"


def test_tool_trace_input_redacts_sensitive_arguments_and_keeps_business_ids():
    traced = _trace_tool_inputs(
        {
            "provider": "lingxing",
            "tool": "ad_campaign_report",
            "arguments": {"token": "do-not-send", "request": {"profile_id": "123"}},
            "trace_context": {"run_id": "ad-run-1", "trace_id": "trace-1"},
        }
    )

    assert traced["arguments"]["token"] == "[REDACTED]"
    assert traced["arguments"]["request"]["profile_id"] == "123"
    assert traced["run_id"] == "ad-run-1"
    assert traced["trace_id"] == "trace-1"


def test_tool_trace_payload_summary_only_reports_counts_and_fields():
    summary = _payload_summary(
        {
            "structuredContent": {
                "total": 30,
                "data": [{"campaignId": "1", "cost": 10}, {"campaignId": "2", "sales": 20}],
            }
        }
    )

    assert summary["record_count"] == 2
    assert summary["reported_total"] == 30
    assert summary["field_summary"] == ["campaignId", "cost", "sales"]

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
from amazon_ops.listing.mcp import MCPProvider
from amazon_ops.listing.mcp_transport import _classify_transport_failure, _tool_payload


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
    assert set(payload["allowed_tools"]) == {"help", "search", "action"}
    assert "ad_campaign_report" not in payload["allowed_tools"]


def test_lingxing_config_uses_default_endpoint_when_override_is_empty(monkeypatch):
    monkeypatch.setenv("LINGXING_MCP_URL", "")

    assert lingxing_mcp_config().endpoint == "https://openmcp.lingxing.com/mcp-servers/lingxing-mcp"


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


def test_nested_unknown_tool_error_is_not_misclassified_as_connection_failure():
    error = ExceptionGroup("stream closed", [RuntimeError("unknown tool: ad_auth_shops")])

    assert _classify_transport_failure(error) == ("MCP_TOOL_UNAVAILABLE", False)


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


# --- MCP 工具级失败（isError）必须记为失败 -----------------------------------
#
# 回归背景：MCP 把「工具执行失败」表达为一次*成功*调用 + ``isError: true``。
# transport 曾把整个信封原样当数据返回，于是上游失败被记成成功、错误文本被当作
# 证据留存（实测 Sif 配额超限时 ok=True、evidence 6 条、无 errors）。


class FakeContentBlock:
    def __init__(self, text):
        self.text = text


class FakeToolResult:
    def __init__(self, *, is_error=False, blocks=(), structured=None):
        self.is_error = is_error
        self.content = [FakeContentBlock(text) for text in blocks]
        self.structured_content = structured

    def model_dump(self, **_kwargs):
        return {
            "content": [{"type": "text", "text": block.text} for block in self.content],
            "isError": self.is_error,
            "structuredContent": self.structured_content,
        }


def _sif_config():
    return sif_mcp_config()


def test_a_tool_level_error_result_becomes_a_failed_call():
    result = FakeToolResult(
        is_error=True,
        blocks=['{"error":"QUOTA_EXCEEDED","message":"未分配MCP用量","secretId":"sifmcp260803xefnk9nhvvckat79"}'],
    )

    with pytest.raises(MCPGatewayError) as exc_info:
        _tool_payload(result, config=_sif_config(), tool="ops_get_asin_traffic_trend")

    assert exc_info.value.code == "QUOTA_EXCEEDED"
    assert exc_info.value.provider is MCPProvider.SIF


def test_a_provider_error_body_never_leaks_into_the_error():
    """上游报文可能含密钥标识，绝不能进入 code 或 message。"""

    secret_id = "sifmcp260803xefnk9nhvvckat79"
    result = FakeToolResult(
        is_error=True,
        blocks=[f'{{"error":"QUOTA_EXCEEDED","message":"key unmasked","secretId":"{secret_id}"}}'],
    )

    with pytest.raises(MCPGatewayError) as exc_info:
        _tool_payload(result, config=_sif_config(), tool="ops_get_asin_traffic_trend")

    assert secret_id not in str(exc_info.value)
    assert secret_id not in exc_info.value.code
    assert secret_id.upper() not in str(exc_info.value)


def test_an_unrecognised_error_body_falls_back_to_a_generic_code():
    for blocks in ([], ["not json at all"], ['{"detail":"boom"}'], ['{"error":"quota exceeded"}']):
        result = FakeToolResult(is_error=True, blocks=blocks)

        with pytest.raises(MCPGatewayError) as exc_info:
            _tool_payload(result, config=_sif_config(), tool="ops_get_asin_traffic_trend")

        assert exc_info.value.code == "MCP_TOOL_ERROR"


def test_a_successful_result_is_returned_as_the_serialized_payload():
    result = FakeToolResult(is_error=False, blocks=['{"data":1}'], structured={"data": 1})

    payload = _tool_payload(result, config=_sif_config(), tool="ops_get_asin_traffic_trend")

    assert payload["isError"] is False
    assert payload["structuredContent"] == {"data": 1}


def test_an_error_result_never_reaches_the_caller_as_evidence():
    """端到端不变量：失败的上游调用不得留下任何看起来像证据的东西。"""

    class ErroringTransport(FakeTransport):
        def call_tool(self, config, tool, arguments):
            # 走真实 transport 的判定函数，而不是绕过它
            raw = FakeToolResult(is_error=True, blocks=['{"error":"QUOTA_EXCEEDED"}'])
            return _tool_payload(raw, config=config, tool=tool)

    client = MCPProviderClient(config=sif_mcp_config(), transport=ErroringTransport())

    with pytest.raises(MCPGatewayError) as exc_info:
        client.call("ops_get_asin_traffic_trend", {"asin": "B0OWN00001", "country": "US"})

    assert exc_info.value.code == "QUOTA_EXCEEDED"


def test_a_gateway_error_raised_inside_the_operation_keeps_its_code():
    """MCP SDK 把操作包在 asyncio.TaskGroup 里，错误到达时会套一层 ExceptionGroup。

    回归背景：不拆包的话，工具级失败的 code 会被替换成通用的
    MCP_TRANSPORT_FAILURE（实测 QUOTA_EXCEEDED 就是这样丢的）。
    """

    from amazon_ops.listing.mcp_transport import _gateway_error

    inner = MCPGatewayError(
        "tool x returned an error result", provider=MCPProvider.SIF, tool="x", code="QUOTA_EXCEEDED"
    )
    wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [inner])
    doubly_wrapped = ExceptionGroup("unhandled errors in a TaskGroup", [wrapped])

    assert _gateway_error(doubly_wrapped) is inner
    assert _gateway_error(RuntimeError("unrelated")) is None
    assert _gateway_error(inner) is inner

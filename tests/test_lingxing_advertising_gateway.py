from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from amazon_ops.advertising.gateway import LingxingAdvertisingGateway
from amazon_ops.advertising.selection_gateway import SelectionDirectoryGateway
from amazon_ops.advertising.models import DiagnosticPeriod
from amazon_ops.advertising.runtime import AdvertisingRunManager
from amazon_ops.listing.mcp import MCPGatewayError, MCPProvider, MCPToolResult


def _trace(tool: str, arguments: dict) -> MCPToolResult:
    now = datetime.now(timezone.utc)
    return MCPToolResult(
        call_id=f"call-{tool}",
        provider=MCPProvider.LINGXING,
        tool=tool,
        arguments=arguments,
        payload={},
        started_at=now,
        finished_at=now,
        duration_ms=1,
    )


class CatalogClient:
    catalog_version = "catalog-v1"

    def __init__(
        self,
        *,
        non_read: bool = False,
        unavailable_once: bool = False,
        response_code: int = 0,
        response_success: bool | None = None,
    ) -> None:
        self.non_read = non_read
        self.unavailable_once = unavailable_once
        self.response_code = response_code
        self.response_success = response_success
        self.calls: list[tuple[str, dict]] = []
        self.action_attempts: dict[str, int] = {}

    def call(self, tool: str, arguments: dict) -> MCPToolResult:
        self.calls.append((tool, arguments))
        result = _trace(tool, arguments)
        if tool == "help":
            tool_id = arguments["query"]
            response = {
                "code": self.response_code,
                "data": {
                    "catalogVersion": self.catalog_version,
                    "tools": [{"toolId": tool_id, "toolType": "write" if self.non_read else "read"}],
                },
            }
            if self.response_success is not None:
                response["success"] = self.response_success
            result.payload = {"content": [{"type": "text", "text": json.dumps(response)}]}
            return result
        if tool == "search":
            tool_id = arguments["toolId"]
            required = (
                []
                if tool_id == "ad_auth_shops"
                else []
                if tool_id == "advertising_analyze_search_term"
                else ["report_date", "profile_ids"]
            )
            response = {
                "code": self.response_code,
                "data": {
                    "toolId": tool_id,
                    "toolType": "write" if self.non_read else "read",
                    "schemaVersion": f"{tool_id}-v1",
                    "toolVersionId": 1,
                    "inputSchema": {"type": "object", "required": required},
                },
            }
            if self.response_success is not None:
                response["success"] = self.response_success
            result.payload = {"content": [{"type": "text", "text": json.dumps(response)}]}
            return result
        assert tool == "action"
        tool_id = arguments["toolId"]
        self.action_attempts[tool_id] = self.action_attempts.get(tool_id, 0) + 1
        if self.unavailable_once and self.action_attempts[tool_id] == 1:
            raise MCPGatewayError(
                "unknown tool",
                provider=MCPProvider.LINGXING,
                tool="action",
                code="MCP_TOOL_UNAVAILABLE",
            )
        rows = (
            [{"profile_id": "1001", "alias": "US 店铺", "sid": 1, "store_id": 2, "country": "US"}]
            if tool_id == "ad_auth_shops"
            else [{"campaign_id": "campaign-1", "profile_id": "profile-1", "spends": 10}]
        )
        response = {"code": self.response_code, "data": {"data": rows}}
        if self.response_success is not None:
            response["success"] = self.response_success
        result.payload = {"content": [{"type": "text", "text": json.dumps(response)}]}
        return result


def _period() -> DiagnosticPeriod:
    return DiagnosticPeriod(start=date(2026, 8, 1), end=date(2026, 8, 7))


def test_gateway_uses_catalog_protocol_for_shops_and_all_advertising_reports():
    client = CatalogClient()
    gateway = LingxingAdvertisingGateway(client=client)

    shops = gateway.list_shops()
    campaign = gateway.campaign_report(profile_ids=["profile-1"], period=_period())
    for tool in (
        "ad_campaign_group_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_search_term_report",
    ):
        gateway.attribution_report(
            tool=tool,
            profile_ids=["profile-1"],
            period=_period(),
            campaign_ids=["campaign-1"],
        )

    assert shops[0].alias == "US 店铺"
    assert campaign.rows[0]["campaign_id"] == "campaign-1"
    action_calls = [arguments for tool, arguments in client.calls if tool == "action"]
    assert len(action_calls) == 6
    assert {item["toolId"] for item in action_calls} == {
        "ad_auth_shops",
        "ad_campaign_report",
        "ad_campaign_group_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_search_term_report",
    }
    assert all(item["catalogVersion"] == "catalog-v1" for item in action_calls)
    assert all(item["schemaVersion"].endswith("-v1") for item in action_calls)
    assert all(isinstance(item["params"], dict) for item in action_calls)
    assert all(item["toolVersionId"] == 1 for item in action_calls)


def test_gateway_accepts_lingxing_code_one_with_explicit_success():
    client = CatalogClient(response_code=1, response_success=True)
    gateway = LingxingAdvertisingGateway(client=client)

    shops = gateway.list_shops()
    campaign = gateway.campaign_report(profile_ids=["profile-1"], period=_period())

    assert shops[0].alias == "US 店铺"
    assert campaign.rows[0]["campaign_id"] == "campaign-1"


def test_gateway_resolves_all_sb_product_asins_from_campaign_reports():
    client = CatalogClient()
    gateway = LingxingAdvertisingGateway(client=client)

    campaign_ids = gateway.resolve_sb_campaign_ids(
        sid="1",
        asins=[f"B0TEST{index:04d}" for index in range(12)],
        period=_period(),
    )

    action_calls = [
        arguments["params"]
        for tool, arguments in client.calls
        if tool == "action" and arguments["toolId"] == "ad_campaign_report"
    ]
    assert campaign_ids == {"campaign-1"}
    assert len(action_calls) == 12
    assert all(call["ads_type"] == [["sb"]] for call in action_calls)
    assert all(call["profile_ids"] == [1001] for call in action_calls)
    assert all(isinstance(call["asin"], str) for call in action_calls)


def test_gateway_rejects_non_read_catalog_tools_before_action():
    client = CatalogClient(non_read=True)
    gateway = LingxingAdvertisingGateway(client=client)

    with pytest.raises(MCPGatewayError) as exc_info:
        gateway.list_shops()

    assert exc_info.value.code == "MCP_TOOL_UNAVAILABLE"
    assert "action" not in [tool for tool, _ in client.calls]


def test_gateway_validates_catalog_required_fields_before_action():
    client = CatalogClient()
    gateway = LingxingAdvertisingGateway(client=client)

    with pytest.raises(MCPGatewayError) as exc_info:
        gateway._call_catalog_tool("ad_campaign_report", {})

    assert exc_info.value.code == "MCP_INVALID_ARGUMENTS"
    assert "action" not in [tool for tool, _ in client.calls]


def test_gateway_refreshes_a_stale_catalog_entry_once():
    client = CatalogClient(unavailable_once=True)
    gateway = LingxingAdvertisingGateway(client=client)

    shops = gateway.list_shops()

    assert shops
    assert client.action_attempts["ad_auth_shops"] == 2
    assert [tool for tool, _ in client.calls].count("help") == 2
    assert [tool for tool, _ in client.calls].count("search") == 2


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("MCP_AUTH_FAILED", "密钥无效或已失效"),
        ("MCP_TOOL_UNAVAILABLE", "未提供所需的广告数据接口"),
        ("MCP_RATE_LIMITED", "请求过于频繁"),
        ("MCP_TRANSPORT_FAILURE", "暂时无法连接"),
    ],
)
def test_runtime_exposes_specific_lingxing_failure_messages(code: str, expected: str):
    error = MCPGatewayError("failed", provider=MCPProvider.LINGXING, code=code)

    assert expected in AdvertisingRunManager._safe_error_message(error)


def test_runtime_hides_python_shutdown_details_from_advertising_users():
    message = AdvertisingRunManager._safe_error_message(
        RuntimeError("cannot schedule new futures after interpreter shutdown")
    )

    assert message == "广告巡检服务正在重启，请稍后重新发起查询。"


class SelectionTransport:
    def get(self, path):
        assert path == "/erp/sc/data/seller/lists"
        return {"code": 0, "data": [{"sid": 7, "name": "安全店铺", "untrusted": "drop"}]}

    def post(self, path, *, payload):
        assert path == "/erp/sc/data/mws/listing"
        return {"code": 0, "data": [{"sid": 7, "parent_asin": "B0PARENT", "principal_info": [{"principal_uid": "staff-1", "principal_name": "负责人 A", "phone": "drop"}]}]}


def test_selection_directory_is_whitelisted_and_resolves_only_its_owner(monkeypatch):
    monkeypatch.setenv("LINGXING_OPEN_API_APP_SECRET", "0123456789abcdef")
    gateway = SelectionDirectoryGateway(transport=SelectionTransport())
    directory = gateway.directory(owner_id="user-a")
    encoded = json.dumps(directory, ensure_ascii=False)
    assert "staff-1" not in encoded and "phone" not in encoded and "sid" not in encoded
    store = directory["stores"][0]
    product = store["responsibles"][0]["products"][0]
    scope = gateway.resolve(owner_id="user-a", version=directory["version"], shop_ref=store["shop_ref"], product_refs=[product["product_ref"]])
    assert scope.sid == "7" and scope.parent_asins == ["B0PARENT"]
    with pytest.raises(PermissionError):
        gateway.resolve(owner_id="user-b", version=directory["version"], shop_ref=store["shop_ref"], product_refs=[product["product_ref"]])

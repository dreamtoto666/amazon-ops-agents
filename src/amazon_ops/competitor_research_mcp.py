"""Internal stdio MCP server for Sif competitor-research business tools."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer

from .competitor_research import CompetitorResearchService
from .competitor_research_catalog import CAPABILITY_BY_TOOL, competitor_research_catalog
from .listing.mcp import MCPProviderClient, sif_mcp_config
from .listing.mcp_transport import StreamableHTTPMCPTransport
from .models import DateRange
from .prompts import COMPETITOR_SPECIALIST_SYSTEM_PROMPT


def _failure(code: str) -> dict[str, Any]:
    return {"ok": False, "summary": {}, "evidence": [], "limitations": [], "errors": [{"code": code}]}


def _guarded(call: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return call()
    except ValueError as exc:
        return _failure(str(exc))


def _comparison(
    service_method: Any,
    own_asin: str,
    competitor_asins: list[str],
    country: str,
) -> dict[str, Any]:
    return _guarded(
        lambda: service_method(own_asin=own_asin, competitor_asins=competitor_asins, country=country)
    )


def _period(period_start: str | None, period_end: str | None) -> DateRange | None:
    """Build the requested window; a half-specified one is a caller error."""

    if period_start is None and period_end is None:
        return None
    if not period_start or not period_end:
        raise ValueError("PERIOD_INCOMPLETE")
    return DateRange(start=period_start, end=period_end)


def create_competitor_research_mcp_server(*, service: CompetitorResearchService) -> MCPServer:
    server = MCPServer(
        name="amazon-ops-competitor-research",
        title="Amazon Ops Sif Competitor Research",
        description="Internal read-only business tools for Sif competitor comparison.",
        instructions=COMPETITOR_SPECIALIST_SYSTEM_PROMPT,
        version="0.1.0",
    )

    @server.tool(name="get_competitor_research_catalog", title="竞品研究能力目录", description="返回所有竞品研究小 MCP 的可信说明、输入要求、依赖和下钻条件。", structured_output=True)
    def get_competitor_research_catalog() -> dict[str, Any]:
        return {"ok": True, "capabilities": competitor_research_catalog()}

    @server.tool(name="compare_asin_sales", title=CAPABILITY_BY_TOOL["compare_asin_sales"].title, description=CAPABILITY_BY_TOOL["compare_asin_sales"].description, structured_output=True)
    def compare_asin_sales(own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        return _comparison(service.compare_asin_sales, own_asin, competitor_asins, country)

    @server.tool(name="analyze_traffic_structure", title=CAPABILITY_BY_TOOL["analyze_traffic_structure"].title, description=CAPABILITY_BY_TOOL["analyze_traffic_structure"].description, structured_output=True)
    def analyze_traffic_structure(own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        return _comparison(service.analyze_traffic_structure, own_asin, competitor_asins, country)

    @server.tool(name="compare_traffic_keywords", title=CAPABILITY_BY_TOOL["compare_traffic_keywords"].title, description=CAPABILITY_BY_TOOL["compare_traffic_keywords"].description, structured_output=True)
    def compare_traffic_keywords(
        own_asin: str, competitor_asins: list[str], country: str,
        period_start: str | None = None, period_end: str | None = None,
    ) -> dict[str, Any]:
        return _guarded(
            lambda: service.compare_traffic_keywords(
                own_asin=own_asin, competitor_asins=competitor_asins, country=country,
                period=_period(period_start, period_end),
            )
        )

    @server.tool(name="replay_operations_history", title=CAPABILITY_BY_TOOL["replay_operations_history"].title, description=CAPABILITY_BY_TOOL["replay_operations_history"].description, structured_output=True)
    def replay_operations_history(own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        return _comparison(service.replay_operations_history, own_asin, competitor_asins, country)

    @server.tool(name="inspect_ad_architecture", title=CAPABILITY_BY_TOOL["inspect_ad_architecture"].title, description=CAPABILITY_BY_TOOL["inspect_ad_architecture"].description, structured_output=True)
    def inspect_ad_architecture(own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        return _comparison(service.inspect_ad_architecture, own_asin, competitor_asins, country)

    @server.tool(name="analyze_recommendation_traffic", title=CAPABILITY_BY_TOOL["analyze_recommendation_traffic"].title, description=CAPABILITY_BY_TOOL["analyze_recommendation_traffic"].description, structured_output=True)
    def analyze_recommendation_traffic(own_asin: str, competitor_asins: list[str], country: str) -> dict[str, Any]:
        return _comparison(service.analyze_recommendation_traffic, own_asin, competitor_asins, country)

    @server.tool(name="inspect_campaign", title=CAPABILITY_BY_TOOL["inspect_campaign"].title, description=CAPABILITY_BY_TOOL["inspect_campaign"].description, structured_output=True)
    def inspect_campaign(
        asin: str, campaign_id: str, country: str,
        period_start: str | None = None, period_end: str | None = None,
    ) -> dict[str, Any]:
        return _guarded(
            lambda: service.inspect_campaign(
                asin=asin, campaign_id=campaign_id, country=country,
                period=_period(period_start, period_end),
            )
        )

    @server.tool(name="inspect_ad_group", title=CAPABILITY_BY_TOOL["inspect_ad_group"].title, description=CAPABILITY_BY_TOOL["inspect_ad_group"].description, structured_output=True)
    def inspect_ad_group(
        asin: str, campaign_id: str, ad_group_id: str, country: str,
        period_start: str | None = None, period_end: str | None = None,
    ) -> dict[str, Any]:
        return _guarded(
            lambda: service.inspect_ad_group(
                asin=asin, campaign_id=campaign_id, ad_group_id=ad_group_id, country=country,
                period=_period(period_start, period_end),
            )
        )

    return server


def main() -> None:
    transport = StreamableHTTPMCPTransport()
    service = CompetitorResearchService(MCPProviderClient(config=sif_mcp_config(), transport=transport))
    create_competitor_research_mcp_server(service=service).run("stdio")


if __name__ == "__main__":
    main()

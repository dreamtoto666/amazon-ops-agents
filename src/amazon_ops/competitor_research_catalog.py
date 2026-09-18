"""Trusted capability catalogue for the internal competitor-research MCP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


# How much of the requested observation window each capability can actually carry
# to Sif.  Most Sif tools take no date argument at all, so their observation
# window is decided by the provider and cannot be aligned with the request.
ObservationWindow = Literal["requested_range", "requested_end_day", "provider_default"]


@dataclass(frozen=True)
class CompetitorResearchCapability:
    tool: str
    title: str
    description: str
    required_inputs: tuple[str, ...]
    returns: tuple[str, ...]
    parallelizable: bool
    observation_window: ObservationWindow
    dependencies: tuple[str, ...] = ()
    drilldown_condition: str | None = None


COMPARISON_INPUTS = ("own_asin", "competitor_asins (1-5)", "country")
DETAIL_INPUTS = ("asin", "campaign_id", "country")
AD_GROUP_INPUTS = ("asin", "campaign_id", "ad_group_id", "country")


COMPETITOR_RESEARCH_CAPABILITIES: tuple[CompetitorResearchCapability, ...] = (
    CompetitorResearchCapability(
        tool="compare_asin_sales", title="销量对比", description="比较自有 ASIN 与竞品的销量明细和销量趋势。",
        required_inputs=COMPARISON_INPUTS, returns=("销量明细", "销量趋势"), parallelizable=True,
        observation_window="provider_default",
    ),
    CompetitorResearchCapability(
        tool="analyze_traffic_structure", title="流量结构", description="比较 Listing/变体的自然、SP、SP 推荐、SB、SBV 与推荐流量结构及趋势。",
        required_inputs=COMPARISON_INPUTS, returns=("流量结构", "流量趋势"), parallelizable=True,
        observation_window="provider_default",
    ),
    CompetitorResearchCapability(
        tool="compare_traffic_keywords", title="流量词对比", description="比较流量词、广告词、关键词级趋势与变体关键词分布。",
        required_inputs=COMPARISON_INPUTS, returns=("流量词", "广告词", "关键词分布"), parallelizable=True,
        observation_window="requested_end_day",
    ),
    CompetitorResearchCapability(
        tool="replay_operations_history", title="运营时光机", description="复盘流量趋势、广告历史画像、广告趋势与 Campaign 变更。",
        required_inputs=COMPARISON_INPUTS, returns=("历史流量", "广告历史", "Campaign 变更"), parallelizable=True,
        observation_window="provider_default",
        drilldown_condition="仅在用户明确要求运营历史或复盘时使用。",
    ),
    CompetitorResearchCapability(
        tool="inspect_ad_architecture", title="广告架构", description="查看广告架构以及 Campaign 贡献总览。",
        required_inputs=COMPARISON_INPUTS, returns=("广告架构", "Campaign 贡献"), parallelizable=True,
        observation_window="provider_default",
    ),
    CompetitorResearchCapability(
        tool="analyze_recommendation_traffic", title="推荐专栏流量", description="分析推荐专栏来源与推荐流量差距。",
        required_inputs=COMPARISON_INPUTS, returns=("推荐来源", "推荐流量"), parallelizable=True,
        observation_window="provider_default",
        drilldown_condition="仅在用户明确要求推荐专栏或推荐流量时使用。",
    ),
    CompetitorResearchCapability(
        tool="inspect_campaign", title="Campaign 拆解", description="查看指定 Campaign 的结构、趋势与关键词/广告组贡献。",
        required_inputs=DETAIL_INPUTS, returns=("Campaign 结构", "Campaign 趋势", "贡献拆解"), parallelizable=False,
        observation_window="requested_range",
        dependencies=("confirmed_campaign_id",), drilldown_condition="仅在用户明确要求且已确认 Campaign ID 时使用。",
    ),
    CompetitorResearchCapability(
        tool="inspect_ad_group", title="广告组与广告词拆解", description="查看指定广告组的趋势和广告词明细。",
        required_inputs=AD_GROUP_INPUTS, returns=("广告组趋势", "广告词明细"), parallelizable=False,
        observation_window="requested_end_day",
        dependencies=("confirmed_campaign_id", "confirmed_ad_group_id"),
        drilldown_condition="仅在用户明确要求且已确认 Campaign ID、广告组 ID 时使用。",
    ),
)

CAPABILITY_BY_TOOL = {item.tool: item for item in COMPETITOR_RESEARCH_CAPABILITIES}
OVERVIEW_TOOLS = (
    "compare_asin_sales", "analyze_traffic_structure", "compare_traffic_keywords", "inspect_ad_architecture",
)
# Tools that always observe the provider's own default window, so a report can
# never claim they were aligned with a requested period.
PROVIDER_DEFAULT_WINDOW_TOOLS = tuple(
    item.tool for item in COMPETITOR_RESEARCH_CAPABILITIES if item.observation_window == "provider_default"
)


def competitor_research_catalog() -> list[dict[str, object]]:
    """Return JSON-safe trusted metadata for MCP clients and the chat router."""
    return [
        {
            "tool": item.tool,
            "title": item.title,
            "description": item.description,
            "required_inputs": list(item.required_inputs),
            "returns": list(item.returns),
            "parallelizable": item.parallelizable,
            "observation_window": item.observation_window,
            "dependencies": list(item.dependencies),
            "drilldown_condition": item.drilldown_condition,
        }
        for item in COMPETITOR_RESEARCH_CAPABILITIES
    ]

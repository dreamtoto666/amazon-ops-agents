from __future__ import annotations

from typing import Any, Mapping


def build_capability_answer(state: Mapping[str, Any]) -> str | None:
    """Return a factual answer for explicit MCP capability questions."""

    messages = state.get("messages", [])
    if not messages:
        return None
    latest = messages[-1]
    content = (
        latest.get("content", "")
        if isinstance(latest, Mapping)
        else getattr(latest, "content", "")
    )
    text = str(content).lower().replace(" ", "")
    asks_capability = any(
        marker in text
        for marker in ("能用", "可以用", "使用", "接入", "配置", "可用", "mcp")
    )
    if not asks_capability:
        return None

    capabilities = state.get("system_capabilities", {})
    mcp = capabilities.get("mcp", {}) if isinstance(capabilities, Mapping) else {}
    agents = capabilities.get("agents", {}) if isinstance(capabilities, Mapping) else {}
    listing_registered = bool(
        isinstance(agents, Mapping)
        and isinstance(agents.get("listing_content"), Mapping)
        and agents["listing_content"].get("specialist_registered")
    )

    if "卖家精灵" in text or "sellersprite" in text:
        if not _configured(mcp, "seller_sprite"):
            return "卖家精灵 MCP 当前未配置，暂时不能调用。"
        if listing_registered:
            return (
                "可以。卖家精灵 MCP 已配置，并已接入 Listing 文案 Agent，用于关键词挖掘、"
                "竞品关键词和流量证据研究。你不需要手动选择 Agent，直接告诉总控具体的 "
                "Listing 任务即可，例如“根据这份商品资料挖掘 30 个关键词并编写美国站 "
                "Listing”，总控会自动路由到 Listing 文案 Agent。当前尚未注册独立的市场风险"
                "和广告分析 Agent，因此暂不承诺独立竞品诊断或广告分析任务。"
            )
        return "卖家精灵 MCP 已配置，但当前没有注册能够调用它的专业 Agent。"

    if "sif" in text:
        if not _configured(mcp, "sif"):
            return "Sif MCP 当前未配置，暂时不能调用。"
        if listing_registered:
            return (
                "可以。Sif MCP 已配置，并已接入 Listing 文案 Agent，用于关键词、流量和 "
                "Listing 关键词分布验证。你只需向总控描述完整的 Listing 任务，总控会自动路由。"
            )
        return "Sif MCP 已配置，但当前没有注册能够调用它的专业 Agent。"

    if "领星" in text or "lingxing" in text:
        if not _configured(mcp, "lingxing"):
            return "领星 MCP 当前尚未配置，因此暂时不能查询店铺经营、商品、广告、库存和利润数据。"
        return (
            "领星 MCP 已完成连接配置，并通过只读工具发现验证。当前只有 Listing 文案 Agent "
            "完成注册，销售利润、广告、库存和市场风险 Agent 尚未接入总控，因此暂时不会把经营"
            "查询路由到领星；需要先完成对应专业 Agent 与领星只读工具的字段映射。"
        )

    return None


def _configured(mcp: Any, provider: str) -> bool:
    return bool(
        isinstance(mcp, Mapping)
        and isinstance(mcp.get(provider), Mapping)
        and mcp[provider].get("configured")
    )

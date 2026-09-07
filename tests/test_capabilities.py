from amazon_ops.capabilities import build_capability_answer


def test_seller_sprite_answer_only_promises_registered_listing_agent():
    answer = build_capability_answer(
        {
            "messages": [{"role": "user", "content": "能使用卖家精灵 MCP 吗"}],
            "system_capabilities": {
                "mcp": {"seller_sprite": {"configured": True}},
                "agents": {"listing_content": {"specialist_registered": True}},
            },
        }
    )

    assert answer is not None
    assert "自动路由到 Listing 文案 Agent" in answer
    assert "尚未注册独立的市场风险和广告分析 Agent" in answer
    assert "手动选择" in answer


def test_lingxing_is_not_used_by_the_operations_assistant():
    answer = build_capability_answer(
        {
            "messages": [{"role": "user", "content": "领星 MCP 配置了吗"}],
            "system_capabilities": {
                "mcp": {"lingxing": {"configured": False}},
                "agents": {},
            },
        }
    )

    assert answer is not None
    assert answer == "该 MCP 能力当前未接入运营助手。"

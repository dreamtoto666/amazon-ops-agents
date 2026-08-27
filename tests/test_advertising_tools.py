from amazon_ops.advertising import ATTRIBUTION_TOOLS, INSPECTION_TOOLS, WRITE_TOOLS_DISABLED


def test_diagnostic_tool_policy_is_read_only():
    callable_tools = INSPECTION_TOOLS | ATTRIBUTION_TOOLS

    assert "ad_campaign_report" in callable_tools
    assert "ad_campaign_search_term_report" in callable_tools
    assert callable_tools.isdisjoint(WRITE_TOOLS_DISABLED)
    assert not any(
        tool.startswith(("put_", "post_", "create_", "add_", "update_"))
        for tool in callable_tools
    )

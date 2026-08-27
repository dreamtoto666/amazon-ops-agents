"""LingXing MCP tool policy for advertising diagnosis.

Only read tools are callable by this workflow. Write tools are catalogued for
future approval/execution adapters and must never be called while generating
diagnoses or todos.
"""

INSPECTION_TOOLS = frozenset(
    {
        "ad_auth_shops",
        "ad_campaign_report",
        "ad_portfolio_report_shop",
    }
)

ATTRIBUTION_TOOLS = frozenset(
    {
        "ad_campaign_group_report",
        "ad_campaign_product_report",
        "ad_campaign_keyword_report",
        "ad_campaign_targeting_report",
        "ad_campaign_search_term_report",
        "query_product_performance_asin_lists",
        "get_profit_report_msku",
        "query_order_profit_list_gross_profit",
        "get_fba_stock_list",
        "erp_listing",
    }
)

WRITE_TOOLS_DISABLED = frozenset(
    {
        "put_campaigns",
        "put_campaigns_sp",
        "put_campaigns_sd",
        "put_adGroups",
        "put_adGroups_sp",
        "put_adGroups_sd",
        "put_targets",
        "put_targets_sp",
        "put_targets_sd",
        "post_keywords",
        "post_targets",
        "post_targets_sp",
        "post_targets_sd",
        "post_productAds",
        "post_productAds_sd",
    }
)

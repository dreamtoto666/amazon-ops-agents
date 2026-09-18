import json
from datetime import date, timedelta

import pytest

from amazon_ops.competitor_advertising import CompetitorAdvertisingSpecialist
from amazon_ops.competitor_research import CompetitorResearchService, _parsed_content, _variant_asins
from amazon_ops.competitor_research_mcp_client import CompetitorResearchMCPClient
from amazon_ops.listing.mcp import MCPProviderClient, sif_mcp_config
from amazon_ops.models import AgentTask, DateRange, QueryScope, SpecialistName


def test_large_json_text_block_is_parsed_into_structured_evidence():
    """A Sif payload arrives as one JSON text block; it is decoded, never cut."""

    rows = [{"asin": f"B0{index:08d}", "title": "x" * 200} for index in range(200)]
    payload = {
        "content": [{"type": "text", "text": json.dumps({"asins": rows})}],
        "isError": False,
        "resultType": "tool",
    }

    parsed = _parsed_content(payload)

    text = parsed["content"][0]["text"]
    assert isinstance(text, dict)
    assert len(text["asins"]) == 200
    assert text["asins"][199]["asin"] == "B000000199"


def test_variant_asins_come_only_from_chars_dims():
    """Payload keys such as ``totalScore`` are ten uppercase alphanumerics, so a
    whole-payload regex scan used to request them as if they were variants."""

    data = {
        "content": [
            {
                "type": "text",
                "text": {
                    "chars": {
                        "dims": [
                            {"val": "[B0OWN00001](https://www.amazon.com/dp/B0OWN00001)", "isVariant": False},
                            {"val": "[B0OWN00002](https://www.amazon.com/dp/B0OWN00002)", "isVariant": True},
                            {"val": "no-asin-here", "isVariant": True},
                        ]
                    },
                    "totalScore": 12345,
                    "resultType": "tool",
                },
            }
        ],
        "isError": False,
    }

    assert _variant_asins(data) == ["B0OWN00002"]


class FakeTransport:
    def __init__(self):
        self.calls = []

    def list_tools(self, config):
        return []

    def call_tool(self, config, tool, arguments):
        self.calls.append((config.provider.value, tool, arguments))
        return {"data": {"items": [{"keyword": "phone stand"}]}}


class FailingTransport(FakeTransport):
    def __init__(self, failed_tools):
        super().__init__()
        self.failed_tools = set(failed_tools)

    def call_tool(self, config, tool, arguments):
        self.calls.append((config.provider.value, tool, arguments))
        if tool in self.failed_tools:
            raise RuntimeError("upstream failed")
        return {"data": {"items": [{"keyword": "phone stand"}]}}


def _service(transport):
    return CompetitorResearchService(MCPProviderClient(config=sif_mcp_config(), transport=transport))


def test_sales_operation_uses_only_sif_and_preserves_evidence():
    transport = FakeTransport()

    result = _service(transport).compare_asin_sales(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )

    assert result["ok"] is True
    assert {call[0] for call in transport.calls} == {"sif"}
    assert {call[1] for call in transport.calls} == {"ops_get_asin_sales_list", "ops_get_asin_sales_trend"}
    assert result["evidence"] and result["evidence"][0]["evidence_id"].startswith("mcp-")
    assert "公开可见研究数据" in result["limitations"][0]


def test_research_service_queries_every_confirmed_competitor():
    """There is no competitor-count cap: every confirmed ASIN is queried."""
    transport = FakeTransport()
    competitors = [f"B0COMP000{i}" for i in range(6)]

    _service(transport).analyze_traffic_structure(
        own_asin="B0OWN00001", competitor_asins=competitors, country="US"
    )

    queried = {arguments.get("asin") for _, _, arguments in transport.calls if isinstance(arguments, dict)}
    assert set(competitors) <= queried


def test_research_service_requires_country_before_calling_sif():
    transport = FakeTransport()

    with pytest.raises(ValueError, match="COUNTRY_REQUIRED"):
        _service(transport).compare_asin_sales(own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="")
    assert transport.calls == []


def test_partial_and_total_upstream_failures_are_reported_without_fabrication():
    partial = _service(FailingTransport({"ops_get_asin_sales_list"})).compare_asin_sales(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )
    total = _service(FailingTransport({"ops_get_asin_sales_list", "ops_get_asin_sales_trend"})).compare_asin_sales(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )

    assert partial["ok"] is True and partial["errors"]
    assert total["ok"] is False and total["evidence"] == []


@pytest.mark.parametrize(
    ("method", "arguments", "expected_tools"),
    [
        ("analyze_traffic_structure", {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}, {"ops_get_listing_traffic_overview", "ops_get_listing_traffic_structure", "ops_get_asin_traffic_trend"}),
        ("compare_traffic_keywords", {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}, {"market_get_asin_keyword_signals", "ops_get_listing_keyword_distribution", "ops_get_asin_traffic_trend_detail"}),
        ("replay_operations_history", {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}, {"ops_get_asin_traffic_trend", "ads_get_asin_ad_historical_feature_profile", "ads_get_asin_ad_traffic_trend", "ads_get_asin_campaign_changes"}),
        ("inspect_ad_architecture", {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}, {"ads_get_asin_ad_structure", "ads_get_asin_campaign_contribution_overview"}),
        ("analyze_recommendation_traffic", {"own_asin": "B0OWN00001", "competitor_asins": ["B0COMP0001"], "country": "US"}, {"ops_get_listing_traffic_overview"}),
        ("inspect_campaign", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "country": "US"}, {"ads_get_campaign_structure", "ads_get_campaign_traffic_trend", "ads_get_campaign_contribution_breakdown"}),
        ("inspect_ad_group", {"asin": "B0OWN00001", "campaign_id": "campaign-1", "ad_group_id": "group-1", "country": "US"}, {"ads_get_ad_group_traffic_trend", "ads_get_ad_group_keyword_breakdown"}),
    ],
)
def test_each_business_operation_has_a_bounded_sif_tool_set(method, arguments, expected_tools):
    transport = FakeTransport()

    result = getattr(_service(transport), method)(**arguments)

    assert result["ok"] is True
    assert {tool for _, tool, _ in transport.calls} == expected_tools


def test_default_chat_comparison_calls_only_parallel_overview_tools():
    calls = []

    def caller(tool, arguments):
        calls.append((tool, arguments))
        return {"ok": True, "summary": {}, "evidence": [], "limitations": [], "errors": []}

    specialist = CompetitorAdvertisingSpecialist(CompetitorResearchMCPClient(tool_caller=caller))
    result = specialist.invoke(
        AgentTask(task_id="task-1", agent=SpecialistName.COMPETITOR_ADVERTISING, objective="对比我和竞品", reason="test"),
        QueryScope(marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]), {},
    )

    assert result.status == "completed"
    assert {name for name, _ in calls} == {"compare_asin_sales", "analyze_traffic_structure", "compare_traffic_keywords", "inspect_ad_architecture"}
    assert "inspect_campaign" not in {name for name, _ in calls}


def test_competitor_keyword_signals_request_the_sif_maximum_top_n():
    transport = FakeTransport()

    _service(transport).compare_traffic_keywords(
        own_asin="B0OWN00001",
        competitor_asins=["B0COMP0001"],
        country="US",
    )

    signal_calls = [
        arguments
        for _, tool, arguments in transport.calls
        if tool == "market_get_asin_keyword_signals"
    ]
    assert len(signal_calls) == 2
    assert all(arguments["topN"] == 300 for arguments in signal_calls)
    assert all(arguments["listingSearch"] is True for arguments in signal_calls)


def test_parent_asin_comparison_expands_returned_variants_only_for_multi_organic_detail():
    transport = FakeTransport()
    own_parent, competitor_parent = "B0CZDH7649", "B09Z72Q5XN"

    _service(transport).analyze_traffic_structure(
        own_asin=own_parent, competitor_asins=[competitor_parent], country="US"
    )
    _service(transport).compare_traffic_keywords(
        own_asin=own_parent, competitor_asins=[competitor_parent], country="US"
    )

    # With no returned child dimensions, all outbound calls remain parent-only.
    # Child ASINs can be queried only after Sif returns them from the parent
    # Listing structure response.
    assert {
        arguments["asin"]
        for _, _, arguments in transport.calls
        if "asin" in arguments
    } == {own_parent, competitor_parent}


def test_traffic_structure_expands_only_returned_variant_asins_for_same_period_nf_details():
    class VariantStructureTransport(FakeTransport):
        def call_tool(self, config, tool, arguments):
            self.calls.append((config.provider.value, tool, arguments))
            if tool == "ops_get_listing_traffic_structure":
                suffix = "2" if arguments["asin"] == "B0OWN00001" else "3"
                return {"data": {"chars": {"dims": [{"val": f"[B0CHLD000{suffix}]"}]}}}
            return {"data": {"items": []}}

    transport = VariantStructureTransport()
    _service(transport).analyze_traffic_structure(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )

    detail_calls = [
        arguments for _, tool, arguments in transport.calls
        if tool == "ops_get_asin_traffic_trend_detail"
    ]
    assert {item["asin"] for item in detail_calls} == {"B0CHLD0002", "B0CHLD0003"}
    assert all(item["keywordType"] == "nf" and item["granularity"] == "week" for item in detail_calls)


def test_overview_calls_use_expanded_sif_result_limits():
    transport = FakeTransport()
    service = _service(transport)

    service.analyze_traffic_structure(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )
    service.compare_traffic_keywords(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )
    service.inspect_ad_architecture(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US"
    )

    by_tool = {}
    for _, tool, arguments in transport.calls:
        by_tool.setdefault(tool, []).append(arguments)
    assert all(item["pageSize"] == 40 for item in by_tool["ops_get_listing_traffic_structure"])
    assert all(item["pageSize"] == 200 for item in by_tool["ops_get_listing_keyword_distribution"])
    assert all(item["pageSize"] == 200 for item in by_tool["ops_get_asin_traffic_trend_detail"])
    assert all(item["granularity"] == "day" for item in by_tool["ops_get_asin_traffic_trend_detail"])
    assert all(item["limit"] == 40 for item in by_tool["ads_get_asin_campaign_contribution_overview"])


def test_ad_group_request_requires_confirmed_ids():
    specialist = CompetitorAdvertisingSpecialist(CompetitorResearchMCPClient(tool_caller=lambda *_: {}))
    result = specialist.invoke(
        AgentTask(task_id="task-1", agent=SpecialistName.COMPETITOR_ADVERTISING, objective="查广告组和广告词", reason="test"),
        QueryScope(marketplaces=["US"], own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]), {},
    )

    assert result.status == "needs_input"


# --- 默认时间窗 -------------------------------------------------------------

FIXED_WINDOW_START = date(2026, 9, 1)
FIXED_WINDOW_END = date(2026, 9, 7)


@pytest.fixture
def fixed_window(monkeypatch):
    """Pin the default window so call-site wiring can be asserted exactly."""

    monkeypatch.setattr(
        CompetitorResearchService,
        "_recent_complete_window",
        staticmethod(lambda today=None: (FIXED_WINDOW_START, FIXED_WINDOW_END)),
    )


@pytest.mark.parametrize(
    "today",
    [
        date(2026, 9, 7),  # 周一：旧实现返回「昨天」，并让窗口伸进未来
        date(2026, 9, 8),
        date(2026, 9, 9),
        date(2026, 9, 10),
        date(2026, 9, 11),
        date(2026, 9, 12),
        date(2026, 9, 13),  # 周日
    ],
)
def test_default_window_is_seven_complete_days_ending_yesterday(today):
    start, end = CompetitorResearchService._recent_complete_window(today)

    assert (end - start).days == 6
    assert end == today - timedelta(days=1)
    assert end < today, "默认窗口绝不能包含今天或未来"
    assert start >= today - timedelta(days=7)


def test_default_window_is_the_same_function_of_today_for_every_weekday():
    """The old helper keyed off today.weekday(), so Monday behaved differently."""

    monday = date(2026, 9, 7)
    windows = [
        CompetitorResearchService._recent_complete_window(monday + timedelta(days=offset))
        for offset in range(7)
    ]

    assert windows == [
        (monday + timedelta(days=offset - 7), monday + timedelta(days=offset - 1))
        for offset in range(7)
    ]


def test_every_default_window_comes_from_the_shared_helper(fixed_window):
    """All three date arguments must flow through one helper, not inline today()."""

    transport = FakeTransport()
    service = _service(transport)

    service.compare_traffic_keywords(own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US")
    service.inspect_campaign(asin="B0OWN00001", campaign_id="campaign-1", country="US")
    service.inspect_ad_group(
        asin="B0OWN00001", campaign_id="campaign-1", ad_group_id="group-1", country="US"
    )

    sent = {tool: arguments for _, tool, arguments in transport.calls}
    assert sent["ops_get_asin_traffic_trend_detail"]["endDay"] == FIXED_WINDOW_END.isoformat()
    assert sent["ads_get_campaign_contribution_breakdown"]["start_date"] == FIXED_WINDOW_START.isoformat()
    assert sent["ads_get_campaign_contribution_breakdown"]["end_date"] == FIXED_WINDOW_END.isoformat()
    assert sent["ads_get_ad_group_keyword_breakdown"]["date"] == FIXED_WINDOW_END.isoformat()


def test_campaign_window_is_never_inverted_or_open_ended(fixed_window):
    """The breakdown range must be a single bounded, ordered span."""

    transport = FakeTransport()
    _service(transport).inspect_campaign(asin="B0OWN00001", campaign_id="campaign-1", country="US")

    breakdown = next(args for _, tool, args in transport.calls if tool == "ads_get_campaign_contribution_breakdown")
    start = date.fromisoformat(breakdown["start_date"])
    end = date.fromisoformat(breakdown["end_date"])

    assert start <= end
    assert end < date.today()


# --- 周期贯通：请求周期必须真的传给 Sif，无法对齐的必须登记 ------------------

PERIOD = DateRange(start=date(2026, 8, 31), end=date(2026, 9, 6))


def test_a_confirmed_period_replaces_the_default_window():
    """回归：解释器解析出的周期曾被完全忽略，静默换成最近 7 天。"""

    transport = FakeTransport()
    service = _service(transport)

    service.inspect_campaign(asin="B0OWN00001", campaign_id="c1", country="US", period=PERIOD)
    campaign = next(args for _, tool, args in transport.calls if tool == "ads_get_campaign_contribution_breakdown")
    assert (campaign["start_date"], campaign["end_date"]) == ("2026-08-31", "2026-09-06")

    transport = FakeTransport()
    _service(transport).inspect_ad_group(
        asin="B0OWN00001", campaign_id="c1", ad_group_id="g1", country="US", period=PERIOD
    )
    breakdown = next(args for _, tool, args in transport.calls if tool == "ads_get_ad_group_keyword_breakdown")
    assert breakdown["date"] == "2026-09-06"

    transport = FakeTransport()
    _service(transport).compare_traffic_keywords(
        own_asin="B0OWN00001", competitor_asins=["B0COMP0001"], country="US", period=PERIOD
    )
    detail = next(args for _, tool, args in transport.calls if tool == "ops_get_asin_traffic_trend_detail")
    assert detail["endDay"] == "2026-09-06"


def test_an_absent_period_keeps_the_default_window():
    transport = FakeTransport()

    _service(transport).inspect_campaign(asin="B0OWN00001", campaign_id="c1", country="US")

    campaign = next(args for _, tool, args in transport.calls if tool == "ads_get_campaign_contribution_breakdown")
    today = date.today()
    assert (campaign["start_date"], campaign["end_date"]) == (
        (today - timedelta(days=7)).isoformat(),
        (today - timedelta(days=1)).isoformat(),
    )


def test_an_unusable_period_is_rejected_instead_of_silently_replaced():
    with pytest.raises(ValueError, match="PERIOD_INVALID"):
        _service(FakeTransport()).inspect_campaign(
            asin="B0OWN00001", campaign_id="c1", country="US",
            period=DateRange(start=date(2026, 9, 6), end=date(2026, 8, 31)),
        )


def test_today_is_allowed_but_date_controlled_evidence_is_marked_unmatured():
    today = date.today()
    result = _service(FakeTransport()).compare_traffic_keywords(
        own_asin="B0OWN00001",
        competitor_asins=["B0COMP0001"],
        country="US",
        period=DateRange(start=today, end=today),
    )

    windows = {item["tool"]: item["observation_window"] for item in result["evidence"]}
    assert windows["ops_get_asin_traffic_trend_detail"]["mode"] == "requested_end_day"
    assert windows["ops_get_asin_traffic_trend_detail"]["effective"] == {"end": today.isoformat()}
    assert windows["ops_get_asin_traffic_trend_detail"]["maturity"] == "UNMATURED"
    assert windows["market_get_asin_keyword_signals"]["maturity"] == "UNKNOWN"
    assert result["observation_window"]["alignment"] == "partially_aligned"
    assert result["observation_window"]["maturity"] == "UNMATURED"


def test_future_period_is_rejected_before_sif_is_called():
    transport = FakeTransport()
    tomorrow = date.today() + timedelta(days=1)

    with pytest.raises(ValueError, match="PERIOD_IN_FUTURE"):
        _service(transport).compare_traffic_keywords(
            own_asin="B0OWN00001",
            competitor_asins=["B0COMP0001"],
            country="US",
            period=DateRange(start=tomorrow, end=tomorrow),
        )

    assert transport.calls == []

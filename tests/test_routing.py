from amazon_ops.models import (
    Action,
    Domain,
    QueryScope,
    SpecialistName,
    UserIntent,
)
from amazon_ops.routing import build_initial_plan


def test_focused_product_diagnosis_fans_out_to_all_specialists():
    intent = UserIntent(domain=Domain.PRODUCT, action=Action.DIAGNOSE, confidence=0.95)
    scope = QueryScope(asins=["B0TEST"])

    plan = build_initial_plan(intent, scope, "诊断 B0TEST 销量下降原因")

    assert {task.agent for task in plan.tasks} == {
        SpecialistName.SALES_PROFIT,
        SpecialistName.INVENTORY,
        SpecialistName.MARKET_RISK,
    }
    assert plan.execution_mode == "parallel"


def test_profit_query_routes_only_to_sales_profit():
    intent = UserIntent(domain=Domain.PROFIT, action=Action.QUERY, confidence=0.99)

    plan = build_initial_plan(intent, QueryScope(), "查询利润")

    assert [task.agent for task in plan.tasks] == [SpecialistName.SALES_PROFIT]
    assert plan.max_rounds == 1


def test_advertising_query_routes_to_advertising_specialist():
    intent = UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.99)

    plan = build_initial_plan(intent, QueryScope(), "查询广告花费")

    assert [task.agent for task in plan.tasks] == [SpecialistName.ADVERTISING]


def test_custom_report_query_has_a_safe_default_route():
    intent = UserIntent(domain=Domain.REPORT, action=Action.QUERY, confidence=0.9)

    plan = build_initial_plan(intent, QueryScope(), "查询月度销售汇总报表")

    assert [task.agent for task in plan.tasks] == [SpecialistName.SALES_PROFIT]


def test_listing_copy_request_routes_to_listing_content_agent():
    intent = UserIntent(domain=Domain.LISTING, action=Action.CREATE, confidence=0.98)

    plan = build_initial_plan(intent, QueryScope(marketplaces=["US"]), "编写 Listing 文案")

    assert [task.agent for task in plan.tasks] == [SpecialistName.LISTING_CONTENT]


def test_competitor_comparison_routes_to_competitor_advertising_specialist():
    intent = UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=0.98)

    plan = build_initial_plan(intent, QueryScope(own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]), "对比竞品广告信号")

    assert [task.agent for task in plan.tasks] == [SpecialistName.COMPETITOR_ADVERTISING]


def test_competitor_report_routes_to_competitor_and_own_advertising_data():
    intent = UserIntent(domain=Domain.COMPETITOR, action=Action.COMPARE, confidence=0.98)

    plan = build_initial_plan(
        intent,
        QueryScope(own_asin="B0OWN00001", competitor_asins=["B0COMP0001"]),
        "生成竞品对比报告",
        response_mode="competitor_report",
    )

    assert {task.agent for task in plan.tasks} == {
        SpecialistName.COMPETITOR_ADVERTISING,
        SpecialistName.ADVERTISING,
    }
    assert plan.execution_mode == "parallel"

from amazon_ops.models import (
    Action,
    Domain,
    QueryScope,
    SpecialistName,
    UserIntent,
)
from amazon_ops.routing import build_initial_plan


def test_llm_selected_agents_are_preserved_for_a_product_diagnosis():
    intent = UserIntent(
        domain=Domain.PRODUCT,
        action=Action.DIAGNOSE,
        primary_agent=SpecialistName.SALES_PROFIT,
        supporting_agents=[SpecialistName.INVENTORY, SpecialistName.MARKET_RISK],
        confidence=0.95,
    )
    scope = QueryScope(asins=["B0TEST"])

    plan = build_initial_plan(intent, scope, "诊断 B0TEST 销量下降原因")

    assert {task.agent for task in plan.tasks} == {
        SpecialistName.SALES_PROFIT,
        SpecialistName.INVENTORY,
        SpecialistName.MARKET_RISK,
    }
    assert plan.execution_mode == "parallel"


def test_llm_selected_primary_agent_is_used_for_a_profit_query():
    intent = UserIntent(domain=Domain.PROFIT, action=Action.QUERY, primary_agent=SpecialistName.SALES_PROFIT, confidence=0.99)

    plan = build_initial_plan(intent, QueryScope(), "查询利润")

    assert [task.agent for task in plan.tasks] == [SpecialistName.SALES_PROFIT]
    assert plan.max_rounds == 1


def test_llm_selected_primary_agent_is_used_for_an_advertising_query():
    intent = UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, primary_agent=SpecialistName.ADVERTISING, confidence=0.99)

    plan = build_initial_plan(intent, QueryScope(), "查询广告花费")

    assert [task.agent for task in plan.tasks] == [SpecialistName.ADVERTISING]


def test_plan_is_unsupported_when_llm_does_not_select_an_agent():
    intent = UserIntent(domain=Domain.ADVERTISING, action=Action.QUERY, confidence=0.99)

    plan = build_initial_plan(intent, QueryScope(), "查询广告花费")

    assert plan.route.value == "unsupported"
    assert plan.tasks == []


def test_llm_selected_primary_agent_is_used_for_a_report_query():
    intent = UserIntent(domain=Domain.REPORT, action=Action.QUERY, primary_agent=SpecialistName.SALES_PROFIT, confidence=0.9)

    plan = build_initial_plan(intent, QueryScope(), "查询月度销售汇总报表")

    assert [task.agent for task in plan.tasks] == [SpecialistName.SALES_PROFIT]


def test_llm_selected_primary_agent_is_used_for_a_listing_request():
    intent = UserIntent(domain=Domain.LISTING, action=Action.CREATE, primary_agent=SpecialistName.LISTING_CONTENT, confidence=0.98)

    plan = build_initial_plan(intent, QueryScope(marketplaces=["US"]), "编写 Listing 文案")

    assert [task.agent for task in plan.tasks] == [SpecialistName.LISTING_CONTENT]

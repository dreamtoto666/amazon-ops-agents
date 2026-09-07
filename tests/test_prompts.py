import json

from amazon_ops.prompts import (
    DIRECT_RESPONDER_SYSTEM_PROMPT,
    REQUEST_INTERPRETER_SYSTEM_PROMPT,
    RESULT_AGGREGATOR_SYSTEM_PROMPT,
    build_aggregation_context,
    build_direct_response_context,
    build_request_context,
)


def test_interpreter_prompt_contains_core_safety_and_routing_contracts():
    assert "不调用任何外部 MCP" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "不得要求 `shop_id`" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "不得因缺少 `period` 进入 clarify" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "确定性路由器" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "route=approval" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "低 confidence 本身不是追问理由" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "能力询问本身判为 unsupported" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "只输出符合 UnderstandRequestResult Schema" in REQUEST_INTERPRETER_SYSTEM_PROMPT


def test_aggregator_prompt_separates_facts_hypotheses_and_actions():
    assert "hypothesis：永远是待验证假设" in RESULT_AGGREGATOR_SYSTEM_PROMPT
    assert "不得声称操作已经执行" in RESULT_AGGREGATOR_SYSTEM_PROMPT
    assert "只输出符合 FinalResponse Schema" in RESULT_AGGREGATOR_SYSTEM_PROMPT


def test_direct_responder_does_not_require_manual_agent_selection():
    assert "用户不需要手动进入或选择专业 Agent" in DIRECT_RESPONDER_SYSTEM_PROMPT
    assert "不得推荐尚未注册" in DIRECT_RESPONDER_SYSTEM_PROMPT


def test_request_context_only_contains_runtime_inputs():
    context = json.loads(
        build_request_context(
            {
                "current_time": "2026-08-13T10:00:00+08:00",
                "user_context": {"timezone": "Asia/Shanghai"},
                "shop_directory": [{"shop_id": "1"}],
                "messages": [{"role": "user", "content": "看下利润"}],
                "specialist_results": [{"should": "not leak"}],
            }
        )
    )
    assert set(context) == {
        "current_time",
        "user_context",
        "shop_directory",
        "system_capabilities",
        "messages",
    }


def test_aggregation_context_does_not_include_unneeded_user_configuration():
    context = json.loads(
        build_aggregation_context(
            {
                "understanding": {"normalized_request": "查询利润"},
                "specialist_results": [],
                "errors": [],
                "called_agents": ["sales_profit"],
                "round": 1,
                "user_context": {"secret": "not included"},
            }
        )
    )
    assert "user_context" not in context
    assert context["round"] == 1


def test_direct_responder_receives_runtime_capabilities():
    context = json.loads(
        build_direct_response_context(
            {
                "understanding": {"intent": {"domain": "system"}},
                "system_capabilities": {
                    "mcp": {"seller_sprite": {"configured": True}}
                },
                "messages": [{"role": "user", "content": "能用卖家精灵吗"}],
            }
        )
    )

    assert context["system_capabilities"]["mcp"]["seller_sprite"]["configured"] is True

import json
from amazon_ops.prompts import (
    DIRECT_RESPONDER_SYSTEM_PROMPT,
    INTERPRETER_HISTORY_MAX_CHARS,
    REQUEST_INTERPRETER_SYSTEM_PROMPT,
    RESULT_AGGREGATOR_SYSTEM_PROMPT,
    build_aggregation_context,
    build_direct_response_context,
    build_request_context,
)


def test_interpreter_prompt_contains_core_safety_and_routing_contracts():
    assert len(REQUEST_INTERPRETER_SYSTEM_PROMPT) < 4_000
    assert "不调用工具" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "history_gate.force_live=true" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "answer_source=history" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "response_mode=competitor_report" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "不因缺 shop_id 或 period 追问" in REQUEST_INTERPRETER_SYSTEM_PROMPT
    assert "只输出符合 Schema 的 JSON" in REQUEST_INTERPRETER_SYSTEM_PROMPT


def test_aggregator_prompt_separates_facts_hypotheses_and_actions():
    assert "hypothesis：永远是待验证假设" in RESULT_AGGREGATOR_SYSTEM_PROMPT
    assert "不得声称操作已经执行" in RESULT_AGGREGATOR_SYSTEM_PROMPT
    assert "只输出符合 FinalResponse Schema" in RESULT_AGGREGATOR_SYSTEM_PROMPT


def test_direct_responder_does_not_require_manual_agent_selection():
    assert "用户不需要手动进入或选择专业 Agent" in DIRECT_RESPONDER_SYSTEM_PROMPT
    assert "不得推荐尚未注册" in DIRECT_RESPONDER_SYSTEM_PROMPT
    assert "根据本次对话此前取得的数据" in DIRECT_RESPONDER_SYSTEM_PROMPT
    assert "不调用 MCP、工具或专家 Agent" in DIRECT_RESPONDER_SYSTEM_PROMPT


def test_request_context_separates_history_from_current_user_message():
    context = json.loads(
        build_request_context(
            {
                "current_time": "2026-08-13T10:00:00+08:00",
                "user_context": {"timezone": "Asia/Shanghai"},
                "shop_directory": [{"shop_id": "1"}],
                "messages": [
                    {"role": "user", "content": "查询广告花费"},
                    {"role": "assistant", "content": "查询完成"},
                    {"role": "user", "content": "没让你使用搜索工具"},
                ],
                "conversation_history": [
                    {"role": "user", "content": "查询广告花费"},
                    {"role": "assistant", "content": "查询完成"},
                ],
                "current_user_message": "没让你使用搜索工具",
                "specialist_results": [{"should": "not leak"}],
            }
        )
    )
    assert set(context) == {
        "current_time",
        "user_context",
        "shop_directory",
        "system_capabilities",
        "history_gate",
        "conversation_history",
        "current_user_message",
    }
    assert context["current_user_message"] == "没让你使用搜索工具"
    assert context["current_user_message"] not in {
        item["content"] for item in context["conversation_history"]
    }


def test_request_context_precomputes_history_gate_for_report_followup():
    context = json.loads(
        build_request_context(
            {
                "conversation_history": [
                    {"role": "assistant", "content": "报告机会与建议"}
                ],
                "current_user_message": "针对给出的机会，我应该调整哪些词？",
            }
        )
    )

    assert context["history_gate"]["allowed"] is True
    assert context["history_gate"]["force_live"] is False
    assert "给出的机会" in context["history_gate"]["matched_history_markers"]


def test_request_context_force_live_takes_priority_over_history_reference():
    context = json.loads(
        build_request_context(
            {
                "conversation_history": [{"role": "assistant", "content": "旧报告"}],
                "current_user_message": "根据这份报告重新查一下最新数据",
            }
        )
    )

    assert context["history_gate"]["force_live"] is True
    assert context["history_gate"]["allowed"] is False


def test_request_context_bounds_a_large_report_for_intent_routing():
    long_report = "报告开头" + ("数据" * 20_000) + "报告结尾"
    context = json.loads(
        build_request_context(
            {
                "conversation_history": [
                    {"role": "assistant", "content": long_report}
                ],
                "current_user_message": "针对给出的机会继续解释",
            }
        )
    )
    bounded = context["conversation_history"][0]["content"]

    assert len(bounded) <= INTERPRETER_HISTORY_MAX_CHARS
    assert len(bounded) < len(long_report)
    assert bounded.startswith("报告开头")
    assert bounded.endswith("报告结尾")
    assert "历史内容已限长" in bounded


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
                "conversation_history": [
                    {"role": "assistant", "content": "此前报告结论"}
                ],
                "current_user_message": "总结一下前面的结果",
            }
        )
    )

    assert context["system_capabilities"]["mcp"]["seller_sprite"]["configured"] is True
    assert context["conversation_history"][0]["content"] == "此前报告结论"
    assert context["current_user_message"] == "总结一下前面的结果"


def test_aggregation_context_carries_deterministic_competitor_data_modules():
    """没有消费者的话，数据处理阶段就是纯浪费。"""

    modules = {"own_parent_asin": "B0OWN00001", "competitor_parent_asin": "B0COMP0001", "marketplace": "US"}
    context = json.loads(
        build_aggregation_context(
            {"understanding": {}, "specialist_results": [], "competitor_data_modules": modules}
        )
    )

    assert context["competitor_data_modules"] == modules


def test_aggregation_context_omits_absent_competitor_data_modules():
    context = json.loads(build_aggregation_context({"understanding": {}, "specialist_results": []}))

    assert "competitor_data_modules" not in context


def test_aggregator_prompt_defines_the_credibility_of_competitor_data_modules():
    assert "competitor_data_modules" in RESULT_AGGREGATOR_SYSTEM_PROMPT

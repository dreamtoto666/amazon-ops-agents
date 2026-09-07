from __future__ import annotations

import json

import httpx
import pytest

from amazon_ops import (
    DeepSeekConfig,
    DeepSeekRequestInterpreter,
    DeepSeekResultAggregator,
    DeepSeekStructuredLLM,
    LLMError,
)
from amazon_ops.deepseek_runtime import build_deepseek_model_roles, build_deepseek_react_model
from amazon_ops.events import InMemoryEventHub, StageController, StageName, StageReporter
from amazon_ops.listing import DeepSeekListingCopywriter, ListingDraft
from amazon_ops.models import FinalResponse, UnderstandRequestResult


def test_deepseek_config_uses_default_url_when_host_sets_empty_override(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "")

    config = DeepSeekConfig.from_env(env_file=tmp_path / "missing.env")

    assert config.base_url == "https://api.deepseek.com"


def test_react_model_disables_thinking_by_default(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    model = build_deepseek_react_model(env_file=tmp_path / "missing.env")

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_react_model_enables_thinking_with_reasoning_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    model = build_deepseek_react_model(
        env_file=tmp_path / "missing.env", reasoning_effort="high"
    )

    assert model.extra_body == {"thinking": {"type": "enabled"}, "reasoning_effort": "high"}


def test_react_model_off_effort_keeps_thinking_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    model = build_deepseek_react_model(
        env_file=tmp_path / "missing.env", reasoning_effort="off"
    )

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_build_deepseek_model_roles_propagates_reasoning_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")

    roles = build_deepseek_model_roles(
        env_file=tmp_path / "missing.env", reasoning_effort="high"
    )

    assert roles.llm.config.reasoning_effort == "high"
    assert roles.advertising_react_model.extra_body == {
        "thinking": {"type": "enabled"}, "reasoning_effort": "high"
    }


def test_deepseek_client_requests_current_model_and_validates_json_output():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Phone Stand",
                                    "subtitle": "Stable desktop holder",
                                    "bullet_points": ["One", "Two", "Three", "Four", "Five"],
                                    "description": "An adjustable stand.",
                                    "search_terms": "desk holder phone cradle",
                                }
                            )
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
            },
        )

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(model="deepseek-v4-flash"),
        api_key="test-key",
        http_client=http_client,
    )

    result = llm.complete(
        system_prompt="Generate a JSON listing.",
        context='{"product":"stand"}',
        output_model=ListingDraft,
    )

    assert result.title == "Phone Stand"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["body"]["model"] == "deepseek-v4-flash"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["thinking"] == {"type": "disabled"}
    assert "JSON Schema" in captured["body"]["messages"][0]["content"]
    assert llm.last_usage["total_tokens"] == 150


def test_pro_selection_sends_the_pro_model_to_the_deepseek_api():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-pro-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps({"answer": "已切换到 Pro"})},
                    }
                ],
            },
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(model="deepseek-v4-pro"),
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = llm.complete(
        system_prompt="Return JSON.", context="{}", output_model=FinalResponse
    )

    assert result.answer == "已切换到 Pro"
    assert captured["body"]["model"] == "deepseek-v4-pro"


def test_deepseek_complete_sends_reasoning_effort_when_configured():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['body'] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                'id': 'chat-effort-1',
                'choices': [
                    {
                        'finish_reason': 'stop',
                        'message': {'content': json.dumps({'answer': 'ok'})},
                    }
                ],
            },
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(model='deepseek-v4-pro', reasoning_effort='high'),
        api_key='test-key',
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = llm.complete(
        system_prompt='Return JSON.', context='{}', output_model=FinalResponse
    )

    assert result.answer == 'ok'
    assert captured['body']['model'] == 'deepseek-v4-pro'
    assert captured['body']['thinking'] == {'type': 'enabled'}
    assert captured['body']['reasoning_effort'] == 'high'


def test_deepseek_complete_off_effort_disables_thinking_and_omits_effort():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured['body'] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                'id': 'chat-effort-off',
                'choices': [
                    {'finish_reason': 'stop', 'message': {'content': json.dumps({'answer': 'ok'})}},
                ],
            },
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(reasoning_effort='off'),
        api_key='test-key',
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    llm.complete(system_prompt='Return JSON.', context='{}', output_model=FinalResponse)

    assert captured['body']['thinking'] == {'type': 'disabled'}
    assert 'reasoning_effort' not in captured['body']


def test_deepseek_config_reads_reasoning_effort_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv('DEEPSEEK_REASONING_EFFORT', 'max')

    config = DeepSeekConfig.from_env(env_file=tmp_path / 'missing.env')

    assert config.reasoning_effort == 'max'


def test_deepseek_complete_captures_reasoning_content_when_thinking_enabled():
    reasoning = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        return httpx.Response(
            200,
            json={
                "id": "chat-reason-1",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "reasoning_content": "先判断意图，再决定路由。",
                            "content": json.dumps({"answer": "已理解"}),
                        },
                    }
                ],
            },
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(),
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = llm.complete(
        system_prompt="Return JSON.",
        context="{}",
        output_model=FinalResponse,
        thinking=True,
        on_reasoning_delta=reasoning.append,
    )

    assert result.answer == "已理解"
    assert reasoning == ["先判断意图，再决定路由。"]
    assert llm.last_reasoning == "先判断意图，再决定路由。"


def test_deepseek_text_stream_forwards_reasoning_deltas_when_enabled():
    emitted = []
    reasoning = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["thinking"] == {"type": "enabled"}
        return httpx.Response(
            200,
            content=(
                'data: {"choices":[{"delta":{"reasoning_content":"逐步分析"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"第一段"}}]}\n\n'
                'data: {"choices":[{"delta":{"reasoning_content":"继续推理"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"第二段"}}]}\n\n'
                'data: [DONE]\n\n'
            ),
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(),
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    answer = llm.stream_text(
        system_prompt="Answer directly.",
        context="{}",
        on_delta=emitted.append,
        on_reasoning_delta=reasoning.append,
        thinking=True,
    )

    assert answer == "第一段第二段"
    assert emitted == ["第一段", "第二段"]
    assert reasoning == ["逐步分析", "继续推理"]
    assert llm.last_reasoning == "逐步分析继续推理"


def test_deepseek_client_never_calls_network_without_api_key(monkeypatch):
    monkeypatch.delenv("TEST_DEEPSEEK_KEY", raising=False)
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(api_key_env="TEST_DEEPSEEK_KEY"),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(LLMError) as exc_info:
        llm.complete(system_prompt="JSON", context="{}", output_model=FinalResponse)

    assert exc_info.value.code == "DEEPSEEK_API_KEY_MISSING"
    assert called is False


class StubStructuredLLM:
    def __init__(self):
        self.calls = []

    def complete(self, *, system_prompt, context, output_model, max_tokens=None):
        self.calls.append((system_prompt, json.loads(context), output_model, max_tokens))
        if output_model is UnderstandRequestResult:
            return output_model.model_validate(
                {
                    "intent": {"domain": "profit", "action": "query", "confidence": 0.95},
                    "scope": {"shop_ids": ["1"]},
                    "route": "execute",
                    "risk_level": "read_only",
                    "normalized_request": "查询店铺利润",
                }
            )
        if output_model is FinalResponse:
            return output_model(answer="利润分析已完成")
        return output_model(
            title="Phone Stand",
            subtitle="Stable Holder",
            bullet_points=["One", "Two", "Three", "Four", "Five"],
            description="A stable holder.",
            search_terms="phone holder desk stand",
        )


def test_controller_and_listing_roles_share_the_structured_deepseek_boundary():
    llm = StubStructuredLLM()
    interpreter = DeepSeekRequestInterpreter(llm)
    aggregator = DeepSeekResultAggregator(llm)
    copywriter = DeepSeekListingCopywriter(llm)

    understood = interpreter.invoke(
        {
            "current_time": "2026-08-14T10:00:00+08:00",
            "messages": [{"role": "user", "content": "看利润"}],
        }
    )
    final = aggregator.invoke({"understanding": understood.model_dump(mode="json")})
    draft = copywriter.generate(
        {
            "validated_request": {"marketplace": "US", "language": "en-US"},
            "selected_keywords": [{"keyword": "phone stand", "tier": "primary"}],
        }
    )

    assert understood.intent.domain.value == "profit"
    assert final.answer == "利润分析已完成"
    assert draft.title == "Phone Stand"
    assert [call[2] for call in llm.calls] == [
        UnderstandRequestResult,
        FinalResponse,
        ListingDraft,
    ]
    assert llm.calls[2][1]["selected_keywords"][0]["keyword"] == "phone stand"


def test_request_interpreter_forwards_reasoning_to_stage_events():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "chat-reason-interpreter",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "reasoning_content": "用户问的是利润，走执行路由。",
                            "content": json.dumps(
                                {
                                    "intent": {
                                        "domain": "profit",
                                        "action": "query",
                                        "confidence": 0.95,
                                    },
                                    "scope": {"shop_ids": ["1"]},
                                    "route": "execute",
                                    "risk_level": "read_only",
                                    "normalized_request": "查询店铺利润",
                                }
                            ),
                        },
                    }
                ],
            },
        )

    llm = DeepSeekStructuredLLM(
        config=DeepSeekConfig(),
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    hub = InMemoryEventHub()
    stages = StageController(hub)
    stages.start("r-reasoning", StageName.UNDERSTANDING, title="正在理解你的问题")
    interpreter = DeepSeekRequestInterpreter(llm)
    state = {
        "messages": [{"role": "user", "content": "看利润"}],
        "_stage_reporter": StageReporter(
            controller=stages,
            run_id="r-reasoning",
            stage=StageName.UNDERSTANDING,
        ),
    }

    understood = interpreter.invoke(state)

    assert understood.normalized_request == "查询店铺利润"
    assert captured["body"]["thinking"] == {"type": "enabled"}
    events = hub.events_after("r-reasoning")
    reasoning_events = [
        event for event in events if event.data.get("kind") == "reasoning.delta"
    ]
    assert len(reasoning_events) == 1
    assert reasoning_events[0].data["text"] == "用户问的是利润，走执行路由。"
    assert reasoning_events[0].stage == StageName.UNDERSTANDING

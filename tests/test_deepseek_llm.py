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
from amazon_ops.listing import DeepSeekListingCopywriter, ListingDraft
from amazon_ops.models import FinalResponse, UnderstandRequestResult


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

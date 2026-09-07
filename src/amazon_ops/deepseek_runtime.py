from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
import langchain_openai.chat_models.base as _lc_openai_base
from dotenv import load_dotenv

from .interfaces import (
    DeepSeekDirectResponder,
    DeepSeekRequestInterpreter,
    DeepSeekResultAggregator,
)
from .listing.copywriter import DeepSeekListingCopywriter
from .llm import DeepSeekConfig, DeepSeekModelName, DeepSeekReasoningEffort, DeepSeekStructuredLLM, build_deepseek_llm


@dataclass(frozen=True)
class DeepSeekModelRoles:
    """One provider instance shared by every model-backed role."""

    llm: DeepSeekStructuredLLM
    request_interpreter: DeepSeekRequestInterpreter
    result_aggregator: DeepSeekResultAggregator
    direct_responder: DeepSeekDirectResponder
    listing_copywriter: DeepSeekListingCopywriter
    advertising_react_model: ChatOpenAI


# langchain-openai deliberately drops DeepSeek's reasoning_content delta field
# (it only handles OpenAI-standard fields). Patch its delta->chunk converter so
# the advertising ReAct model's streaming chunks carry reasoning_content in
# additional_kwargs, which report_specialist forwards as reasoning.delta events.
_ORIGINAL_DELTA_CONVERTER = _lc_openai_base._convert_delta_to_message_chunk
_reasoning_patch_installed = False


def _deepseek_delta_chunk_with_reasoning(
    delta: dict, default_class: type
):
    chunk = _ORIGINAL_DELTA_CONVERTER(delta, default_class)
    reasoning = delta.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning and isinstance(chunk, AIMessageChunk):
        try:
            chunk.additional_kwargs["reasoning_content"] = reasoning
        except Exception:
            pass
    return chunk


def _install_deepseek_reasoning_patch() -> None:
    """Idempotently install the reasoning_content delta patch for ChatOpenAI.

    The wrapper is a pure function and thread-safe; installing once per process
    means every ChatOpenAI streaming chunk keeps reasoning_content, without
    changing any other behavior.
    """
    global _reasoning_patch_installed
    if not _reasoning_patch_installed:
        _lc_openai_base._convert_delta_to_message_chunk = _deepseek_delta_chunk_with_reasoning
        _reasoning_patch_installed = True


def _react_thinking_extra_body(effort: DeepSeekReasoningEffort | None) -> dict[str, str]:
    """Wire thinking/effort for the advertising ReAct ChatOpenAI model.

    Measured against the configured DeepSeek endpoint: thinking can coexist with
    tools, but the ReAct graph deliberately does NOT force tool_choice (matches
    the harness's own request shape, which never sends it). effort low/high/max
    enables thinking plus the reasoning_effort; off/None keeps thinking disabled
    (the prior default).
    """
    if effort in {"low", "high", "max"}:
        return {"thinking": {"type": "enabled"}, "reasoning_effort": effort}
    return {"thinking": {"type": "disabled"}}


def build_deepseek_react_model(
    *,
    env_file: str | Path = ".env",
    model: DeepSeekModelName | None = None,
    reasoning_effort: DeepSeekReasoningEffort | None = None,
) -> ChatOpenAI:
    """Build a LangChain chat model for DeepSeek's OpenAI-compatible endpoint."""
    _install_deepseek_reasoning_patch()
    load_dotenv(env_file, override=False)
    config = DeepSeekConfig.from_env(env_file=env_file)
    update: dict[str, object] = {}
    if model is not None:
        update["model"] = model
    if reasoning_effort is not None:
        update["reasoning_effort"] = reasoning_effort
    if update:
        config = config.model_copy(update=update)
    return ChatOpenAI(
        model=config.model,
        api_key=os.getenv(config.api_key_env, "").strip() or None,
        base_url=config.base_url,
        temperature=config.temperature,
        timeout=config.timeout_seconds,
        max_retries=1,
        # Measured on the configured endpoint: thinking + tools coexist (the old
        # "rejects forced tool selection" note was outdated). We still avoid
        # forcing tool_choice, mirroring the harness. thinking is enabled only
        # when a reasoning_effort is selected.
        extra_body=_react_thinking_extra_body(config.reasoning_effort),
        http_client=httpx.Client(timeout=config.timeout_seconds, trust_env=False),
        http_async_client=httpx.AsyncClient(timeout=config.timeout_seconds, trust_env=False),
    )


def build_deepseek_model_roles(
    *,
    env_file: str | Path = ".env",
    trust_env: bool = False,
    model: DeepSeekModelName | None = None,
    reasoning_effort: DeepSeekReasoningEffort | None = None,
) -> DeepSeekModelRoles:
    llm = build_deepseek_llm(env_file=env_file, trust_env=trust_env)
    update: dict[str, object] = {}
    if model is not None:
        update["model"] = model
    if reasoning_effort is not None:
        update["reasoning_effort"] = reasoning_effort
    if update:
        llm.config = llm.config.model_copy(update=update)
    return DeepSeekModelRoles(
        llm=llm,
        request_interpreter=DeepSeekRequestInterpreter(llm),
        result_aggregator=DeepSeekResultAggregator(llm),
        direct_responder=DeepSeekDirectResponder(llm),
        listing_copywriter=DeepSeekListingCopywriter(llm),
        advertising_react_model=build_deepseek_react_model(
            env_file=env_file, model=model, reasoning_effort=reasoning_effort
        ),
    )

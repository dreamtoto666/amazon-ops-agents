from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .interfaces import (
    DeepSeekDirectResponder,
    DeepSeekRequestInterpreter,
    DeepSeekResultAggregator,
)
from .listing.copywriter import DeepSeekListingCopywriter
from .llm import DeepSeekStructuredLLM, build_deepseek_llm


@dataclass(frozen=True)
class DeepSeekModelRoles:
    """One provider instance shared by every model-backed role."""

    llm: DeepSeekStructuredLLM
    request_interpreter: DeepSeekRequestInterpreter
    result_aggregator: DeepSeekResultAggregator
    direct_responder: DeepSeekDirectResponder
    listing_copywriter: DeepSeekListingCopywriter


def build_deepseek_model_roles(
    *, env_file: str | Path = ".env", trust_env: bool = False
) -> DeepSeekModelRoles:
    llm = build_deepseek_llm(env_file=env_file, trust_env=trust_env)
    return DeepSeekModelRoles(
        llm=llm,
        request_interpreter=DeepSeekRequestInterpreter(llm),
        result_aggregator=DeepSeekResultAggregator(llm),
        direct_responder=DeepSeekDirectResponder(llm),
        listing_copywriter=DeepSeekListingCopywriter(llm),
    )

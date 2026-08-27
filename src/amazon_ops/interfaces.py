from __future__ import annotations

from typing import Protocol

from .llm import StructuredLLM
from .models import AgentTask, FinalResponse, QueryScope, SpecialistResult, UnderstandRequestResult
from .prompts import (
    DIRECT_RESPONDER_SYSTEM_PROMPT,
    REQUEST_INTERPRETER_SYSTEM_PROMPT,
    RESULT_AGGREGATOR_SYSTEM_PROMPT,
    build_aggregation_context,
    build_direct_response_context,
    build_request_context,
)


class RequestInterpreter(Protocol):
    def invoke(self, state: dict) -> UnderstandRequestResult:
        """Convert the conversation into a validated operational request."""


class SpecialistAgent(Protocol):
    name: str

    def invoke(self, task: AgentTask, scope: QueryScope, state: dict) -> SpecialistResult:
        """Execute one bounded read-only business investigation."""


class ResultAggregator(Protocol):
    def invoke(self, state: dict) -> FinalResponse:
        """Merge specialist evidence without turning hypotheses into facts."""


class DirectResponder(Protocol):
    def invoke(self, state: dict) -> FinalResponse:
        """Answer requests that do not require live operational data."""


class DeepSeekRequestInterpreter:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> UnderstandRequestResult:
        return self.llm.complete(
            system_prompt=REQUEST_INTERPRETER_SYSTEM_PROMPT,
            context=build_request_context(state),
            output_model=UnderstandRequestResult,
            max_tokens=3000,
        )


class DeepSeekResultAggregator:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> FinalResponse:
        return self.llm.complete(
            system_prompt=RESULT_AGGREGATOR_SYSTEM_PROMPT,
            context=build_aggregation_context(state),
            output_model=FinalResponse,
            max_tokens=5000,
        )


class DeepSeekDirectResponder:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> FinalResponse:
        return self.llm.complete(
            system_prompt=DIRECT_RESPONDER_SYSTEM_PROMPT,
            context=build_direct_response_context(state),
            output_model=FinalResponse,
            max_tokens=3000,
        )


class DeterministicAggregator:
    """Safe baseline aggregator; replace with an LLM implementation later."""

    def invoke(self, state: dict) -> FinalResponse:
        results = [SpecialistResult.model_validate(item) for item in state.get("specialist_results", [])]
        findings = [finding for result in results for finding in result.findings]
        hypotheses = [hypothesis for finding in findings for hypothesis in finding.hypotheses]
        actions = [action for result in results for action in result.recommended_actions]

        summaries = [result.summary for result in results if result.summary]
        answer = "\n".join(summaries) if summaries else "没有获得可用的专家分析结果。"
        return FinalResponse(
            answer=answer,
            confirmed_findings=findings,
            open_hypotheses=hypotheses,
            recommended_actions=actions,
        )

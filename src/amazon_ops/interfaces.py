from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from .events import get_stage_reporter
from .llm import MAX_COMPLETION_TOKENS, DeepSeekStructuredLLM, StructuredLLM
from .models import AgentTask, CompetitorAdvertisingReport, CompetitorReportSection, FinalResponse, QueryScope, SpecialistResult, UnderstandRequestResult
from .prompts import (
    DIRECT_RESPONDER_SYSTEM_PROMPT,
    REQUEST_INTERPRETER_SYSTEM_PROMPT,
    RESULT_AGGREGATOR_SYSTEM_PROMPT,
    COMPETITOR_REPORT_SYSTEM_PROMPT,
    build_aggregation_context,
    build_competitor_report_context,
    build_competitor_report_section_context,
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


class CompetitorReportWriter(Protocol):
    def invoke(self, state: dict) -> CompetitorAdvertisingReport:
        """Write an evidence-bounded competitor report from completed specialist results."""

    def invoke_section(self, state: dict, section_key: str) -> CompetitorReportSection:
        """Write one evidence-bounded report section."""


class DeepSeekRequestInterpreter:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> UnderstandRequestResult:
        reporter = get_stage_reporter(state)
        kwargs: dict[str, Any] = {}
        if reporter is not None and isinstance(self.llm, DeepSeekStructuredLLM):
            kwargs["thinking"] = True
            kwargs["on_reasoning_delta"] = lambda text: reporter.emit(
                "reasoning.delta", text=text
            )
        if isinstance(self.llm, DeepSeekStructuredLLM):
            images = state.get("image_attachments", [])
            if isinstance(images, list) and all(isinstance(item, str) for item in images):
                kwargs["image_data_urls"] = images
        return self.llm.complete(
            system_prompt=REQUEST_INTERPRETER_SYSTEM_PROMPT,
            context=build_request_context(state),
            output_model=UnderstandRequestResult,
            max_tokens=3000,
            **kwargs,
        )


class DeepSeekResultAggregator:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> FinalResponse:
        reporter = get_stage_reporter(state)
        kwargs: dict[str, Any] = {}
        if reporter is not None and isinstance(self.llm, DeepSeekStructuredLLM):
            kwargs["thinking"] = True
            kwargs["on_reasoning_delta"] = lambda text: reporter.emit(
                "reasoning.delta", text=text
            )
        return self.llm.complete(
            system_prompt=RESULT_AGGREGATOR_SYSTEM_PROMPT,
            context=build_aggregation_context(state),
            output_model=FinalResponse,
            max_tokens=5000,
            **kwargs,
        )


class DeepSeekCompetitorReportWriter:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> CompetitorAdvertisingReport:
        reporter = get_stage_reporter(state)
        kwargs: dict[str, Any] = {}
        if reporter is not None and isinstance(self.llm, DeepSeekStructuredLLM):
            kwargs["thinking"] = True
            kwargs["on_reasoning_delta"] = lambda text: reporter.emit("reasoning.delta", text=text)
        return self.llm.complete(
            system_prompt=COMPETITOR_REPORT_SYSTEM_PROMPT,
            context=build_competitor_report_context(state),
            output_model=CompetitorAdvertisingReport,
            max_tokens=MAX_COMPLETION_TOKENS,
            **kwargs,
        )

    def invoke_section(self, state: dict, section_key: str) -> CompetitorReportSection:
        reporter = get_stage_reporter(state)
        kwargs: dict[str, Any] = {}
        if reporter is not None and isinstance(self.llm, DeepSeekStructuredLLM):
            kwargs["thinking"] = True
            kwargs["on_reasoning_delta"] = lambda text: reporter.emit(
                "reasoning.delta", text=text, section_key=section_key
            )
        return self.llm.complete(
            system_prompt=(
                COMPETITOR_REPORT_SYSTEM_PROMPT
                + f"\n\n本次只生成 section key={section_key} 的单个 CompetitorReportSection JSON。"
                + " key 必须与指定值完全一致，不要生成标题、其他章节或报告级字段。"
            ),
            context=build_competitor_report_section_context(state, section_key),
            output_model=CompetitorReportSection,
            max_tokens=MAX_COMPLETION_TOKENS,
            **kwargs,
        )


class DeepSeekDirectResponder:
    def __init__(self, llm: StructuredLLM) -> None:
        self.llm = llm

    def invoke(self, state: dict) -> FinalResponse:
        reporter = get_stage_reporter(state)
        kwargs: dict[str, Any] = {}
        if reporter is not None and isinstance(self.llm, DeepSeekStructuredLLM):
            kwargs["thinking"] = True
            kwargs["on_reasoning_delta"] = lambda text: reporter.emit(
                "reasoning.delta", text=text
            )
        if isinstance(self.llm, DeepSeekStructuredLLM):
            images = state.get("image_attachments", [])
            if isinstance(images, list) and all(isinstance(item, str) for item in images):
                kwargs["image_data_urls"] = images
        return self.llm.complete(
            system_prompt=DIRECT_RESPONDER_SYSTEM_PROMPT,
            context=build_direct_response_context(state),
            output_model=FinalResponse,
            max_tokens=3000,
            **kwargs,
        )

    def stream(
        self,
        state: dict,
        on_delta: Callable[[str], None],
        on_reasoning_delta: Callable[[str], None] | None = None,
    ) -> FinalResponse:
        """Generate only the user-facing answer text as a true provider stream."""
        if not isinstance(self.llm, DeepSeekStructuredLLM):
            return self.invoke(state)
        images = state.get("image_attachments", [])
        answer = self.llm.stream_text(
            system_prompt=(
                DIRECT_RESPONDER_SYSTEM_PROMPT
                + "\n\n只输出面向用户的回答正文，不要 JSON、Markdown 代码块或思考过程。"
            ),
            context=build_direct_response_context(state),
            on_delta=on_delta,
            on_reasoning_delta=on_reasoning_delta,
            max_tokens=3000,
            thinking=True,
            image_data_urls=(
                images if isinstance(images, list) and all(isinstance(item, str) for item in images) else None
            ),
        )
        return FinalResponse(answer=answer)


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

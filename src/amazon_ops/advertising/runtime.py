from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import Event, RLock, Thread
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from amazon_ops.llm import StructuredLLM, build_deepseek_llm
from amazon_ops.events import InMemoryEventHub, StageEventType, StageName
from amazon_ops.listing.mcp import mcp_trace_context

from .agents import (
    ApprovalRequiredReviewTodoAgent,
)
from .gateway import AdvertisingDataGateway, AdvertisingReport, LingxingAdvertisingGateway
from amazon_ops.listing.mcp import MCPToolResult
from .graph import AdvertisingDiagnosticServices, build_advertising_diagnostic_graph
from .history import AdvertisingRunHistoryStore, InMemoryAdvertisingRunHistoryStore
from .llm_agents import (
    DeepSeekDataInspectionAgent,
    DeepSeekProblemAttributionAgent,
    DeepSeekStrategyRecommendationAgent,
)
from .models import (
    AdDiagnosticRequest,
    AdDiagnosticResult,
    AdShop,
    AttributionResult,
    DataInspectionResult,
    ReviewTodoResult,
    StrategyResult,
)


class AdvertisingRunRecord(BaseModel):
    run_id: str
    trace_id: str
    span_id: str
    stage: str
    status: str
    result: AdDiagnosticResult | None = None
    error: dict[str, Any] | None = None
    detail_call_quotas: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    recovery_count: int = 0
    last_heartbeat_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None


class _LedgerGateway:
    """Records every diagnostic read and reuses completed immutable responses."""
    def __init__(self, delegate: AdvertisingDataGateway, manager: "AdvertisingRunManager", run_id: str, state: dict[str, Any]) -> None:
        self.delegate, self.manager, self.run_id, self.state = delegate, manager, run_id, state

    def list_shops(self):
        return self.delegate.list_shops()

    def campaign_report(self, **kwargs):
        return self._call("campaign_report", kwargs, lambda: self.delegate.campaign_report(**kwargs))

    def attribution_report(self, **kwargs):
        return self._call(kwargs["tool"], kwargs, lambda: self.delegate.attribution_report(**kwargs))

    def _call(self, tool: str, arguments: dict[str, Any], invoke):
        # Gateway methods accept Pydantic request models (notably
        # DiagnosticPeriod), while the durable ledger is JSONB. Convert at
        # this boundary rather than leaking Python objects into persistence.
        normalized_arguments = self._json_value(arguments)
        normalized = json.dumps(normalized_arguments, sort_keys=True, separators=(",", ":"))
        logical_key = hashlib.sha256(f"{self.state.get('stage')}|{self.state.get('attribution_round', 0)}|{tool}|{normalized}".encode()).hexdigest()
        entry = self.manager.history_store.reserve_call(self.run_id, logical_key, {
            "call_id": f"read-{uuid4().hex}", "stage": self.state.get("stage", "queued"),
            "attribution_round": self.state.get("attribution_round", 0), "tool": tool, "request": normalized_arguments,
        })
        if entry.get("status") == "succeeded" and entry.get("result"):
            result = entry["result"]
            return AdvertisingReport(tool=result["tool"], arguments=result["arguments"], rows=result["rows"], total=result["total"], trace=MCPToolResult.model_validate(result["trace"]))
        report = invoke()
        self.manager.history_store.complete_call(entry["call_id"], {
            "tool": report.tool, "arguments": report.arguments, "rows": report.rows,
            "total": report.total, "trace": report.trace.model_dump(mode="json"),
        })
        return report

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if isinstance(value, BaseModel):
            return cls._json_value(value.model_dump(mode="json"))
        if isinstance(value, dict):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        # Dates, decimals and enums used in request models are all handled by
        # Pydantic above; this fallback preserves ledger availability for any
        # future adapter-specific scalar without serialising arbitrary objects.
        try:
            json.dumps(value)
        except TypeError:
            return str(value)
        return value


class _EventedInspector:
    def __init__(self, delegate: DeepSeekDataInspectionAgent, manager: "AdvertisingRunManager", run_id: str) -> None:
        self.delegate = delegate
        self.manager = manager
        self.run_id = run_id

    def invoke(self, request, state) -> DataInspectionResult:
        return self.manager._run_stage(
            self.run_id,
            StageName.DATA_INSPECTION,
            self.delegate.invoke,
            request,
            state,
            summary=lambda result: {
                "anomaly_count": len(result.anomalies),
                "hypothesis_count": len(result.hypotheses),
                "record_count": len(result.snapshots),
                "summary": (
                    f"完成数据巡检，发现 {len(result.anomalies)} 个异常，"
                    f"生成 {len(result.hypotheses)} 条候选假设。"
                ),
            },
        )


class _EventedAttribution:
    def __init__(
        self,
        delegate: DeepSeekProblemAttributionAgent,
        manager: "AdvertisingRunManager",
        run_id: str,
    ) -> None:
        self.delegate = delegate
        self.manager = manager
        self.run_id = run_id

    def invoke(self, request, state) -> AttributionResult:
        return self.manager._run_stage(
            self.run_id,
            StageName.PROBLEM_ATTRIBUTION,
            self.delegate.invoke,
            request,
            state,
            summary=lambda result: {
                "cause_count": len(result.causes),
                "round": state.get("attribution_round", 0) + 1,
                "detail_call_quotas": [item.model_dump(mode="json") for item in result.detail_call_quotas],
                "summary": f"完成问题归因，形成 {len(result.causes)} 条原因判断。",
            },
        )


class _EventedStrategist:
    def __init__(
        self,
        delegate: DeepSeekStrategyRecommendationAgent,
        manager: "AdvertisingRunManager",
        run_id: str,
    ) -> None:
        self.delegate = delegate
        self.manager = manager
        self.run_id = run_id

    def invoke(self, request, state) -> StrategyResult:
        return self.manager._run_stage(
            self.run_id,
            StageName.STRATEGY_RECOMMENDATION,
            self.delegate.invoke,
            request,
            state,
            summary=lambda result: {
                "recommendation_count": len(result.recommendations),
                "summary": f"形成 {len(result.recommendations)} 条策略建议。",
            },
        )


class _EventedReviewer:
    def __init__(
        self,
        delegate: ApprovalRequiredReviewTodoAgent,
        manager: "AdvertisingRunManager",
        run_id: str,
    ) -> None:
        self.delegate = delegate
        self.manager = manager
        self.run_id = run_id

    def invoke(self, request, state) -> ReviewTodoResult:
        return self.manager._run_stage(
            self.run_id,
            StageName.REVIEW_TODO,
            self.delegate.invoke,
            request,
            state,
            summary=lambda result: {
                "todo_count": len(result.todos),
                "rejected_count": len(result.rejected_recommendation_ids),
                "summary": f"复核完成，生成 {len(result.todos)} 个待审批代办。",
            },
        )


class AdvertisingRunManager:
    """Runs the fixed four-agent workflow outside the chat controller."""

    def __init__(
        self,
        *,
        gateway: AdvertisingDataGateway | None = None,
        llm: StructuredLLM | None = None,
        history_store: AdvertisingRunHistoryStore | None = None,
        max_workers: int = 2,
    ) -> None:
        self.gateway = gateway or LingxingAdvertisingGateway()
        self.llm = llm or build_deepseek_llm()
        self.history_store = history_store or InMemoryAdvertisingRunHistoryStore()
        self.hub = InMemoryEventHub()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="advertising-diagnostic"
        )
        self._lock = RLock()
        self._runs: dict[str, AdvertisingRunRecord] = {}
        self._owners: dict[str, str | None] = {}
        self._recovery_stop = Event()
        self._recovery_thread: Thread | None = None

    def list_shops(self) -> list[AdShop]:
        return self.gateway.list_shops()

    def submit(self, request: AdDiagnosticRequest, *, owner_id: str | None = None) -> str:
        run_id = f"ad-run-{uuid4().hex}"
        trace_id = f"trace-{uuid4().hex}"
        root_span_id = f"span-{uuid4().hex}"
        with self._lock:
            record = AdvertisingRunRecord(
                run_id=run_id,
                trace_id=trace_id,
                span_id=root_span_id,
                stage="queued",
                status="running",
                last_heartbeat_at=datetime.now(timezone.utc),
            )
            self._runs[run_id] = record
            self._owners[run_id] = owner_id
        self.history_store.save(record.model_dump(mode="json"), request.model_dump(mode="json"), owner_id)
        self._executor.submit(self._execute, run_id, trace_id, root_span_id, request, None, None)
        return run_id

    def get(self, run_id: str, *, owner_id: str | None = None) -> AdvertisingRunRecord | None:
        with self._lock:
            record = self._runs.get(run_id)
            owner = self._owners.get(run_id)
        if record is not None and owner == owner_id:
            return record.model_copy(deep=True)
        stored = self.history_store.get(run_id, owner_id)
        return AdvertisingRunRecord.model_validate(stored) if stored else None

    def list_history(self, limit: int = 20, *, owner_id: str | None = None) -> list[AdvertisingRunRecord]:
        return [AdvertisingRunRecord.model_validate(item) for item in self.history_store.list(limit, owner_id)]

    def delete_history(self, run_id: str, *, owner_id: str | None = None) -> bool:
        """Delete a completed history record owned by the requesting user."""
        with self._lock:
            record = self._runs.get(run_id)
            if record is not None and self._owners.get(run_id) == owner_id:
                if record.status == "running":
                    raise ValueError("running advertising diagnostic cannot be deleted")
        deleted = self.history_store.delete(run_id, owner_id)
        if deleted:
            with self._lock:
                if self._owners.get(run_id) == owner_id:
                    self._runs.pop(run_id, None)
                    self._owners.pop(run_id, None)
        return deleted

    def _execute(
        self,
        run_id: str,
        trace_id: str,
        root_span_id: str,
        request: AdDiagnosticRequest,
        restored_state: dict[str, Any] | None = None,
        lease_owner: str | None = None,
    ) -> None:
        state: dict[str, Any] = {
            "request_id": run_id,
            "run_id": run_id,
            "trace_id": trace_id,
            "span_id": root_span_id,
            "stage": "queued",
            "request": request.model_dump(mode="json"),
        }
        if restored_state:
            state.update(restored_state)
            state["run_id"] = run_id
            state["trace_id"] = trace_id
            state["request"] = request.model_dump(mode="json")
        if lease_owner:
            self.history_store.heartbeat(run_id, lease_owner=lease_owner)
        self.hub.emit(
            run_id,
            StageEventType.RUN_STARTED,
            trace_id=trace_id,
            span_id=root_span_id,
            data={
                "workflow": "advertising_diagnostic",
                "llm_provider": "deepseek",
                "agents": [
                    "data_inspection",
                    "problem_attribution",
                    "strategy_recommendation",
                    "review_todo",
                ],
            },
        )
        ledger_gateway = _LedgerGateway(self.gateway, self, run_id, state)
        services = AdvertisingDiagnosticServices(
            inspector=_EventedInspector(DeepSeekDataInspectionAgent(ledger_gateway, self.llm), self, run_id),
            attribution=_EventedAttribution(
                DeepSeekProblemAttributionAgent(ledger_gateway, self.llm), self, run_id
            ),
            strategist=_EventedStrategist(
                DeepSeekStrategyRecommendationAgent(self.llm), self, run_id
            ),
            reviewer=_EventedReviewer(ApprovalRequiredReviewTodoAgent(), self, run_id),
            checkpoint=lambda current, key, update: self._checkpoint(run_id, request, current, key, update, lease_owner),
        )
        try:
            graph = build_advertising_diagnostic_graph(services=services)
            with mcp_trace_context(run_id=run_id, trace_id=trace_id):
                output = graph.invoke(
                    state,
                    config={
                        "run_name": "amazon_ops.advertising_diagnostic",
                        "tags": ["amazon-ops", "advertising-diagnostic", request.trigger],
                        "metadata": {
                            "workflow": "advertising_diagnostic",
                            "trace_id": trace_id,
                            "run_id": run_id,
                            "profile_ids": request.profile_ids,
                            "goal": request.goal.growth_priority,
                            "baseline_comparison": bool(request.baseline_period),
                        },
                    },
                )
            result = AdDiagnosticResult.model_validate(output["final_result"])
            record = AdvertisingRunRecord(
                run_id=run_id,
                trace_id=result.trace_id,
                span_id=result.span_id,
                stage=result.stage,
                status="completed",
                result=result,
                detail_call_quotas=output.get("detail_call_quotas", []),
            )
            self.hub.emit(
                run_id,
                StageEventType.RUN_COMPLETED,
                trace_id=result.trace_id,
                span_id=result.span_id,
                stage=StageName(result.stage),
                data={"result": result.model_dump(mode="json")},
            )
        except Exception as exc:
            message = self._safe_error_message(exc)
            record = AdvertisingRunRecord(
                run_id=run_id,
                trace_id=trace_id,
                span_id=str(state["span_id"]),
                stage=str(state["stage"]),
                status="failed",
                error={
                    "code": getattr(exc, "code", "AD_DIAGNOSTIC_FAILED"),
                    "message": message,
                },
            )
            self.hub.emit(
                run_id,
                StageEventType.RUN_FAILED,
                trace_id=trace_id,
                span_id=str(state["span_id"]),
                stage=self._stage_from_state(state),
                data={"error": message},
            )
        with self._lock:
            self._runs[run_id] = record
        with self._lock:
            owner_id = self._owners.get(run_id)
        self.history_store.save(record.model_dump(mode="json"), request.model_dump(mode="json"), owner_id)

    def _checkpoint(self, run_id: str, request: AdDiagnosticRequest, state: dict[str, Any], key: str, update: dict[str, Any], lease_owner: str | None) -> None:
        """Persist only validated graph state before advertising the stage as complete."""
        checkpoint_state = {**state, **update}
        checkpoint_state.pop("resume_next", None)
        self.history_store.save_checkpoint(run_id, key, checkpoint_state)
        if lease_owner:
            self.history_store.heartbeat(run_id, lease_owner=lease_owner)
        with self._lock:
            current = self._runs.get(run_id)
            owner_id = self._owners.get(run_id)
            if current:
                current.stage = update.get("stage", state.get("stage", key))
                current.last_heartbeat_at = datetime.now(timezone.utc)
                self.history_store.save(current.model_dump(mode="json"), request.model_dump(mode="json"), owner_id)

    def recover_interrupted_runs(self, *, stale_after: timedelta = timedelta(minutes=30)) -> list[str]:
        """Claim stale durable runs and resume them from their newest checkpoint."""
        stale_before = datetime.now(timezone.utc) - stale_after
        recovered: list[str] = []
        for candidate in self.history_store.recoverable_runs(stale_before=stale_before):
            run_id = candidate["run_id"]
            lease_owner = f"recovery-{uuid4().hex}"
            claimed = self.history_store.reserve_recovery(run_id, lease_owner=lease_owner, stale_before=stale_before)
            if not claimed:
                continue
            request = AdDiagnosticRequest.model_validate(claimed["request"])
            checkpoint = self.history_store.latest_checkpoint(run_id)
            state = dict(checkpoint["state"]) if checkpoint else {}
            state["resume_next"] = self._next_node(checkpoint["checkpoint_key"] if checkpoint else None, state)
            record = AdvertisingRunRecord.model_validate(claimed["record"])
            record.last_heartbeat_at = datetime.now(timezone.utc)
            record.lease_owner = lease_owner
            with self._lock:
                self._runs[run_id] = record
            self._executor.submit(self._execute, run_id, record.trace_id, record.span_id, request, state, lease_owner)
            recovered.append(run_id)
        return recovered

    def start_recovery_monitor(self, *, interval: timedelta = timedelta(minutes=5)) -> None:
        """Start the durable stale-run scanner once per API process."""
        if self._recovery_thread and self._recovery_thread.is_alive():
            return
        self.recover_interrupted_runs()
        self._recovery_stop.clear()
        def monitor() -> None:
            while not self._recovery_stop.wait(interval.total_seconds()):
                self.recover_interrupted_runs()
        self._recovery_thread = Thread(target=monitor, name="advertising-recovery", daemon=True)
        self._recovery_thread.start()

    def close(self) -> None:
        self._recovery_stop.set()
        self._executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _next_node(checkpoint_key: str | None, state: dict[str, Any]) -> str:
        if checkpoint_key is None:
            return "inspect_data"
        if checkpoint_key == "data_inspection":
            inspection = DataInspectionResult.model_validate(state.get("inspection", {}))
            return "attribute_problem" if inspection.anomalies else "review_and_create_todos"
        if checkpoint_key.startswith("problem_attribution:"):
            attribution = AttributionResult.model_validate(state.get("attribution", {}))
            return "attribute_problem" if attribution.needs_more_evidence and state.get("attribution_round", 0) < 2 else "recommend_strategy"
        if checkpoint_key == "strategy_recommendation":
            return "review_and_create_todos"
        return "review_and_create_todos"

    def _run_stage(
        self,
        run_id: str,
        stage: StageName,
        operation,
        request,
        state,
        *,
        summary,
    ):
        trace_id = str(state["trace_id"])
        span_id = f"span-{uuid4().hex}"
        state["span_id"] = span_id
        state["stage"] = stage.value
        # A database heartbeat is independent from the browser SSE heartbeat.
        # It makes long-running external calls safe to recover after a process loss.
        self.hub.emit(
            run_id,
            StageEventType.STAGE_STARTED,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
        )
        try:
            result = operation(request, state)
        except Exception as exc:
            self.hub.emit(
                run_id,
                StageEventType.STAGE_FAILED,
                trace_id=trace_id,
                span_id=span_id,
                stage=stage,
                data={"error": self._safe_error_message(exc)},
            )
            raise
        self.hub.emit(
            run_id,
            StageEventType.STAGE_COMPLETED,
            trace_id=trace_id,
            span_id=span_id,
            stage=stage,
            data=summary(result),
        )
        return result

    @staticmethod
    def _stage_from_state(state: dict[str, Any]) -> StageName | None:
        raw_stage = state.get("stage")
        try:
            return StageName(raw_stage) if raw_stage else None
        except ValueError:
            return None

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if code == "MCP_SECRET_MISSING":
            return "领星 MCP 密钥尚未配置。"
        if code in {"MCP_TRANSPORT_FAILURE", "MCP_TEMPORARY_FAILURE"}:
            return "领星 MCP 暂时无法连接，请稍后重试。"
        if code in {"MCP_TOOL_CALL_FAILED", "MCP_GATEWAY_ERROR"}:
            return "领星广告数据查询失败。"
        if code == "DEEPSEEK_API_KEY_MISSING":
            return "DeepSeek API Key 尚未配置，无法进行广告归因。"
        if code == "DEEPSEEK_AUTH_FAILED":
            return "DeepSeek API Key 无效或无权限。"
        if code in {
            "DEEPSEEK_CONNECTION_FAILED",
            "DEEPSEEK_RATE_LIMITED",
            "DEEPSEEK_OUTPUT_TRUNCATED",
        }:
            return "DeepSeek 暂时无法完成广告分析，请稍后重试。"
        if isinstance(code, str) and code.startswith("DEEPSEEK_INVALID_AD"):
            return "DeepSeek 返回的广告结论未通过证据校验。"
        if isinstance(exc, RuntimeError) and str(exc).startswith("领星"):
            return str(exc)
        return "广告巡检运行失败，请查看服务端日志。"

"""Chat specialist that routes comparison requests to local business MCP tools."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from .competitor_research_catalog import CAPABILITY_BY_TOOL, OVERVIEW_TOOLS
from .competitor_research_mcp_client import CompetitorResearchMCPClient
from .competitor_router import CompetitorResearchRouter, validate_competitor_research_plan
from .events import get_stage_reporter
from .models import AgentTask, DataArtifact, Finding, QueryScope, SpecialistName, SpecialistResult
from .prompts import COMPETITOR_SPECIALIST_SYSTEM_PROMPT


class CompetitorAdvertisingSpecialist:
    """竞品专家：按系统提示词约束调用本地只读 MCP。"""

    name = SpecialistName.COMPETITOR_ADVERTISING.value
    system_prompt = COMPETITOR_SPECIALIST_SYSTEM_PROMPT

    def __init__(
        self,
        client: CompetitorResearchMCPClient | None = None,
        router: CompetitorResearchRouter | None = None,
    ) -> None:
        self._client = client or CompetitorResearchMCPClient()
        self._router = router

    @classmethod
    def production(cls, router: CompetitorResearchRouter) -> "CompetitorAdvertisingSpecialist":
        return cls(router=router)

    def invoke(self, task: AgentTask, scope: QueryScope, state: dict[str, Any]) -> SpecialistResult:
        own_asin, competitors, country = scope.own_asin, list(dict.fromkeys(scope.competitor_asins)), self._country(scope)
        if not own_asin or not competitors:
            return self._needs_input(task, "请明确提供自有 ASIN 和至少一个竞品 ASIN，才能进行竞品对标。")
        if len(competitors) > 5:
            return self._needs_input(task, "一次最多对比 5 个竞品，请缩小竞品范围后重试。")
        if not country:
            return self._needs_input(task, "请提供要对比的站点（例如 US）。")

        if self._router:
            planned = self._router.plan(task, scope)
            if planned:
                batches = validate_competitor_research_plan(planned, scope)
                if batches:
                    return self._planned_result(task, batches, scope, country, state)
        return self._legacy_result(task, scope, country, state)

    def _legacy_result(self, task: AgentTask, scope: QueryScope, country: str, state: dict[str, Any]) -> SpecialistResult:
        """Compatibility fallback when the model planner is unavailable or invalid.

        It builds the same batch shape the planner would and runs it through the
        same executor, so events, drilldown gating and ASIN handling cannot drift
        between the planned and fallback paths.  A missing identifier asks for it
        instead of silently degrading to a full overview sweep.
        """

        objective = task.objective.casefold()
        if any(term in objective for term in ("广告组", "广告词")):
            if not scope.campaign_ids or not scope.ad_group_ids:
                return self._needs_input(task, "请提供已确认的 Campaign ID 和广告组 ID，才能查看广告组或广告词。")
            batches = [["inspect_ad_group"]]
        elif any(term in objective for term in ("campaign", "广告活动")):
            if not scope.campaign_ids:
                return self._needs_input(task, "请提供已确认的 Campaign ID，才能拆解 Campaign。")
            batches = [["inspect_campaign"]]
        elif any(term in objective for term in ("运营时光机", "复盘", "历史")):
            batches = [["replay_operations_history"]]
        elif any(term in objective for term in ("推荐专栏", "推荐流量")):
            batches = [["analyze_recommendation_traffic"]]
        elif any(term in objective for term in ("广告架构", "广告结构")):
            batches = [["inspect_ad_architecture"]]
        else:
            batches = [list(OVERVIEW_TOOLS)]
        return self._planned_result(task, batches, scope, country, state)

    @staticmethod
    def _country(scope: QueryScope) -> str | None:
        return scope.marketplaces[0] if scope.marketplaces else None

    def _planned_result(
        self,
        task: AgentTask,
        batches: list[list[str]],
        scope: QueryScope,
        country: str,
        state: dict[str, Any],
    ) -> SpecialistResult:
        """Execute only validated plan batches; arguments always come from scope."""
        reporter = get_stage_reporter(state)
        results: list[tuple[str, dict[str, Any]]] = []
        for batch in batches:
            with ThreadPoolExecutor(max_workers=max(1, len(batch))) as executor:
                futures = {
                    executor.submit(self._planned_tool_results, tool, scope, country, reporter): tool
                    for tool in batch
                }
                completed: dict[str, list[tuple[str, dict[str, Any]]]] = {}
                for future in as_completed(futures):
                    completed[futures[future]] = future.result()
            # Concurrent completion order is nondeterministic, so replay each
            # batch in plan order: identical inputs must produce an identical
            # evidence list, prompt, and report on every run.
            for tool in batch:
                results.extend(completed[tool])
        return self._with_observation_window(
            self._as_specialist_result(task, results),
            scope,
            results,
        )

    @staticmethod
    def _window_arguments(scope: QueryScope, tool: str) -> dict[str, str]:
        """Carry the confirmed period only where Sif can actually accept one."""

        if scope.period is None or CAPABILITY_BY_TOOL[tool].observation_window == "provider_default":
            return {}
        return {
            "period_start": scope.period.current.start.isoformat(),
            "period_end": scope.period.current.end.isoformat(),
        }

    @staticmethod
    def _observation_window(
        scope: QueryScope, results: list[tuple[str, dict[str, Any]]]
    ) -> dict[str, Any]:
        """Aggregate evidence-level window contracts without overstating alignment."""

        requested = scope.period.current if scope.period else None
        by_tool: dict[str, str] = {}
        has_unmatured_data = False
        # An unavailable or legacy result without evidence metadata is treated as
        # unaligned.  This is deliberately conservative for future report use.
        rank = {"fully_aligned": 0, "partially_aligned": 1, "unaligned": 2}
        for tool, result in results:
            window = result.get("observation_window") if isinstance(result, dict) else None
            alignment = window.get("alignment") if isinstance(window, dict) else "unaligned"
            if alignment not in rank:
                alignment = "unaligned"
            existing = by_tool.get(tool)
            if existing is None or rank[alignment] > rank[existing]:
                by_tool[tool] = alignment
            if isinstance(window, dict) and window.get("maturity") == "UNMATURED":
                has_unmatured_data = True
        return {
            "source": "requested_period" if requested else "service_default",
            "requested": (
                {"start": requested.start.isoformat(), "end": requested.end.isoformat()}
                if requested
                else None
            ),
            "baseline_requested": bool(scope.period and scope.period.baseline),
            "baseline_status": "not_queried" if scope.period and scope.period.baseline else "not_requested",
            "maturity": "UNMATURED" if has_unmatured_data else "COMPLETE",
            "fully_aligned": [tool for tool, alignment in by_tool.items() if alignment == "fully_aligned"],
            "partially_aligned": [tool for tool, alignment in by_tool.items() if alignment == "partially_aligned"],
            "unaligned": [tool for tool, alignment in by_tool.items() if alignment == "unaligned"],
        }

    def _with_observation_window(
        self,
        result: SpecialistResult,
        scope: QueryScope,
        results: list[tuple[str, dict[str, Any]]],
    ) -> SpecialistResult:
        coverage = self._observation_window(scope, results)
        requested = coverage["requested"]
        limitations: list[str] = []
        if requested and (coverage["partially_aligned"] or coverage["unaligned"]):
            limitations.append("部分来源的观察窗口由 Sif 决定或仅支持结束日，未与请求周期完整对齐")
        if coverage["maturity"] == "UNMATURED":
            limitations.append("包含当天数据，归因和流量结果可能继续回补，不能用于确定趋势或触发重操作")
        if result.status == "completed" and limitations:
            result = result.model_copy(
                update={
                    "summary": (
                        f"{result.summary}（请求周期 {requested['start']} 至 {requested['end']}；"
                        f"{'；'.join(limitations)}）"
                    )
                }
            )
        deliverables = [dict(item) for item in result.deliverables]
        if deliverables:
            deliverables[0]["observation_window"] = coverage
        else:
            deliverables = [{"type": "competitor_research", "tools": [], "results": [], "observation_window": coverage}]
        return result.model_copy(update={"deliverables": deliverables})

    def _planned_tool_results(
        self, tool: str, scope: QueryScope, country: str, reporter: Any
    ) -> list[tuple[str, dict[str, Any]]]:
        window = self._window_arguments(scope, tool)
        if tool == "inspect_campaign":
            asin = scope.own_asin or scope.competitor_asins[0]
            return [
                (tool, self._call(tool, {"asin": asin, "campaign_id": campaign_id, "country": country, **window}, reporter))
                for campaign_id in scope.campaign_ids
            ]
        if tool == "inspect_ad_group":
            asin = scope.own_asin or scope.competitor_asins[0]
            return [
                (tool, self._call(tool, {"asin": asin, "campaign_id": scope.campaign_ids[0], "ad_group_id": group_id, "country": country, **window}, reporter))
                for group_id in scope.ad_group_ids
            ]
        arguments = {
            "own_asin": scope.own_asin,
            "competitor_asins": list(dict.fromkeys(scope.competitor_asins)),
            "country": country,
            **window,
        }
        return [(tool, self._call(tool, arguments, reporter))]

    def _call(self, tool: str, arguments: dict[str, Any], reporter: Any) -> dict[str, Any]:
        if reporter:
            reporter.emit("tool.call.started", tool=tool, provider="competitor_research_mcp")
        result = self._client.call(tool, arguments)
        if reporter:
            reporter.emit("tool.call.completed" if result.get("ok") else "tool.call.failed", tool=tool, provider="competitor_research_mcp")
        return result

    def _as_specialist_result(self, task: AgentTask, results: list[tuple[str, dict[str, Any]]]) -> SpecialistResult:
        artifacts: list[DataArtifact] = []
        errors: list[dict[str, Any]] = []
        completed = 0
        for _, result in results:
            if result.get("ok"):
                completed += 1
            errors.extend(result.get("errors", []) if isinstance(result.get("errors"), list) else [])
            for item in result.get("evidence", []) if isinstance(result.get("evidence"), list) else []:
                if isinstance(item, dict) and isinstance(item.get("evidence_id"), str) and isinstance(item.get("tool"), str):
                    artifacts.append(DataArtifact(artifact_id=item["evidence_id"], source="sif", tool=item["tool"], query=item.get("query", {}), fetched_at=item.get("fetched_at")))
        if not completed:
            return SpecialistResult(task_id=task.task_id, agent=SpecialistName.COMPETITOR_ADVERTISING, status="unavailable", summary="竞品对标数据暂不可用，未读取到可验证的 Sif 数据。", errors=errors)
        names = "、".join(tool for tool, result in results if result.get("ok"))
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.COMPETITOR_ADVERTISING,
            status="degraded" if errors else "completed",
            summary=f"已完成竞品对标：{names}。结果仅基于 Sif 实际返回的公开可见研究数据。",
            findings=[Finding(finding="竞品私有花费、竞价、ACOS、ROAS、订单和 CVR 未被推断。", severity="info", confidence=1.0, evidence_refs=[item.artifact_id for item in artifacts])],
            artifacts=artifacts,
            deliverables=[{"type": "competitor_research", "tools": [tool for tool, _ in results], "results": [result for _, result in results]}], errors=errors,
        )

    @staticmethod
    def _needs_input(task: AgentTask, question: str) -> SpecialistResult:
        return SpecialistResult(task_id=task.task_id, agent=SpecialistName.COMPETITOR_ADVERTISING, status="needs_input", summary=question)

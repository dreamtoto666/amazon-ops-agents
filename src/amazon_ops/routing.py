from __future__ import annotations

from uuid import uuid4

from .models import Action, AgentTask, QueryScope, RequestRoute, RoutePlan, SpecialistName, SpecialistResult, UserIntent


def _task(agent: SpecialistName, objective: str, reason: str, priority: int = 1) -> AgentTask:
    return AgentTask(
        task_id=f"task-{uuid4().hex[:10]}",
        agent=agent,
        objective=objective,
        priority=priority,
        reason=reason,
    )


def build_initial_plan(intent: UserIntent, scope: QueryScope, normalized_request: str) -> RoutePlan:
    del scope
    if intent.primary_agent is None:
        return RoutePlan(route=RequestRoute.UNSUPPORTED, execution_mode="none")

    agents = list(dict.fromkeys([intent.primary_agent, *intent.supporting_agents]))
    max_rounds = 1

    if intent.action in {Action.DIAGNOSE, Action.RECOMMEND}:
        max_rounds = 2

    tasks = [
        _task(
            agent=agent,
            objective=normalized_request,
            reason=f"LLM 选择：{intent.domain.value}/{intent.action.value} → {agent.value}",
            priority=index + 1,
        )
        for index, agent in enumerate(agents)
    ]
    return RoutePlan(
        execution_mode="parallel" if len(tasks) > 1 else "sequential",
        tasks=tasks,
        max_rounds=max_rounds,
    )


def select_followup_tasks(
    *,
    intent: UserIntent,
    results: list[SpecialistResult],
    called_agents: set[SpecialistName],
    current_round: int,
    max_rounds: int,
) -> list[AgentTask]:
    if current_round >= max_rounds:
        return []
    if intent.action not in {Action.DIAGNOSE, Action.RECOMMEND}:
        return []

    followups: dict[SpecialistName, str] = {}
    for result in results:
        for finding in result.findings:
            for hypothesis in finding.hypotheses:
                agent = hypothesis.requires_agent
                if agent and agent not in called_agents and hypothesis.confidence >= 0.5:
                    followups.setdefault(agent, hypothesis.description)

    return [
        _task(
            agent=agent,
            objective=f"验证假设：{description}",
            reason="专家结果提出了需要验证的跨领域假设",
        )
        for agent, description in followups.items()
    ]

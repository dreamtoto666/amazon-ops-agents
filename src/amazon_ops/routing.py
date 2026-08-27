from __future__ import annotations

from uuid import uuid4

from .models import (
    Action,
    AgentTask,
    Domain,
    QueryScope,
    RequestRoute,
    RoutePlan,
    SpecialistName,
    SpecialistResult,
    UserIntent,
)


PRIMARY_ROUTES: dict[Domain, SpecialistName] = {
    Domain.STORE: SpecialistName.SALES_PROFIT,
    Domain.PRODUCT: SpecialistName.SALES_PROFIT,
    Domain.PROFIT: SpecialistName.SALES_PROFIT,
    Domain.ADVERTISING: SpecialistName.ADVERTISING,
    Domain.INVENTORY: SpecialistName.INVENTORY,
    Domain.KEYWORD: SpecialistName.MARKET_RISK,
    Domain.COMPETITOR: SpecialistName.MARKET_RISK,
    Domain.FOLLOW_SALE: SpecialistName.MARKET_RISK,
    Domain.LISTING: SpecialistName.LISTING_CONTENT,
    Domain.REPORT: SpecialistName.SALES_PROFIT,
}

DOMAIN_ROUTES = PRIMARY_ROUTES


def _task(agent: SpecialistName, objective: str, reason: str, priority: int = 1) -> AgentTask:
    return AgentTask(
        task_id=f"task-{uuid4().hex[:10]}",
        agent=agent,
        objective=objective,
        priority=priority,
        reason=reason,
    )


def build_initial_plan(intent: UserIntent, scope: QueryScope, normalized_request: str) -> RoutePlan:
    primary = PRIMARY_ROUTES.get(intent.domain)
    if primary is None:
        return RoutePlan(route=RequestRoute.UNSUPPORTED, execution_mode="none")

    agents: list[SpecialistName] = [primary]
    max_rounds = 1

    # A focused product diagnosis benefits from independent evidence gathered in parallel.
    if intent.domain == Domain.PRODUCT and intent.action == Action.DIAGNOSE and scope.has_product_target():
        agents = [
            SpecialistName.SALES_PROFIT,
            SpecialistName.ADVERTISING,
            SpecialistName.INVENTORY,
            SpecialistName.MARKET_RISK,
        ]
    elif intent.action in {Action.DIAGNOSE, Action.RECOMMEND}:
        max_rounds = 2

    for domain in intent.secondary_domains:
        agent = DOMAIN_ROUTES.get(domain)
        if agent and agent not in agents:
            agents.append(agent)

    tasks = [
        _task(
            agent=agent,
            objective=normalized_request,
            reason=f"{intent.domain.value}/{intent.action.value} 路由到 {agent.value}",
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

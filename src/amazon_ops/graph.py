from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Mapping

from langgraph.graph import END, START, StateGraph

from .capabilities import build_capability_answer
from .events import StageController, StageName, StageReporter
from .interfaces import (
    DeterministicAggregator,
    DirectResponder,
    RequestInterpreter,
    ResultAggregator,
    SpecialistAgent,
)
from .models import (
    Action,
    AgentTask,
    Domain,
    FinalResponse,
    QueryScope,
    RequestRoute,
    RoutePlan,
    SpecialistName,
    SpecialistResult,
    UnderstandRequestResult,
    UserIntent,
)
from .routing import build_initial_plan, select_followup_tasks
from .state import AmazonOpsState


def build_controller_graph(
    *,
    interpreter: RequestInterpreter,
    specialists: Mapping[str, SpecialistAgent],
    aggregator: ResultAggregator | None = None,
    responder: DirectResponder | None = None,
    stages: StageController | None = None,
):
    aggregator = aggregator or DeterministicAggregator()

    def run_id(state: AmazonOpsState) -> str:
        value = state.get("request_id")
        if not value:
            raise ValueError("request_id is required when stage streaming is enabled")
        return str(value)

    def understand_request(state: AmazonOpsState) -> dict:
        identifier = run_id(state) if stages else ""
        if stages:
            stages.start(identifier, StageName.UNDERSTANDING, title="正在理解你的问题")
        try:
            interpreter_state = dict(state)
            if stages:
                interpreter_state["_stage_reporter"] = StageReporter(
                    controller=stages,
                    run_id=identifier,
                    stage=StageName.UNDERSTANDING,
                )
            result = interpreter.invoke(interpreter_state)
        except Exception as exc:
            if stages:
                stages.fail(identifier, StageName.UNDERSTANDING, str(exc))
            raise
        if (
            result.intent.domain == Domain.ADVERTISING
            and result.intent.action in {Action.QUERY, Action.OVERVIEW, Action.COMPARE}
            and result.route == RequestRoute.CLARIFY
            and result.missing_fields
            and set(result.missing_fields).issubset({"shop_id", "period"})
        ):
            result = result.model_copy(
                update={
                    "route": RequestRoute.EXECUTE,
                    "missing_fields": [],
                    "clarification_question": None,
                }
            )
        # With attached images, route straight to the vision responder instead of
        # an operational specialist: the user wants the image analyzed, not a
        # live-data query. Only vision-capable models may carry images (enforced
        # in submit), so the responder reads the image from state and answers
        # directly about it.
        has_images = bool(state.get("image_attachments"))
        final_route = RequestRoute.RESPOND.value if has_images else result.route.value
        if stages:
            stages.progress(
                identifier,
                StageName.UNDERSTANDING,
                kind="intent.resolved",
                domain=result.intent.domain.value,
                action=result.intent.action.value,
                route=final_route,
            )
            stages.complete(identifier, StageName.UNDERSTANDING)
        return {
            "understanding": result.model_dump(mode="json"),
            "intent": result.intent.model_dump(mode="json"),
            "scope": result.scope.model_dump(mode="json"),
            "route": final_route,
            "risk_level": result.risk_level.value,
            "missing_fields": result.missing_fields,
            "clarification_question": result.clarification_question,
            "round": 0,
        }

    def after_understanding(state: AmazonOpsState) -> str:
        return state["route"]

    def direct_response(state: AmazonOpsState) -> dict:
        route = RequestRoute(state["route"])
        understanding = UnderstandRequestResult.model_validate(state["understanding"])
        identifier = run_id(state) if stages else ""
        if route == RequestRoute.CLARIFY:
            answer = understanding.clarification_question or "请补充完成任务所需的信息。"
            if stages:
                stages.wait(
                    identifier,
                    StageName.WAITING_INPUT,
                    title="需要补充信息",
                    missing_fields=understanding.missing_fields,
                    question=answer,
                )
        elif route == RequestRoute.RESPOND:
            has_images = bool(state.get("image_attachments"))
            # With an attached image, skip the deterministic capability answer so
            # the vision responder actually reads the picture and answers about
            # it, instead of returning a static capabilities statement.
            capability_answer = None if has_images else build_capability_answer(state)
            if stages:
                stages.start(identifier, StageName.SYNTHESIS, title="正在分析图片" if has_images else "正在整理回复")
            reporter = StageReporter(controller=stages, run_id=identifier, stage=StageName.SYNTHESIS) if stages else None
            response = None
            streamed = False
            if responder and capability_answer is None:
                stream = getattr(responder, "stream", None)
                if callable(stream) and reporter:
                    response = stream(
                        dict(state),
                        on_delta=lambda text: reporter.emit("response.delta", text=text),
                        on_reasoning_delta=lambda text: reporter.emit("reasoning.delta", text=text),
                    )
                    streamed = True
                else:
                    responder_state = dict(state)
                    if reporter is not None:
                        responder_state["_stage_reporter"] = reporter
                    response = responder.invoke(responder_state)
            answer = capability_answer or (
                response.answer if response else understanding.normalized_request
            )
            if stages:
                if not streamed:
                    stages.progress(identifier, StageName.SYNTHESIS, kind="response.delta", text=answer)
                stages.progress(identifier, StageName.SYNTHESIS, kind="response.completed")
                stages.complete(identifier, StageName.SYNTHESIS)
                stages.finish(
                    identifier,
                    status="completed",
                    result=(
                        response.model_dump(mode="json")
                        if response
                        else {"answer": answer}
                    ),
                )
        elif route == RequestRoute.APPROVAL:
            answer = "该操作需要人工审批，第一版不会直接执行写操作。"
            if stages:
                stages.wait(
                    identifier,
                    StageName.WAITING_APPROVAL,
                    title="等待人工审批",
                    risk_level=understanding.risk_level.value,
                    message=answer,
                )
        else:
            answer = "当前系统已接入的 Agent 或数据源暂不支持执行该请求。"
            if stages:
                stages.start(identifier, StageName.SYNTHESIS, title="正在整理能力说明")
                stages.progress(identifier, StageName.SYNTHESIS, kind="response.ready")
                stages.complete(identifier, StageName.SYNTHESIS)
                stages.finish(
                    identifier,
                    status="unsupported",
                    result={"answer": answer},
                )
        return {"final_response": {"answer": answer}}

    def create_plan(state: AmazonOpsState) -> dict:
        understanding = UnderstandRequestResult.model_validate(state["understanding"])
        identifier = run_id(state) if stages else ""
        if stages:
            stages.start(identifier, StageName.PLANNING, title="正在制定分析计划")
        try:
            plan = build_initial_plan(
                understanding.intent,
                understanding.scope,
                understanding.normalized_request,
            )
        except Exception as exc:
            if stages:
                stages.fail(identifier, StageName.PLANNING, str(exc))
            raise
        if stages:
            stages.progress(
                identifier,
                StageName.PLANNING,
                kind="route.planned",
                execution_mode=plan.execution_mode,
                agents=[task.agent.value for task in plan.tasks],
            )
            stages.complete(identifier, StageName.PLANNING, total_units=len(plan.tasks))
        return {
            "plan": plan.model_dump(mode="json"),
            "pending_tasks": [task.model_dump(mode="json") for task in plan.tasks],
        }

    def execute_specialists(state: AmazonOpsState) -> dict:
        identifier = run_id(state) if stages else ""
        stage = StageName.ANALYSIS if state.get("round", 0) == 0 else StageName.VERIFICATION
        title = "正在分析经营数据" if stage == StageName.ANALYSIS else "正在验证可能原因"
        if stages:
            stages.start(identifier, stage, title=title)
        try:
            tasks = [AgentTask.model_validate(item) for item in state.get("pending_tasks", [])]
            scope = QueryScope.model_validate(state["scope"])
        except Exception as exc:
            if stages:
                stages.fail(identifier, stage, str(exc))
            raise
        results: list[SpecialistResult] = []
        errors: list[dict] = []

        def run(task: AgentTask) -> SpecialistResult:
            if stages:
                stages.progress(
                    identifier,
                    stage,
                    kind="unit.started",
                    unit_id=task.agent.value,
                    task_id=task.task_id,
                )
            try:
                specialist = specialists.get(task.agent.value)
                if specialist is None:
                    result = SpecialistResult(
                        task_id=task.task_id,
                        agent=task.agent,
                        status="unavailable",
                        summary=f"专家 {task.agent.value} 尚未接入。",
                        errors=[{"code": "SPECIALIST_NOT_REGISTERED", "agent": task.agent.value}],
                    )
                else:
                    specialist_state = dict(state)
                    if stages:
                        specialist_state["_stage_reporter"] = StageReporter(
                            controller=stages,
                            run_id=identifier,
                            stage=stage,
                            base_data={"unit_id": task.agent.value, "task_id": task.task_id},
                        )
                    result = specialist.invoke(task, scope, specialist_state)
                if stages:
                    if result.status == "needs_input":
                        kind = "unit.waiting"
                    elif result.status == "completed" and not result.errors:
                        kind = "unit.completed"
                    else:
                        kind = "unit.failed"
                    stages.progress(
                        identifier,
                        stage,
                        kind=kind,
                        unit_id=task.agent.value,
                        task_id=task.task_id,
                        status=result.status,
                        summary=result.summary,
                    )
                return result
            except Exception as exc:
                if stages:
                    stages.progress(
                        identifier,
                        stage,
                        kind="unit.failed",
                        unit_id=task.agent.value,
                        task_id=task.task_id,
                        error=str(exc),
                    )
                raise

        if len(tasks) <= 1:
            for task in tasks:
                try:
                    results.append(run(task))
                except Exception as exc:
                    errors.append({
                        "code": "SPECIALIST_EXECUTION_FAILED",
                        "agent": task.agent.value,
                        "message": str(exc),
                    })
        else:
            with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
                futures = {executor.submit(run, task): task for task in tasks}
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        results.append(future.result())
                    except Exception as exc:  # Boundary: one expert failure must not cancel the graph.
                        errors.append({
                            "code": "SPECIALIST_EXECUTION_FAILED",
                            "agent": task.agent.value,
                            "message": str(exc),
                        })

        if stages:
            stages.complete(
                identifier,
                stage,
                total_units=len(tasks),
                completed_units=len(results),
                failed_units=len(errors) + sum(bool(result.errors) for result in results),
            )

        return {
            "specialist_results": [result.model_dump(mode="json") for result in results],
            "data_artifacts": [
                artifact.model_dump(mode="json")
                for result in results
                for artifact in result.artifacts
            ],
            "called_agents": [task.agent.value for task in tasks],
            "errors": errors,
            "round": state.get("round", 0) + 1,
            "pending_tasks": [],
        }

    def prepare_followups(state: AmazonOpsState) -> dict:
        intent = UserIntent.model_validate(state["intent"])
        plan = RoutePlan.model_validate(state["plan"])
        results = [SpecialistResult.model_validate(item) for item in state.get("specialist_results", [])]
        called = {SpecialistName(name) for name in state.get("called_agents", [])}
        tasks = select_followup_tasks(
            intent=intent,
            results=results,
            called_agents=called,
            current_round=state.get("round", 0),
            max_rounds=plan.max_rounds,
        )
        return {"pending_tasks": [task.model_dump(mode="json") for task in tasks]}

    def after_specialist_execution(state: AmazonOpsState) -> str:
        results = [
            SpecialistResult.model_validate(item)
            for item in state.get("specialist_results", [])
        ]
        return (
            "waiting_input"
            if any(result.status == "needs_input" for result in results)
            else "prepare_followups"
        )

    def specialist_waiting_input(state: AmazonOpsState) -> dict:
        results = [
            SpecialistResult.model_validate(item)
            for item in state.get("specialist_results", [])
        ]
        waiting = next(result for result in reversed(results) if result.status == "needs_input")
        question = waiting.summary or "请继续补充专业 Agent 所需信息。"
        identifier = run_id(state) if stages else ""
        if stages:
            stages.wait(
                identifier,
                StageName.WAITING_INPUT,
                title="专业 Agent 需要补充信息",
                question=question,
                agent=waiting.agent.value,
            )
        return {
            "clarification_question": question,
            "final_response": FinalResponse(answer=question).model_dump(mode="json"),
        }

    def after_followups(state: AmazonOpsState) -> str:
        return "execute" if state.get("pending_tasks") else "aggregate"

    def aggregate_response(state: AmazonOpsState) -> dict:
        identifier = run_id(state) if stages else ""
        if stages:
            stages.start(identifier, StageName.SYNTHESIS, title="正在整理结论")
            stages.progress(
                identifier,
                StageName.SYNTHESIS,
                kind="response.started",
                findings=sum(
                    len(item.get("findings", [])) for item in state.get("specialist_results", [])
                ),
            )
        try:
            aggregation_state = dict(state)
            if stages:
                aggregation_state["_stage_reporter"] = StageReporter(
                    controller=stages,
                    run_id=identifier,
                    stage=StageName.SYNTHESIS,
                )
            response = aggregator.invoke(aggregation_state)
            deliverables = [
                deliverable
                for result in state.get("specialist_results", [])
                for deliverable in result.get("deliverables", [])
            ]
            response = response.model_copy(update={"deliverables": deliverables})
        except Exception as exc:
            if stages:
                stages.fail(identifier, StageName.SYNTHESIS, str(exc))
            raise
        if stages:
            specialist_results = [
                SpecialistResult.model_validate(item)
                for item in state.get("specialist_results", [])
            ]
            all_specialists_failed = bool(specialist_results) and all(
                item.status != "completed" for item in specialist_results
            )
            if all_specialists_failed:
                stages.fail(identifier, StageName.SYNTHESIS, response.answer)
                return {"final_response": response.model_dump(mode="json")}
            stages.progress(
                identifier,
                StageName.SYNTHESIS,
                kind="response.completed",
                confirmed_findings=len(response.confirmed_findings),
                open_hypotheses=len(response.open_hypotheses),
                recommended_actions=len(response.recommended_actions),
            )
            stages.complete(identifier, StageName.SYNTHESIS)
            stages.finish(
                identifier,
                status="completed",
                result=response.model_dump(mode="json"),
            )
        return {"final_response": response.model_dump(mode="json")}

    graph = StateGraph(AmazonOpsState)
    graph.add_node("understand_request", understand_request)
    graph.add_node("direct_response", direct_response)
    graph.add_node("create_plan", create_plan)
    graph.add_node("execute_specialists", execute_specialists)
    graph.add_node("prepare_followups", prepare_followups)
    graph.add_node("specialist_waiting_input", specialist_waiting_input)
    graph.add_node("aggregate_response", aggregate_response)

    graph.add_edge(START, "understand_request")
    graph.add_conditional_edges(
        "understand_request",
        after_understanding,
        {
            RequestRoute.EXECUTE.value: "create_plan",
            RequestRoute.CLARIFY.value: "direct_response",
            RequestRoute.RESPOND.value: "direct_response",
            RequestRoute.APPROVAL.value: "direct_response",
            RequestRoute.UNSUPPORTED.value: "direct_response",
        },
    )
    graph.add_edge("direct_response", END)
    graph.add_edge("create_plan", "execute_specialists")
    graph.add_conditional_edges(
        "execute_specialists",
        after_specialist_execution,
        {
            "waiting_input": "specialist_waiting_input",
            "prepare_followups": "prepare_followups",
        },
    )
    graph.add_edge("specialist_waiting_input", END)
    graph.add_conditional_edges(
        "prepare_followups",
        after_followups,
        {"execute": "execute_specialists", "aggregate": "aggregate_response"},
    )
    graph.add_edge("aggregate_response", END)
    return graph.compile()

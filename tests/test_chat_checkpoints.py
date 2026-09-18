"""Checkpoint and recovery behaviour for ordinary controller chat runs."""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from amazon_ops.api import AgentRunManager
from amazon_ops.events import InMemoryEventHub, StageController
from amazon_ops.graph import build_controller_graph
from amazon_ops.models import (
    Action,
    Domain,
    QueryScope,
    RequestRoute,
    RiskLevel,
    SpecialistName,
    SpecialistResult,
    UnderstandRequestResult,
    UserIntent,
)


class RecordingChatRunStore:
    """Records the checkpoint payloads PostgresChatRunStore would persist.

    Payloads are snapshotted on write, mirroring the real store, which serializes
    to JSONB at call time.
    """

    def __init__(self, claimed: list[dict[str, Any]] | None = None) -> None:
        self.checkpoints: list[tuple[str, str, dict[str, Any]]] = []
        self.claimed = claimed or []

    def checkpoint(self, run_id: str, key: str, state: dict[str, Any]) -> None:
        self.checkpoints.append((run_id, key, json.loads(json.dumps(state, default=str))))

    def claim_stale(self, stale_after: Any) -> list[dict[str, Any]]:
        return list(self.claimed)

    def payload_for(self, node: str) -> dict[str, Any]:
        return next(state for _, key, state in self.checkpoints if key == node)

    def successors(self) -> dict[str, str]:
        return {key: str(state["resume_next"]) for _, key, state in self.checkpoints}


class InlineExecutor:
    """Runs submitted work on the calling thread so recovery is deterministic."""

    def submit(self, function: Any, *args: Any) -> None:
        function(*args)


class NeedsInputSpecialist:
    name = SpecialistName.LISTING_CONTENT.value

    def invoke(self, task: Any, scope: Any, state: Any) -> SpecialistResult:
        return SpecialistResult(
            task_id=task.task_id,
            agent=SpecialistName.LISTING_CONTENT,
            status="needs_input",
            summary="请补充商品材质、尺寸和核心卖点。",
        )


class StubInterpreter:
    def __init__(self, result: UnderstandRequestResult) -> None:
        self.result = result

    def invoke(self, state: Any) -> UnderstandRequestResult:
        return self.result


def listing_understanding() -> UnderstandRequestResult:
    return UnderstandRequestResult(
        intent=UserIntent(domain=Domain.LISTING, action=Action.CREATE, confidence=0.9),
        scope=QueryScope(marketplaces=["US"]),
        route=RequestRoute.EXECUTE,
        risk_level=RiskLevel.READ_ONLY,
        normalized_request="写 Listing 文案",
    )


def needs_input_graph() -> Any:
    return build_controller_graph(
        interpreter=StubInterpreter(listing_understanding()),
        specialists={SpecialistName.LISTING_CONTENT.value: NeedsInputSpecialist()},
        responder=None,
        stages=StageController(InMemoryEventHub()),
    )


def chat_state(request_id: str) -> dict[str, Any]:
    return {
        "messages": [{"role": "user", "content": "写 Listing 文案"}],
        "request_id": request_id,
        "current_time": "2026-09-10T00:00:00+00:00",
        "user_context": {},
        "shop_directory": [],
        "system_capabilities": {},
    }


def test_checkpoint_names_the_node_the_graph_actually_moved_to() -> None:
    """A ``needs_input`` run must be recorded as resuming at the waiting node.

    ``resume_next`` used to be predicted from ``clarification_question``, which
    only the waiting node itself writes, so recovery was recorded as resuming at
    ``prepare_followups`` and skipped the node that produces the follow-up
    question.
    """

    manager = AgentRunManager()
    store = RecordingChatRunStore()
    manager.chat_runs = store  # type: ignore[assignment]

    manager._run_graph_with_checkpoints("run-checkpoint-1", chat_state("run-checkpoint-1"), needs_input_graph())

    assert store.successors()["execute_specialists"] == "specialist_waiting_input"
    recorded = store.payload_for("execute_specialists")
    assert [item["status"] for item in recorded["specialist_results"]] == ["needs_input"]
    assert recorded["resume_next"] == "specialist_waiting_input"


def test_checkpoint_carries_everything_the_successor_needs() -> None:
    """A checkpoint must be self-sufficient: recovery replays from it alone."""

    manager = AgentRunManager()
    store = RecordingChatRunStore()
    manager.chat_runs = store  # type: ignore[assignment]

    manager._run_graph_with_checkpoints("run-checkpoint-2", chat_state("run-checkpoint-2"), needs_input_graph())

    recorded = store.payload_for("execute_specialists")
    assert set(store.successors()) == {"understand_request", "create_plan", "execute_specialists"}
    assert [item["status"] for item in recorded["specialist_results"]] == ["needs_input"]
    assert recorded["request_id"] == "run-checkpoint-2"
    # The interpreter must not be repeated when resuming from the first checkpoint.
    assert "understanding" in store.payload_for("understand_request")
    assert "understanding" not in chat_state("run-checkpoint-2")
    # Terminal nodes are not checkpointed: resuming replays at most the last one.
    assert "specialist_waiting_input" not in store.successors()


class _ReducingState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    notes: Annotated[list[str], operator.add]
    finished: bool


def _reducing_graph() -> Any:
    builder: StateGraph = StateGraph(_ReducingState)
    builder.add_node("first", lambda state: {"messages": [{"role": "assistant", "content": "one"}], "notes": ["one"]})
    builder.add_node("second", lambda state: {"messages": [{"role": "assistant", "content": "two"}], "notes": ["two"], "finished": True})
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    return builder.compile()


def test_streamed_state_applies_state_reducers_instead_of_overwriting() -> None:
    """Reducer keys accumulate exactly as they do under ``graph.invoke``.

    The previous implementation rebuilt the state from a hardcoded list of
    accumulating keys, so any reducer-backed key missing from that list was
    overwritten by the last node update instead of accumulated.
    """

    graph = _reducing_graph()
    manager = AgentRunManager()
    manager.chat_runs = RecordingChatRunStore()  # type: ignore[assignment]
    initial: dict[str, Any] = {"messages": [{"role": "user", "content": "写 Listing 文案"}], "notes": []}

    streamed = manager._run_graph_with_checkpoints("run-checkpoint-3", dict(initial), graph)
    invoked = graph.invoke(dict(initial))

    assert [message.content for message in streamed["messages"]] == ["写 Listing 文案", "one", "two"]
    assert streamed["notes"] == ["one", "two"]
    assert streamed["finished"] is True
    assert set(streamed) == set(invoked)


def test_recovery_from_the_newest_checkpoint_produces_the_follow_up_question() -> None:
    """End to end: the persisted payload alone is enough to finish a stale run."""

    manager = AgentRunManager()
    store = RecordingChatRunStore()
    manager.chat_runs = store  # type: ignore[assignment]
    manager._run_graph_with_checkpoints(
        "run-checkpoint-6", chat_state("run-checkpoint-6"), needs_input_graph()
    )

    _, key, payload = store.checkpoints[-1]
    assert key == "execute_specialists"

    class MustNotRunInterpreter:
        def invoke(self, state: Any) -> Any:
            raise AssertionError("recovery must not repeat request understanding")

    resumed = build_controller_graph(
        interpreter=MustNotRunInterpreter(),
        specialists={SpecialistName.LISTING_CONTENT.value: NeedsInputSpecialist()},
        responder=None,
        stages=StageController(InMemoryEventHub()),
    ).invoke(dict(payload))

    assert resumed["clarification_question"] == "请补充商品材质、尺寸和核心卖点。"
    assert resumed["final_response"]["answer"] == "请补充商品材质、尺寸和核心卖点。"


def test_recovery_resumes_at_the_checkpointed_successor() -> None:
    """Recovery must not re-run the interpreter or skip the recorded node."""

    manager = AgentRunManager()
    submitted: list[tuple[Any, ...]] = []
    manager._executor = InlineExecutor()  # type: ignore[assignment]
    manager._execute = lambda *args: submitted.append(args)  # type: ignore[method-assign]
    manager.chat_runs = RecordingChatRunStore(  # type: ignore[assignment]
        [
            {
                "run_id": "run-stale-1",
                "owner_id": "owner-1",
                "conversation_id": "conversation-1",
                "checkpoint_key": "execute_specialists",
                "state": {
                    "request_id": "run-stale-1",
                    "specialist_results": [{"status": "needs_input"}],
                    "resume_next": "specialist_waiting_input",
                },
            }
        ]
    )

    assert manager.recover_interrupted_chat_runs() == ["run-stale-1"]
    assert len(submitted) == 1
    run_id, conversation_id, state, owner_id, _, _ = submitted[0]
    assert (run_id, conversation_id, owner_id) == ("run-stale-1", "conversation-1", "owner-1")
    assert state["resume_next"] == "specialist_waiting_input"
    assert state["specialist_results"] == [{"status": "needs_input"}]


def test_recovery_without_a_recorded_successor_restarts_from_the_entry_node() -> None:
    """A checkpoint payload with no ``resume_next`` must not skip work."""

    manager = AgentRunManager()
    submitted: list[tuple[Any, ...]] = []
    manager._executor = InlineExecutor()  # type: ignore[assignment]
    manager._execute = lambda *args: submitted.append(args)  # type: ignore[method-assign]
    manager.chat_runs = RecordingChatRunStore(  # type: ignore[assignment]
        [
            {
                "run_id": "run-stale-2",
                "owner_id": None,
                "conversation_id": "conversation-2",
                "checkpoint_key": "create_plan",
                "state": {"request_id": "run-stale-2"},
            }
        ]
    )

    assert manager.recover_interrupted_chat_runs() == ["run-stale-2"]
    assert submitted[0][2]["resume_next"] == "understand_request"

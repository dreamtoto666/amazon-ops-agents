"""Stage-oriented event protocol independent from LangGraph's node stream."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from time import monotonic
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class StageName(str, Enum):
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    ANALYSIS = "analysis"
    VERIFICATION = "verification"
    SYNTHESIS = "synthesis"
    WAITING_INPUT = "waiting_input"
    WAITING_APPROVAL = "waiting_approval"
    DATA_INSPECTION = "data_inspection"
    PROBLEM_ATTRIBUTION = "problem_attribution"
    STRATEGY_RECOMMENDATION = "strategy_recommendation"
    REVIEW_TODO = "review_todo"


class StageEventType(str, Enum):
    RUN_STARTED = "run.started"
    STAGE_STARTED = "stage.started"
    STAGE_PROGRESS = "stage.progress"
    STAGE_COMPLETED = "stage.completed"
    STAGE_WAITING = "stage.waiting"
    STAGE_FAILED = "stage.failed"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"


TERMINAL_EVENT_TYPES = {
    StageEventType.RUN_COMPLETED,
    StageEventType.RUN_FAILED,
}


class StageEvent(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    event_id: str
    run_id: str
    trace_id: str | None = None
    span_id: str | None = None
    sequence: int = Field(ge=1)
    event: StageEventType
    stage: StageName | None = None
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


@dataclass
class _Subscriber:
    subscriber_id: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[StageEvent | None]


class EventSubscription:
    """Replay-first asynchronous subscription to one run."""

    def __init__(
        self,
        hub: "InMemoryEventHub",
        run_id: str,
        replay: list[StageEvent],
        subscriber: _Subscriber | None,
    ) -> None:
        self._hub = hub
        self.run_id = run_id
        self._replay = deque(replay)
        self._subscriber = subscriber
        self._closed = False

    async def next_event(self) -> StageEvent | None:
        if self._replay:
            return self._replay.popleft()
        if self._closed or self._subscriber is None:
            return None
        event = await self._subscriber.queue.get()
        if event is None:
            self.close()
        return event

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._subscriber is not None:
            self._hub._unsubscribe(self.run_id, self._subscriber.subscriber_id)


class InMemoryEventHub:
    """Thread-safe event store and live fan-out for development and tests.

    Events are stored before publishing. If a subscriber cannot keep up, its
    connection is closed so it can reconnect with Last-Event-ID and replay the
    missing sequence without silent data loss.
    """

    def __init__(self, *, subscriber_queue_size: int = 256) -> None:
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")
        self._queue_size = subscriber_queue_size
        self._lock = RLock()
        self._events: dict[str, list[StageEvent]] = defaultdict(list)
        self._sequences: dict[str, int] = defaultdict(int)
        self._subscribers: dict[str, dict[str, _Subscriber]] = defaultdict(dict)

    def emit(
        self,
        run_id: str,
        event: StageEventType,
        *,
        trace_id: str | None = None,
        span_id: str | None = None,
        stage: StageName | None = None,
        data: dict[str, Any] | None = None,
    ) -> StageEvent:
        with self._lock:
            self._sequences[run_id] += 1
            sequence = self._sequences[run_id]
            item = StageEvent(
                event_id=f"{run_id}:{sequence}",
                run_id=run_id,
                trace_id=trace_id,
                span_id=span_id,
                sequence=sequence,
                event=event,
                stage=stage,
                timestamp=datetime.now(timezone.utc),
                data=data or {},
            )
            self._events[run_id].append(item)
            subscribers = list(self._subscribers[run_id].values())

        for subscriber in subscribers:
            subscriber.loop.call_soon_threadsafe(self._deliver, run_id, subscriber, item)
        return item

    def events_after(self, run_id: str, sequence: int = 0) -> list[StageEvent]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._events.get(run_id, []) if item.sequence > sequence]

    def subscribe(self, run_id: str, *, after_sequence: int = 0) -> EventSubscription:
        loop = asyncio.get_running_loop()
        with self._lock:
            all_events = self._events.get(run_id, [])
            replay = [item.model_copy(deep=True) for item in all_events if item.sequence > after_sequence]
            already_terminal = any(StageEventType(item.event) in TERMINAL_EVENT_TYPES for item in all_events)
            subscriber: _Subscriber | None = None
            if not already_terminal:
                subscriber = _Subscriber(
                    subscriber_id=uuid4().hex,
                    loop=loop,
                    queue=asyncio.Queue(maxsize=self._queue_size),
                )
                self._subscribers[run_id][subscriber.subscriber_id] = subscriber
        return EventSubscription(self, run_id, replay, subscriber)

    def _deliver(self, run_id: str, subscriber: _Subscriber, event: StageEvent) -> None:
        with self._lock:
            if subscriber.subscriber_id not in self._subscribers.get(run_id, {}):
                return
        try:
            subscriber.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Force reconnect. Persisted events can be replayed from the last id.
            self._unsubscribe(run_id, subscriber.subscriber_id)
            try:
                subscriber.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                subscriber.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _unsubscribe(self, run_id: str, subscriber_id: str) -> None:
        with self._lock:
            subscribers = self._subscribers.get(run_id)
            if not subscribers:
                return
            subscribers.pop(subscriber_id, None)
            if not subscribers:
                self._subscribers.pop(run_id, None)


ALLOWED_TRANSITIONS: dict[StageName, set[StageName]] = {
    StageName.UNDERSTANDING: {
        StageName.PLANNING,
        StageName.SYNTHESIS,
        StageName.WAITING_INPUT,
        StageName.WAITING_APPROVAL,
    },
    StageName.WAITING_INPUT: {StageName.UNDERSTANDING},
    StageName.PLANNING: {StageName.ANALYSIS},
    StageName.ANALYSIS: {
        StageName.VERIFICATION,
        StageName.SYNTHESIS,
        StageName.WAITING_INPUT,
    },
    StageName.VERIFICATION: {StageName.SYNTHESIS},
    StageName.SYNTHESIS: {StageName.WAITING_APPROVAL},
    StageName.WAITING_APPROVAL: {StageName.UNDERSTANDING, StageName.SYNTHESIS},
}


@dataclass
class _RunStageState:
    run_started: bool = False
    current: StageName | None = None
    previous: StageName | None = None
    stage_started_at: float | None = None
    terminal: bool = False
    lock: RLock = field(default_factory=RLock)


class StageTransitionError(RuntimeError):
    pass


class StageController:
    """Deterministic stage state machine that emits transport-neutral events."""

    def __init__(self, hub: InMemoryEventHub) -> None:
        self.hub = hub
        self._lock = RLock()
        self._runs: dict[str, _RunStageState] = {}

    def start_run(self, run_id: str, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            if state.terminal:
                raise StageTransitionError(f"run {run_id} is already terminal")
            if state.run_started:
                return
            state.run_started = True
            self.hub.emit(run_id, StageEventType.RUN_STARTED, data=data)

    def start(self, run_id: str, stage: StageName, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            self.start_run(run_id)
            if state.current in {StageName.WAITING_INPUT, StageName.WAITING_APPROVAL}:
                self._complete_locked(run_id, state, resumed=True)
            if state.current is not None:
                raise StageTransitionError(f"stage {state.current.value} is still active")
            if state.previous is not None and stage not in ALLOWED_TRANSITIONS.get(state.previous, set()):
                raise StageTransitionError(
                    f"invalid stage transition: {state.previous.value} -> {stage.value}"
                )
            state.current = stage
            state.stage_started_at = monotonic()
            self.hub.emit(run_id, StageEventType.STAGE_STARTED, stage=stage, data=data)

    def progress(self, run_id: str, stage: StageName, *, kind: str, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            self._require_current(run_id, state, stage)
            self.hub.emit(
                run_id,
                StageEventType.STAGE_PROGRESS,
                stage=stage,
                data={"kind": kind, **data},
            )

    def complete(self, run_id: str, stage: StageName, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            self._require_current(run_id, state, stage)
            self._complete_locked(run_id, state, **data)

    def wait(self, run_id: str, stage: StageName, **data: Any) -> None:
        self.start(run_id, stage, **data)
        state = self._state(run_id)
        with state.lock:
            self.hub.emit(run_id, StageEventType.STAGE_WAITING, stage=stage, data=data)

    def fail(self, run_id: str, stage: StageName, error: str, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            if state.terminal:
                return
            if state.current == stage:
                self.hub.emit(
                    run_id,
                    StageEventType.STAGE_FAILED,
                    stage=stage,
                    data={"error": error, **data},
                )
                state.previous = stage
                state.current = None
                state.stage_started_at = None
            state.terminal = True
            self.hub.emit(
                run_id,
                StageEventType.RUN_FAILED,
                stage=stage,
                data={"error": error},
            )

    def finish(self, run_id: str, **data: Any) -> None:
        state = self._state(run_id)
        with state.lock:
            if state.current is not None:
                raise StageTransitionError(f"cannot finish while {state.current.value} is active")
            if state.terminal:
                return
            state.terminal = True
            self.hub.emit(run_id, StageEventType.RUN_COMPLETED, data=data)

    def _state(self, run_id: str) -> _RunStageState:
        with self._lock:
            return self._runs.setdefault(run_id, _RunStageState())

    def _complete_locked(self, run_id: str, state: _RunStageState, **data: Any) -> None:
        assert state.current is not None
        duration_ms = None
        if state.stage_started_at is not None:
            duration_ms = round((monotonic() - state.stage_started_at) * 1000)
        completed = state.current
        self.hub.emit(
            run_id,
            StageEventType.STAGE_COMPLETED,
            stage=completed,
            data={"duration_ms": duration_ms, **data},
        )
        state.previous = completed
        state.current = None
        state.stage_started_at = None

    @staticmethod
    def _require_current(run_id: str, state: _RunStageState, stage: StageName) -> None:
        if state.current != stage:
            actual = state.current.value if state.current else "none"
            raise StageTransitionError(
                f"run {run_id} expected active stage {stage.value}, got {actual}"
            )


@dataclass(frozen=True)
class StageReporter:
    """A scoped emitter for low-latency progress inside a running stage."""

    controller: StageController
    run_id: str
    stage: StageName
    base_data: dict[str, Any] = field(default_factory=dict)

    def emit(self, kind: str, **data: Any) -> None:
        self.controller.progress(
            self.run_id,
            self.stage,
            kind=kind,
            **self.base_data,
            **data,
        )

    def child(self, **base_data: Any) -> "StageReporter":
        return StageReporter(
            controller=self.controller,
            run_id=self.run_id,
            stage=self.stage,
            base_data={**self.base_data, **base_data},
        )


def get_stage_reporter(state: dict[str, Any]) -> StageReporter | None:
    """Return the transient reporter injected into specialist/aggregator input."""

    reporter = state.get("_stage_reporter")
    return reporter if isinstance(reporter, StageReporter) else None

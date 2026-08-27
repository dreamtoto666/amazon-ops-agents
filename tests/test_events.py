import asyncio
import json

import pytest

from amazon_ops.events import (
    InMemoryEventHub,
    StageController,
    StageEventType,
    StageName,
    StageTransitionError,
)
from amazon_ops.sse import encode_sse_event, parse_last_event_id, stage_sse_stream


def test_stage_controller_emits_ordered_business_stages():
    hub = InMemoryEventHub()
    stages = StageController(hub)

    stages.start("run-1", StageName.UNDERSTANDING)
    stages.progress("run-1", StageName.UNDERSTANDING, kind="intent.resolved")
    stages.complete("run-1", StageName.UNDERSTANDING)
    stages.start("run-1", StageName.PLANNING)
    stages.complete("run-1", StageName.PLANNING)
    stages.start("run-1", StageName.ANALYSIS)
    stages.complete("run-1", StageName.ANALYSIS)
    stages.start("run-1", StageName.SYNTHESIS)
    stages.complete("run-1", StageName.SYNTHESIS)
    stages.finish("run-1")

    events = hub.events_after("run-1")

    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event == StageEventType.RUN_STARTED
    assert events[-1].event == StageEventType.RUN_COMPLETED
    assert [event.stage for event in events if event.event == StageEventType.STAGE_STARTED] == [
        StageName.UNDERSTANDING,
        StageName.PLANNING,
        StageName.ANALYSIS,
        StageName.SYNTHESIS,
    ]


def test_invalid_stage_transition_is_rejected():
    hub = InMemoryEventHub()
    stages = StageController(hub)
    stages.start("run-2", StageName.UNDERSTANDING)
    stages.complete("run-2", StageName.UNDERSTANDING)

    with pytest.raises(StageTransitionError):
        stages.start("run-2", StageName.ANALYSIS)


def test_sse_encoder_uses_event_id_and_event_name():
    hub = InMemoryEventHub()
    event = hub.emit(
        "run-3",
        StageEventType.RUN_STARTED,
        trace_id="trace-3",
        span_id="span-3",
        data={"title": "开始"},
    )

    encoded = encode_sse_event(event)

    assert encoded.startswith("id: run-3:1\nevent: run.started\ndata: ")
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["event_id"] == "run-3:1"
    assert payload["trace_id"] == "trace-3"
    assert payload["span_id"] == "span-3"
    assert payload["data"]["title"] == "开始"


def test_last_event_id_is_scoped_to_run():
    assert parse_last_event_id("run-4:12", run_id="run-4") == 12
    assert parse_last_event_id("12", run_id="run-4") == 12
    with pytest.raises(ValueError):
        parse_last_event_id("other:12", run_id="run-4")


def test_sse_replays_after_last_event_id_and_closes_on_terminal():
    async def collect():
        hub = InMemoryEventHub()
        hub.emit("run-5", StageEventType.RUN_STARTED)
        hub.emit("run-5", StageEventType.STAGE_STARTED, stage=StageName.UNDERSTANDING)
        hub.emit("run-5", StageEventType.RUN_COMPLETED)
        return [
            chunk
            async for chunk in stage_sse_stream(
                hub,
                "run-5",
                last_event_id="run-5:1",
                heartbeat_seconds=0.1,
            )
        ]

    chunks = asyncio.run(collect())

    assert len(chunks) == 2
    assert chunks[0].startswith("id: run-5:2")
    assert "event: run.completed" in chunks[1]


def test_reconnecting_after_terminal_with_latest_id_returns_immediately():
    async def collect():
        hub = InMemoryEventHub()
        hub.emit("run-terminal", StageEventType.RUN_STARTED)
        hub.emit("run-terminal", StageEventType.RUN_COMPLETED)
        return [
            chunk
            async for chunk in stage_sse_stream(
                hub,
                "run-terminal",
                last_event_id="run-terminal:2",
                heartbeat_seconds=0.1,
            )
        ]

    assert asyncio.run(collect()) == []


def test_sse_closes_when_run_waits_for_user_input():
    async def collect():
        hub = InMemoryEventHub()
        hub.emit("run-6", StageEventType.RUN_STARTED)
        hub.emit(
            "run-6",
            StageEventType.STAGE_WAITING,
            stage=StageName.WAITING_INPUT,
            data={"question": "请选择店铺"},
        )
        return [chunk async for chunk in stage_sse_stream(hub, "run-6", heartbeat_seconds=0.1)]

    chunks = asyncio.run(collect())

    assert len(chunks) == 2
    assert "event: stage.waiting" in chunks[-1]

"""SSE transport for the stage event protocol."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from .events import InMemoryEventHub, StageEvent, StageEventType, TERMINAL_EVENT_TYPES


STREAM_CLOSING_EVENT_TYPES = TERMINAL_EVENT_TYPES | {StageEventType.STAGE_WAITING}

SSE_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "Content-Type": "text/event-stream; charset=utf-8",
    "X-Accel-Buffering": "no",
}


def parse_last_event_id(value: str | None, *, run_id: str) -> int:
    if not value:
        return 0
    prefix, separator, raw_sequence = value.rpartition(":")
    if separator:
        if prefix != run_id:
            raise ValueError("Last-Event-ID belongs to a different run")
    else:
        raw_sequence = value
    sequence = int(raw_sequence)
    if sequence < 0:
        raise ValueError("Last-Event-ID sequence must be non-negative")
    return sequence


def encode_sse_event(event: StageEvent) -> str:
    payload = event.model_dump(mode="json")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event.event_id}\nevent: {event.event}\ndata: {data}\n\n"


def encode_heartbeat(run_id: str) -> str:
    data = json.dumps(
        {
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"event: heartbeat\ndata: {data}\n\n"


async def stage_sse_stream(
    hub: InMemoryEventHub,
    run_id: str,
    *,
    last_event_id: str | None = None,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Replay missed events, then switch to live delivery until the run ends."""

    if heartbeat_seconds <= 0:
        raise ValueError("heartbeat_seconds must be positive")
    after_sequence = parse_last_event_id(last_event_id, run_id=run_id)
    subscription = hub.subscribe(run_id, after_sequence=after_sequence)
    try:
        while True:
            try:
                event = await asyncio.wait_for(
                    subscription.next_event(),
                    timeout=heartbeat_seconds,
                )
            except asyncio.TimeoutError:
                yield encode_heartbeat(run_id)
                continue
            if event is None:
                return
            yield encode_sse_event(event)
            if StageEventType(event.event) in STREAM_CLOSING_EVENT_TYPES:
                return
    finally:
        subscription.close()

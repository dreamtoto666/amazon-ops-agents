"""Optional, non-intrusive Langfuse tracing helpers.

Langfuse is deliberately best-effort: an unavailable observability service must
never stop an Amazon operations run.

One client is shared for the whole process.  A Langfuse client owns a background
exporter and its own HTTP client, so building one per observation would leak a
client for every LLM call; the shared instance is flushed once on shutdown
instead.  Only client construction is guarded here: once a client exists, an
error raised while opening or closing an observation is still reported rather
than hidden.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from threading import RLock
from typing import Any, Iterator

import httpx
from dotenv import load_dotenv
from langfuse import Langfuse


_UNRESOLVED = object()

_client: Langfuse | None | object = _UNRESOLVED
_client_lock = RLock()


def _build_client() -> Langfuse | None:
    """Build a client only when both local credentials are present."""

    load_dotenv(override=False)
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    if not public_key or not secret_key:
        return None
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=os.getenv("LANGFUSE_BASE_URL", "").strip() or None,
        # The application deliberately bypasses workstation proxy settings for
        # the private Langfuse endpoint.
        httpx_client=httpx.Client(trust_env=False),
    )


def _release(client: Langfuse | None) -> None:
    """Flush and dispose of a client without ever raising."""

    if client is None:
        return
    try:
        client.shutdown()
    except Exception:
        # Observability teardown must not break application teardown.
        return


def _langfuse_client() -> Langfuse | None:
    """Return the process-wide client, creating it at most once.

    Missing credentials resolve to ``None`` and stay resolved.  A failed
    construction is deliberately not cached, so a later observation can retry it.
    """

    global _client

    with _client_lock:
        if _client is not _UNRESOLVED:
            return _client  # type: ignore[return-value]

    try:
        client = _build_client()
    except Exception:
        # Tracing is optional and must not turn an otherwise successful run into
        # a user-visible failure when the local client cannot initialize.
        return None

    with _client_lock:
        resolved = _client
        if resolved is _UNRESOLVED:
            resolved = client
            _client = client

    if client is not None and client is not resolved:
        # Another thread resolved first: dispose of ours instead of leaking it.
        _release(client)
    return resolved  # type: ignore[return-value]


def shutdown_observability() -> None:
    """Flush buffered observations and release the shared client."""

    global _client

    with _client_lock:
        client = None if _client is _UNRESOLVED else _client
        _client = _UNRESOLVED
    _release(client)  # type: ignore[arg-type]


@contextmanager
def langfuse_observation(
    *, name: str, as_type: str = "span", metadata: dict[str, Any] | None = None
) -> Iterator[None]:
    """Attach nested Langfuse observations without exposing business payloads."""

    client = _langfuse_client()
    if client is None:
        yield
        return
    with client.start_as_current_observation(
        name=name,
        as_type=as_type,  # type: ignore[arg-type]
        metadata=metadata,
    ):
        yield

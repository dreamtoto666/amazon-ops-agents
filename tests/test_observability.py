"""Observability client lifecycle: one shared client, released on shutdown."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from amazon_ops import observability


class RecordingLangfuse:
    """Stands in for the Langfuse client and records lifecycle calls."""

    instances: list["RecordingLangfuse"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.shutdown_calls = 0
        self.observations: list[dict[str, Any]] = []
        RecordingLangfuse.instances.append(self)

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Iterator[None]:
        self.observations.append(kwargs)
        yield


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give every test a fresh client cache and a fake Langfuse client."""

    monkeypatch.setattr(observability, "Langfuse", RecordingLangfuse)
    monkeypatch.setattr(observability, "_client", observability._UNRESOLVED)
    # Credentials come from the test only, never from a developer's .env file.
    monkeypatch.setattr(observability, "load_dotenv", lambda *args, **kwargs: None)
    RecordingLangfuse.instances.clear()
    yield
    RecordingLangfuse.instances.clear()


def configure_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://langfuse.test:3000")


def test_one_client_is_shared_across_observations(monkeypatch: pytest.MonkeyPatch) -> None:
    """A client per observation would leak an exporter per LLM call."""

    configure_credentials(monkeypatch)

    for index in range(3):
        with observability.langfuse_observation(name=f"call-{index}"):
            pass

    assert len(RecordingLangfuse.instances) == 1
    assert [item["name"] for item in RecordingLangfuse.instances[0].observations] == [
        "call-0",
        "call-1",
        "call-2",
    ]


def test_missing_credentials_disable_tracing_without_creating_a_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    executed: list[str] = []

    with observability.langfuse_observation(name="business"):
        executed.append("ran")

    assert executed == ["ran"]
    assert RecordingLangfuse.instances == []
    # A resolved-but-disabled client must not be rebuilt on every call.
    assert observability._client is None


def test_shutdown_flushes_once_and_allows_a_fresh_client(monkeypatch: pytest.MonkeyPatch) -> None:
    configure_credentials(monkeypatch)

    with observability.langfuse_observation(name="before-shutdown"):
        pass
    observability.shutdown_observability()

    assert [item.shutdown_calls for item in RecordingLangfuse.instances] == [1]
    assert observability._client is observability._UNRESOLVED

    with observability.langfuse_observation(name="after-shutdown"):
        pass

    assert len(RecordingLangfuse.instances) == 2


def test_shutdown_without_a_client_is_a_noop() -> None:
    observability.shutdown_observability()

    assert RecordingLangfuse.instances == []
    assert observability._client is observability._UNRESOLVED


def test_a_failed_construction_does_not_break_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tracing is optional; an unusable client must not fail a business run."""

    configure_credentials(monkeypatch)
    attempts: list[int] = []

    def failing(**kwargs: Any) -> RecordingLangfuse:
        attempts.append(1)
        raise RuntimeError("client init failed")

    monkeypatch.setattr(observability, "Langfuse", failing)
    executed: list[str] = []

    with observability.langfuse_observation(name="business"):
        executed.append("ran")

    assert executed == ["ran"]
    # The failure is not cached, so a later observation may still succeed.
    assert attempts == [1]
    assert observability._client is observability._UNRESOLVED

    monkeypatch.setattr(observability, "Langfuse", RecordingLangfuse)
    with observability.langfuse_observation(name="retry"):
        pass

    assert len(RecordingLangfuse.instances) == 1


def test_a_losing_race_disposes_of_the_extra_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parallel specialists can race the first client into existence."""

    configure_credentials(monkeypatch)
    barrier = threading.Barrier(3)
    opened: list[Any] = []

    def slow(**kwargs: Any) -> RecordingLangfuse:
        barrier.wait(timeout=5)
        return RecordingLangfuse(**kwargs)

    monkeypatch.setattr(observability, "Langfuse", slow)

    def worker() -> None:
        opened.append(observability._langfuse_client())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)

    assert len(RecordingLangfuse.instances) == 2
    assert len({id(item) for item in opened}) == 1
    assert sum(item.shutdown_calls for item in RecordingLangfuse.instances) == 1


def test_business_errors_are_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only client construction is guarded; failures inside a run stay visible."""

    configure_credentials(monkeypatch)

    with pytest.raises(ValueError, match="business failure"):
        with observability.langfuse_observation(name="boom"):
            raise ValueError("business failure")


def test_local_env_file_is_loaded_without_overriding_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, Any]] = []

    def recording_load_dotenv(*args: Any, **kwargs: Any) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(observability, "load_dotenv", recording_load_dotenv)
    configure_credentials(monkeypatch)

    observability._langfuse_client()

    assert calls == [((), {"override": False})]
    assert RecordingLangfuse.instances[0].kwargs["base_url"] == "http://langfuse.test:3000"
    assert set(RecordingLangfuse.instances[0].kwargs) == {
        "public_key",
        "secret_key",
        "base_url",
        "httpx_client",
    }

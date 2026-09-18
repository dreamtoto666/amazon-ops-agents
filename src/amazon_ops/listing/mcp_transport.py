from __future__ import annotations

import asyncio
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any, Awaitable, Callable, TypeVar

import httpx2
from dotenv import load_dotenv
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from .mcp import (
    MCPGatewayError,
    MCPServerConfig,
    MCPToolDefinition,
)


T = TypeVar("T")


def _walk_exceptions(exc: BaseException) -> list[BaseException]:
    """Collect an exception and everything nested under it, groups included."""

    seen: set[int] = set()
    found: list[BaseException] = []

    def visit(current: BaseException) -> None:
        if id(current) in seen:
            return
        seen.add(id(current))
        found.append(current)
        children = getattr(current, "exceptions", ())
        if isinstance(children, tuple):
            for child in children:
                if isinstance(child, BaseException):
                    visit(child)
        for child in (current.__cause__, current.__context__):
            if isinstance(child, BaseException):
                visit(child)

    visit(exc)
    return found


def _exception_text(exc: BaseException) -> str:
    """Flatten exception groups so MCP JSON-RPC errors retain their meaning."""

    return " ".join(text for text in (str(item) for item in _walk_exceptions(exc)) if text)


def _gateway_error(exc: BaseException) -> MCPGatewayError | None:
    """Return a structured gateway error raised inside the operation, if any.

    The MCP SDK runs operations inside an asyncio TaskGroup, so an error raised
    while handling a result arrives wrapped in an ExceptionGroup.  Without
    unwrapping, its code would be replaced by a generic transport failure.
    """

    for item in _walk_exceptions(exc):
        if isinstance(item, MCPGatewayError):
            return item
    return None


def _classify_transport_failure(exc: BaseException) -> tuple[str, bool]:
    text = _exception_text(exc).casefold()
    if "unknown tool" in text or "tool not found" in text:
        return "MCP_TOOL_UNAVAILABLE", False
    if "catalogversion" in text or "schemaversion" in text:
        return "MCP_CATALOG_VERSION_STALE", False
    if "rate limit" in text or "too many requests" in text:
        return "MCP_RATE_LIMITED", True
    if "timeout" in text or isinstance(exc, (TimeoutError, ConnectionError)):
        return "MCP_TRANSPORT_FAILURE", True
    return "MCP_TRANSPORT_FAILURE", False


# Upstream error results may carry credential identifiers in their text, so only
# an already upper-snake token is ever accepted as a code and the rest is dropped.
_ERROR_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _tool_error_code(result: Any) -> str:
    """Recover a short machine code from a tool-level MCP error result.

    Only a strict code shape is read out; free text is never echoed, because
    provider error bodies can contain key identifiers.
    """

    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if not isinstance(text, str):
            continue
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for key in ("error", "code"):
            candidate = payload.get(key)
            if isinstance(candidate, str) and _ERROR_CODE_PATTERN.match(candidate.strip()):
                return candidate.strip()
    return "MCP_TOOL_ERROR"


def _tool_payload(result: Any, *, config: MCPServerConfig, tool: str) -> Any:
    """Serialize a tool result, raising when the server reported a failure.

    MCP reports a *tool* failure as a successful call whose result sets
    ``isError``, with the reason in the content blocks.  Without this check the
    failure would be recorded as a successful call and the error body would be
    filed away as evidence.
    """

    if getattr(result, "is_error", False):
        raise MCPGatewayError(
            f"tool {tool} returned an error result",
            provider=config.provider,
            tool=tool,
            code=_tool_error_code(result),
        )
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


class StreamableHTTPMCPTransport:
    """Official MCP SDK adapter for authenticated Streamable HTTP servers.

    A short-lived negotiated MCP session is used per operation. This keeps the
    synchronous LangGraph boundary safe and avoids sharing an async session
    across worker threads. Provider-level semaphores enforce bounded
    concurrency. A future async graph can replace this adapter with a pooled
    async lifecycle without changing the domain ports.
    """

    def __init__(
        self,
        *,
        env_file: str | Path = ".env",
        trust_env: bool = False,
    ) -> None:
        self.env_file = Path(env_file)
        self.trust_env = trust_env
        self._lock = Lock()
        self._semaphores: dict[str, BoundedSemaphore] = {}

    def list_tools(self, config: MCPServerConfig) -> list[MCPToolDefinition]:
        async def operation(client: Client) -> list[MCPToolDefinition]:
            result = await client.list_tools()
            return [
                MCPToolDefinition(
                    name=tool.name,
                    description=tool.description,
                    input_schema=dict(tool.input_schema or {}),
                )
                for tool in result.tools
            ]

        return self._execute(config, operation)

    def call_tool(
        self,
        config: MCPServerConfig,
        tool: str,
        arguments: dict[str, Any],
    ) -> Any:
        async def operation(client: Client) -> Any:
            result = await client.call_tool(tool, arguments)
            return _tool_payload(result, config=config, tool=tool)

        return self._execute(config, operation)

    def _execute(
        self,
        config: MCPServerConfig,
        operation: Callable[[Client], Awaitable[T]],
    ) -> T:
        secret = self._read_secret(config)
        semaphore = self._semaphore(config)
        with semaphore:
            return self._run(self._execute_async(config, secret, operation))

    async def _execute_async(
        self,
        config: MCPServerConfig,
        secret: str,
        operation: Callable[[Client], Awaitable[T]],
    ) -> T:
        headers = {config.auth_header: secret} if config.auth_header else {}
        timeout = httpx2.Timeout(
            connect=config.timeout_seconds,
            write=config.timeout_seconds,
            pool=config.timeout_seconds,
            read=max(300, config.timeout_seconds),
        )
        limits = httpx2.Limits(
            max_connections=config.max_concurrency,
            max_keepalive_connections=config.max_concurrency,
        )
        try:
            async with httpx2.AsyncClient(
                headers=headers,
                timeout=timeout,
                limits=limits,
                follow_redirects=True,
                trust_env=self.trust_env,
            ) as http_client:
                transport = streamable_http_client(config.endpoint, http_client=http_client)
                async with Client(transport, raise_exceptions=True) as client:
                    return await operation(client)
        except MCPGatewayError:
            raise
        except Exception as exc:
            nested = _gateway_error(exc)
            if nested is not None:
                raise nested from exc
            code, retryable = _classify_transport_failure(exc)
            raise MCPGatewayError(
                f"{config.provider.value} MCP connection failed: {type(exc).__name__}",
                provider=config.provider,
                code=code,
                retryable=retryable,
            ) from exc

    def _read_secret(self, config: MCPServerConfig) -> str:
        load_dotenv(self.env_file, override=False)
        secret = os.getenv(config.api_key_env, "").strip()
        if not secret:
            raise MCPGatewayError(
                f"missing environment variable {config.api_key_env}",
                provider=config.provider,
                code="MCP_SECRET_MISSING",
            )
        return secret

    def _semaphore(self, config: MCPServerConfig) -> BoundedSemaphore:
        key = config.provider.value
        with self._lock:
            return self._semaphores.setdefault(key, BoundedSemaphore(config.max_concurrency))

    @staticmethod
    def _run(awaitable: Awaitable[T]) -> T:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)

        # The synchronous specialist graph may be invoked from an async web
        # server. Run the SDK lifecycle on a dedicated thread instead of nesting
        # event loops.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-sync-adapter") as executor:
            return executor.submit(asyncio.run, awaitable).result()

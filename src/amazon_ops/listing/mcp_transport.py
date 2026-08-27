from __future__ import annotations

import asyncio
import os
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
            return result.model_dump(mode="json", by_alias=True, exclude_none=True)

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
            retryable = isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in str(
                exc
            ).lower()
            raise MCPGatewayError(
                f"{config.provider.value} MCP connection failed: {type(exc).__name__}",
                provider=config.provider,
                code="MCP_TRANSPORT_FAILURE",
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

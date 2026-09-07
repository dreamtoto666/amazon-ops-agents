"""Trusted stdio MCP client for the shared-data NL2SQL tool."""
from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from mcp.client import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from .nl2sql import NL2SQLError, NL2SQLResult
from .nl2sql_mcp import TOOL_NAME


class NL2SQLMCPClientError(RuntimeError):
    pass


ToolCaller = Callable[[str], dict[str, Any]]


class _StdioMCPTransport:
    """Adapt the SDK's stdio stream pair to the high-level Client transport API."""

    def __init__(self, parameters: StdioServerParameters) -> None:
        self._parameters = parameters
        self._context: Any | None = None

    async def __aenter__(self) -> Any:
        self._context = stdio_client(self._parameters)
        return await self._context.__aenter__()

    async def __aexit__(self, *args: Any) -> Any:
        if self._context is None:
            return None
        return await self._context.__aexit__(*args)


class NL2SQLMCPClient:
    """Calls the read-only NL2SQL MCP tool for shared report data."""

    MAX_ATTEMPTS = 3

    def __init__(self, *, tool_caller: ToolCaller | None = None) -> None:
        self._tool_caller = tool_caller or self._call_tool

    def query(self, *, question: str) -> NL2SQLResult:
        """Run a read-only query, retrying transient or regenerated-query failures.

        The child MCP server asks the model to generate a fresh, shared-data
        SELECT statement for every call. A malformed generation or temporary
        transport failure is safe to retry because this tool cannot write data.
        """

        for attempt in range(self.MAX_ATTEMPTS):
            try:
                payload = self._tool_caller(question)
            except Exception as exc:
                if attempt + 1 == self.MAX_ATTEMPTS:
                    raise NL2SQLMCPClientError("广告报表查询暂时不可用，请稍后重试。") from exc
                continue

            if payload.get("ok") is True:
                answer = payload.get("answer")
                evidence = payload.get("evidence")
                if isinstance(answer, str) and isinstance(evidence, dict):
                    rows = payload.get("rows", [])
                    if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                        return NL2SQLResult(answer=answer, evidence=evidence, rows=rows)
                if attempt + 1 == self.MAX_ATTEMPTS:
                    raise NL2SQLMCPClientError("NL2SQL MCP 返回格式无效")
                continue

            message = str(payload.get("message") or "广告报表查询暂时不可用，请稍后重试。")
            error_code = payload.get("error_code")
            if error_code == "QUESTION_REQUIRED":
                raise NL2SQLError(message)
            if attempt + 1 == self.MAX_ATTEMPTS:
                if error_code == "QUERY_REJECTED":
                    raise NL2SQLError(message)
                raise NL2SQLMCPClientError(message)

        raise AssertionError("unreachable")

    def _call_tool(self, question: str) -> dict[str, Any]:
        try:
            return self._run(self._call_tool_async(question))
        except Exception as exc:
            raise NL2SQLMCPClientError("NL2SQL MCP 调用失败") from exc

    async def _call_tool_async(self, question: str) -> dict[str, Any]:
        environment = os.environ.copy()
        source_dir = str(Path(__file__).resolve().parents[1])
        existing_pythonpath = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            source_dir if not existing_pythonpath else f"{source_dir}{os.pathsep}{existing_pythonpath}"
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "amazon_ops.nl2sql_mcp"],
            env=environment,
            cwd=Path(__file__).resolve().parents[2],
        )
        async with Client(_StdioMCPTransport(parameters), raise_exceptions=True) as client:
            result = await client.call_tool(TOOL_NAME, {"question": question})
        structured = result.structured_content
        if not isinstance(structured, dict):
            raise NL2SQLMCPClientError("NL2SQL MCP 未返回结构化结果")
        return structured

    @staticmethod
    def _run(awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="nl2sql-mcp-client") as executor:
            return executor.submit(asyncio.run, awaitable).result()

"""Trusted client for the local Sif competitor-research MCP server."""

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


ToolCaller = Callable[[str, dict[str, Any]], dict[str, Any]]


class _StdioTransport:
    def __init__(self, parameters: StdioServerParameters) -> None:
        self.parameters, self.context = parameters, None

    async def __aenter__(self) -> Any:
        self.context = stdio_client(self.parameters)
        return await self.context.__aenter__()

    async def __aexit__(self, *args: Any) -> Any:
        return await self.context.__aexit__(*args) if self.context else None


class CompetitorResearchMCPClient:
    def __init__(self, *, tool_caller: ToolCaller | None = None) -> None:
        self._tool_caller = tool_caller or self._call_tool

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._tool_caller(tool, arguments)
        except Exception:
            return {"ok": False, "summary": {}, "evidence": [], "limitations": [], "errors": [{"code": "COMPETITOR_RESEARCH_MCP_UNAVAILABLE"}]}
        if not isinstance(result, dict):
            return {"ok": False, "summary": {}, "evidence": [], "limitations": [], "errors": [{"code": "COMPETITOR_RESEARCH_MCP_INVALID"}]}
        return result

    def _call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self._run(self._call_tool_async(tool, arguments))

    async def _call_tool_async(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        environment = os.environ.copy()
        source_dir = str(Path(__file__).resolve().parents[1])
        environment["PYTHONPATH"] = source_dir + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        parameters = StdioServerParameters(command=sys.executable, args=["-m", "amazon_ops.competitor_research_mcp"], env=environment, cwd=Path(__file__).resolve().parents[2])
        async with Client(_StdioTransport(parameters), raise_exceptions=True) as client:
            result = await client.call_tool(tool, arguments)
        return result.structured_content if isinstance(result.structured_content, dict) else {}

    @staticmethod
    def _run(awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="competitor-research-mcp") as executor:
            return executor.submit(asyncio.run, awaitable).result()

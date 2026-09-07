"""Internal MCP wrapper for shared imported-advertising-report queries.

The server uses stdio so it can be launched as a trusted child process by an
agent runtime.
"""
from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .llm import build_deepseek_llm
from .nl2sql import NL2SQLError, NL2SQLService


TOOL_NAME = "query_imported_advertising_report"
def create_nl2sql_mcp_server(*, service: NL2SQLService) -> MCPServer:
    """Create an MCP server for the shared, read-only advertising data set."""

    server = MCPServer(
        name="amazon-ops-nl2sql",
        title="Amazon Ops Imported Advertising Report Query",
        description="Read-only queries over the team-shared imported advertising reports.",
        instructions=(
            "Use this tool only for questions about imported advertising reports. "
            "It is read-only and returns at most 100 rows."
        ),
        version="0.1.0",
    )

    @server.tool(
        name=TOOL_NAME,
        title="Query imported advertising report",
        description=(
            "Answer a read-only advertising-report question over shared team data. "
            "Do not use it for writes, account changes, or data outside imported advertising reports."
        ),
        structured_output=True,
    )
    def query_imported_advertising_report(question: str) -> dict[str, Any]:
        """Run a bounded shared-data query."""

        normalized_question = question.strip()
        if not normalized_question:
            return {"ok": False, "error_code": "QUESTION_REQUIRED", "message": "请提供广告报表查询问题。"}
        try:
            result = service.query(question=normalized_question)
        except NL2SQLError as exc:
            return {"ok": False, "error_code": "QUERY_REJECTED", "message": str(exc)}
        except Exception:
            return {
                "ok": False,
                "error_code": "QUERY_UNAVAILABLE",
                "message": "广告报表查询暂时不可用，请稍后重试。",
            }
        return {
            "ok": True,
            "answer": result.answer,
            "evidence": result.evidence,
            "rows": result.rows,
        }

    return server


def main() -> None:
    """Run the shared-data local MCP server over stdio."""
    service = NL2SQLService.from_env(build_deepseek_llm())
    if service is None:
        raise SystemExit("DATABASE_READONLY_URL is required")
    create_nl2sql_mcp_server(service=service).run("stdio")


if __name__ == "__main__":
    main()

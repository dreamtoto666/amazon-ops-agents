import asyncio

from amazon_ops.nl2sql import NL2SQLResult, NL2SQLService
from amazon_ops.nl2sql_mcp import TOOL_NAME, create_nl2sql_mcp_server


class StubNL2SQL:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def query(self, *, question: str) -> NL2SQLResult:
        self.calls.append(question)
        return NL2SQLResult(answer="查询完成。", evidence={"rows": 1})


def test_nl2sql_mcp_exposes_a_shared_query_tool():
    service = StubNL2SQL()
    server = create_nl2sql_mcp_server(service=service)

    tools = asyncio.run(server.list_tools())
    result = asyncio.run(server.call_tool(TOOL_NAME, {"question": "查询广告花费"}))

    assert [tool.name for tool in tools] == [TOOL_NAME]
    assert service.calls == ["查询广告花费"]
    assert result.structured_content == {
        "ok": True,
        "answer": "查询完成。",
        "evidence": {"rows": 1},
        "rows": [],
    }


def test_nl2sql_mcp_does_not_accept_an_empty_question():
    server = create_nl2sql_mcp_server(service=StubNL2SQL())

    result = asyncio.run(server.call_tool(TOOL_NAME, {"question": "  "}))

    assert result.structured_content == {
        "ok": False,
        "error_code": "QUESTION_REQUIRED",
        "message": "请提供广告报表查询问题。",
    }


def test_nl2sql_prompt_contains_controlled_business_descriptions():
    prompt = NL2SQLService._prompt()

    assert "Profile ID" in prompt
    assert "不是 ERP sid" in prompt
    assert "团队共享数据" in prompt

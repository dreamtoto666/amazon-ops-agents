import pytest

from amazon_ops.nl2sql import NL2SQLError, NL2SQLService
from amazon_ops.nl2sql_mcp_client import NL2SQLMCPClient, NL2SQLMCPClientError


def test_nl2sql_mcp_client_returns_structured_tool_result_for_shared_data():
    calls: list[str] = []

    def tool_caller(question: str):
        calls.append(question)
        return {"ok": True, "answer": "查询完成。", "evidence": {"rows": 1}}

    result = NL2SQLMCPClient(tool_caller=tool_caller).query(
        question="查询广告花费"
    )

    assert calls == ["查询广告花费"]
    assert result.answer == "查询完成。"
    assert result.evidence == {"rows": 1}


def test_nl2sql_mcp_client_preserves_query_rejections():
    calls = 0

    def tool_caller(question: str):
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "error_code": "QUERY_REJECTED",
            "message": "只允许单条 SELECT 查询",
        }

    client = NL2SQLMCPClient(
        tool_caller=tool_caller
    )

    with pytest.raises(NL2SQLError, match="只允许单条 SELECT 查询"):
        client.query(question="删除广告数据")
    assert calls == 3


def test_nl2sql_mcp_client_hides_internal_tool_failures():
    calls = 0

    def tool_caller(question: str):
        nonlocal calls
        calls += 1
        return {
            "ok": False,
            "error_code": "QUERY_UNAVAILABLE",
            "message": "广告报表查询暂时不可用，请稍后重试。",
        }

    client = NL2SQLMCPClient(
        tool_caller=tool_caller
    )

    with pytest.raises(NL2SQLMCPClientError, match="暂时不可用"):
        client.query(question="查询广告花费")
    assert calls == 3


def test_nl2sql_mcp_client_recovers_when_a_later_attempt_succeeds():
    calls = 0

    def tool_caller(question: str):
        nonlocal calls
        calls += 1
        if calls < 3:
            return {
                "ok": False,
                "error_code": "QUERY_REJECTED",
                "message": "查询包含未授权数据表",
            }
        return {"ok": True, "answer": "查询完成。", "evidence": {"rows": 1}}

    result = NL2SQLMCPClient(tool_caller=tool_caller).query(
        question="查询效果不好的广告"
    )

    assert calls == 3
    assert result.answer == "查询完成。"


def test_nl2sql_mcp_client_preserves_all_returned_rows():
    rows = [{"campaign_id": f"campaign-{index}"} for index in range(250)]
    client = NL2SQLMCPClient(
        tool_caller=lambda question: {
            "ok": True,
            "answer": "查询完成。",
            "evidence": {"rows": 250},
            "rows": rows,
        }
    )

    result = client.query(question="列出广告")

    assert result.rows == rows


def test_nl2sql_validation_does_not_apply_a_result_limit():
    service = NL2SQLService(llm=None, readonly_url="postgresql://unused")

    normalized = service._validate(
        "SELECT campaign_id FROM shared_imported_advertising_report_rows LIMIT 100000"
    )

    assert normalized.endswith("LIMIT 100000")


def test_nl2sql_validation_rejects_legacy_owner_filtering():
    service = NL2SQLService(llm=None, readonly_url="postgresql://unused")

    with pytest.raises(NL2SQLError, match="不支持按上传用户筛选"):
        service._validate(
            "SELECT campaign_id FROM shared_imported_advertising_report_rows "
            "WHERE owner_id = 'owner-a'"
        )

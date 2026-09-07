from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from amazon_ops.advertising.report_specialist import (
    AD_REPORT_QUERY_FAILED_RESPONSE,
    REACT_TOOL_NOT_CALLED_RESPONSE,
    ImportedAdvertisingReportSpecialist,
)
from amazon_ops.events import InMemoryEventHub, StageController, StageName, StageReporter
from amazon_ops.models import AgentTask, QueryScope, SpecialistName
from amazon_ops.nl2sql import NL2SQLResult


class StubNL2SQL:
    def query(self, *, question: str) -> NL2SQLResult:
        assert question == "查询广告花费"
        return NL2SQLResult(
            answer="查询完成，共返回 1 行。",
            evidence={"rows": 1, "source": "shared_imported_advertising_report_rows"},
            rows=[{"campaign_id": "campaign-1", "spend": "12.34"}],
        )


class ToolCapableFakeChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        # FakeMessagesListChatModel is a Pydantic model; stash kwargs via
        # object.__setattr__ to bypass field validation.
        current = getattr(self, "_bind_kwargs", None)
        if current is None:
            current = []
            object.__setattr__(self, "_bind_kwargs", current)
        current.append(kwargs)
        return self


class FailingNL2SQL:
    def query(self, *, question: str) -> NL2SQLResult:
        raise RuntimeError("MCP unavailable")


def test_imported_advertising_report_specialist_returns_query_evidence():
    react_model = ToolCapableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_imported_advertising_report",
                        "args": {"question": "查询广告花费"},
                        "id": "tool-1",
                    }
                ],
            ),
            AIMessage(content="广告花费查询完成，共返回 1 行。"),
        ]
    )
    specialist = ImportedAdvertisingReportSpecialist(
        lambda: StubNL2SQL(), lambda: react_model
    )
    task = AgentTask(
        task_id="task-1",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    result = specialist.invoke(task, QueryScope(), {"owner_id": "owner-a"})

    assert result.status == "completed"
    assert result.summary == "广告花费查询完成，共返回 1 行。"
    assert result.deliverables == [
        {
            "type": "query_evidence",
            "rows": 1,
            "source": "shared_imported_advertising_report_rows",
        }
    ]


def test_imported_advertising_report_specialist_uses_fixed_reply_when_model_skips_tool():
    specialist = ImportedAdvertisingReportSpecialist(
        lambda: StubNL2SQL(),
        lambda: ToolCapableFakeChatModel(responses=[AIMessage(content="直接回答")]),
    )
    task = AgentTask(
        task_id="task-2",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    result = specialist.invoke(task, QueryScope(), {"owner_id": "owner-a"})

    assert result.status == "failed"
    assert result.summary == REACT_TOOL_NOT_CALLED_RESPONSE
    assert result.errors == [{"code": "ADVERTISING_REACT_TOOL_NOT_CALLED"}]


def test_imported_advertising_report_specialist_uses_fixed_reply_when_tool_fails():
    react_model = ToolCapableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_imported_advertising_report",
                        "args": {"question": "查询广告花费"},
                        "id": "tool-2",
                    }
                ],
            ),
            AIMessage(content="工具失败"),
        ]
    )
    specialist = ImportedAdvertisingReportSpecialist(lambda: FailingNL2SQL(), lambda: react_model)
    task = AgentTask(
        task_id="task-3",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    result = specialist.invoke(task, QueryScope(), {"owner_id": "owner-a"})

    assert result.status == "failed"
    assert result.summary == AD_REPORT_QUERY_FAILED_RESPONSE
    assert result.errors == [{"code": "AD_REPORT_QUERY_FAILED"}]


def test_advertising_specialist_never_forces_tool_choice():
    react_model = ToolCapableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_imported_advertising_report",
                        "args": {"question": "查询广告花费"},
                        "id": "tool-tc",
                    }
                ],
            ),
            AIMessage(content="完成。"),
        ]
    )
    specialist = ImportedAdvertisingReportSpecialist(
        lambda: StubNL2SQL(), lambda: react_model
    )
    task = AgentTask(
        task_id="task-tc",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    result = specialist.invoke(task, QueryScope(), {"owner_id": "owner-a"})

    assert result.status == "completed"
    assert len(react_model._bind_kwargs) >= 1
    assert all("tool_choice" not in kw for kw in react_model._bind_kwargs)


def test_advertising_specialist_forwards_reasoning_delta():
    hub = InMemoryEventHub()
    stages = StageController(hub)
    stages.start("reasoning-run", StageName.ANALYSIS)
    react_model = ToolCapableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                additional_kwargs={"reasoning_content": "我正在分析广告花费数据。"},
                tool_calls=[
                    {
                        "name": "query_imported_advertising_report",
                        "args": {"question": "查询广告花费"},
                        "id": "tool-reason",
                    }
                ],
            ),
            AIMessage(content="广告花费查询完成。"),
        ]
    )
    specialist = ImportedAdvertisingReportSpecialist(
        lambda: StubNL2SQL(), lambda: react_model
    )
    task = AgentTask(
        task_id="task-reason",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    specialist.invoke(
        task,
        QueryScope(),
        {
            "owner_id": "owner-a",
            "_stage_reporter": StageReporter(
                controller=stages,
                run_id="reasoning-run",
                stage=StageName.ANALYSIS,
            ),
        },
    )

    events = hub.events_after("reasoning-run")
    kinds = [item.data.get("kind") for item in events if item.data.get("kind")]
    assert "reasoning.delta" in kinds
    delta = next(item for item in events if item.data.get("kind") == "reasoning.delta")
    assert "分析广告花费" in delta.data["text"]


def test_advertising_specialist_emits_sanitized_tool_audit_events():
    hub = InMemoryEventHub()
    stages = StageController(hub)
    stages.start("audit-run", StageName.ANALYSIS)
    react_model = ToolCapableFakeChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_imported_advertising_report",
                        "args": {"question": "查询广告花费"},
                        "id": "tool-audit",
                    }
                ],
            ),
            AIMessage(content="广告花费查询完成。"),
        ]
    )
    specialist = ImportedAdvertisingReportSpecialist(lambda: StubNL2SQL(), lambda: react_model)
    task = AgentTask(
        task_id="task-audit",
        agent=SpecialistName.ADVERTISING,
        objective="查询广告花费",
        reason="advertising/query",
    )

    specialist.invoke(
        task,
        QueryScope(),
        {
            "owner_id": "owner-a",
            "_stage_reporter": StageReporter(
                controller=stages,
                run_id="audit-run",
                stage=StageName.ANALYSIS,
            ),
        },
    )

    events = hub.events_after("audit-run")
    kinds = [item.data.get("kind") for item in events if item.data.get("kind")]
    completed = next(item for item in events if item.data.get("kind") == "tool.call.completed")
    assert kinds == ["tool.call.started", "tool.call.completed", "response.delta", "response.completed"]
    assert completed.data["result"]["rows"][0]["campaign_id"] == "campaign-1"
    assert "owner_id" not in str(completed.data)

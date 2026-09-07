from __future__ import annotations

import io
from datetime import date

from fastapi.testclient import TestClient
from openpyxl import Workbook

from amazon_ops.api import create_app
from amazon_ops.auth import AuthUser
from amazon_ops.idempotency import InMemoryIdempotencyRegistry
from amazon_ops.memory import InMemoryConversationStore


class StubRunManager:
    def __init__(self) -> None:
        self.memory = InMemoryConversationStore()
        self.roles = type("Roles", (), {"llm": None})()

    def health(self):
        return {"status": "ok"}


class StubAuthStore:
    def user_for_token(self, token):
        return AuthUser("test-user", None, "operator", True, "operator") if token == "test-token" else None

    def close(self) -> None:
        return None


def european_file() -> tuple[str, bytes, str]:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    sheet.append(["发货日期", "发货人", "FBA号", "渠道", "国家", "箱数", "总重KG", "产品名称", "数量", "报关单价", "重量"])
    sheet.append([date(2026, 9, 1), "甲", "FBA-A", "华贸海运", "红福德国", 1, 2, "挡水条1m", 2, 3, 1])
    output = io.BytesIO()
    workbook.save(output)
    return "欧洲货件.xlsx", output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def client() -> TestClient:
    return TestClient(
        create_app(StubRunManager(), idempotency_registry=InMemoryIdempotencyRegistry(), auth_store=StubAuthStore()),
        headers={"Authorization": "Bearer test-token"},
    )


def test_european_customs_preview_and_generation() -> None:
    files = {"file": european_file()}
    api = client()

    preview = api.post("/api/european-customs-declarations/preview", files=files)
    generated = api.post("/api/european-customs-declarations/generate", files=files)

    assert preview.status_code == 200
    assert preview.json()["can_generate"] is True
    assert len(preview.json()["categories"]) == 24
    assert generated.status_code == 200
    assert generated.headers["content-type"] == "application/zip"

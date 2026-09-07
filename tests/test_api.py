from fastapi.testclient import TestClient

from amazon_ops.api import DEFAULT_DATABASE_URL, _env_float, _env_int, _env_text, create_app
from amazon_ops.auth import AuthUser
from amazon_ops.auth import normalize_username
from amazon_ops.idempotency import InMemoryIdempotencyRegistry
from amazon_ops.memory import InMemoryConversationStore


class StubRunManager:
    def __init__(self):
        self.submit_calls = 0
        self.memory = InMemoryConversationStore()
        self.roles = type(
            "Roles",
            (),
            {"llm": type("LLM", (), {"config": type("Config", (), {"model": "deepseek-v4-flash"})()})()},
        )()
        self.last_request = None

    def health(self):
        return {
            "status": "ok",
            "llm": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "configured": False,
            },
            "specialists": [],
            "mcp": {
                "seller_sprite_configured": True,
                "sif_configured": True,
            },
        }

    def submit(self, request, *, owner_id=None):
        self.last_request = request
        self.submit_calls += 1
        return "run-real-1"

    def get(self, run_id, *, owner_id=None):
        return None


class StubAuthStore:
    def user_for_token(self, token):
        return AuthUser("test-user", "operator@example.com", "operator", True) if token == "test-token" else None

    def close(self):
        return None


class StubAdminAuthStore(StubAuthStore):
    def user_for_token(self, token):
        return AuthUser("test-admin", "admin@example.com", "admin", True) if token == "test-token" else None

    def create_user(self, email=None, password="", role="operator", *, username=None):
        assert password == "secret6"
        return AuthUser("new-user", None, role, True, username)


def create_test_client(manager: StubRunManager) -> TestClient:
    return TestClient(
        create_app(
            manager,
            idempotency_registry=InMemoryIdempotencyRegistry(),
            auth_store=StubAuthStore(),
        )
        , headers={"Authorization": "Bearer test-token"}
    )


def test_empty_host_environment_overrides_use_safe_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("IDEMPOTENCY_TTL_SECONDS", "")
    monkeypatch.setenv("AUTH_DB_POOL_SIZE", "")
    monkeypatch.setenv("CONVERSATION_MEMORY_MAX_MESSAGES", "")

    assert _env_text("DATABASE_URL", DEFAULT_DATABASE_URL) == DEFAULT_DATABASE_URL
    assert _env_float("IDEMPOTENCY_TTL_SECONDS", 86400) == 86400
    assert _env_int("AUTH_DB_POOL_SIZE", 10) == 10
    assert _env_int("CONVERSATION_MEMORY_MAX_MESSAGES", 20) == 20




def test_vercel_uses_ephemeral_storage_for_backend_smoke_test(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")

    app = create_app(auth_store=StubAuthStore())

    assert app.state.idempotency_registry.health() == {"backend": "memory", "status": "ok"}
    assert app.state.advertising_run_manager.history_store.__class__.__name__ == "InMemoryAdvertisingRunHistoryStore"
    assert app.state.run_manager.memory.health() == {"backend": "memory", "status": "ok"}


def test_api_exposes_real_configuration_status_and_creates_run():
    manager = StubRunManager()
    client = create_test_client(manager)

    health = client.get("/api/health")
    created = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？"},
        headers={"Idempotency-Key": "chat-test-0001"},
    )

    assert health.status_code == 200
    assert health.json()["llm"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "configured": False,
    }
    assert created.status_code == 202
    assert created.json()["run_id"] == "run-real-1"
    assert created.json()["status"] == "accepted"
    assert created.json()["model"] == "deepseek-v4-flash"
    assert created.json()["conversation_id"].startswith("conversation-")
    assert created.headers["Idempotency-Replayed"] == "false"
    assert manager.submit_calls == 1


def test_api_accepts_the_pro_model_for_one_run():
    manager = StubRunManager()
    client = create_test_client(manager)

    response = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？", "model": "deepseek-v4-pro"},
        headers={"Idempotency-Key": "chat-pro-model-0001"},
    )

    assert response.status_code == 202
    assert response.json()["model"] == "deepseek-v4-pro"
    assert manager.last_request.model == "deepseek-v4-pro"


def test_api_accepts_images_only_with_the_vision_model():
    manager = StubRunManager()
    client = create_test_client(manager)
    image = "data:image/png;base64," + "a" * 32

    rejected = client.post(
        "/api/runs",
        json={
            "message": "分析图片",
            "model": "deepseek-v4-flash",
            "image_attachments": [{"data_url": image}],
        },
        headers={"Idempotency-Key": "chat-image-rejected-0001"},
    )
    accepted = client.post(
        "/api/runs",
        json={
            "message": "分析图片",
            "model": "deepseek-v4-flash-vision-exp",
            "image_attachments": [{"data_url": image}],
        },
        headers={"Idempotency-Key": "chat-image-accepted-0001"},
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 202
    assert manager.last_request.image_attachments[0].data_url == image


def test_api_rejects_an_unsupported_model_before_starting_agent():
    manager = StubRunManager()
    client = create_test_client(manager)

    response = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？", "model": "anything-else"},
        headers={"Idempotency-Key": "chat-invalid-model-0001"},
    )

    assert response.status_code == 422
    assert manager.submit_calls == 0


def test_api_accepts_reasoning_effort_for_one_run():
    manager = StubRunManager()
    client = create_test_client(manager)

    response = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？", "reasoning_effort": "high"},
        headers={"Idempotency-Key": "chat-effort-test-0001"},
    )

    assert response.status_code == 202
    assert manager.last_request.reasoning_effort == "high"


def test_api_rejects_an_unsupported_reasoning_effort():
    manager = StubRunManager()
    client = create_test_client(manager)

    response = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？", "reasoning_effort": "extreme"},
        headers={"Idempotency-Key": "chat-effort-invalid-0001"},
    )

    assert response.status_code == 422
    assert manager.submit_calls == 0


def test_api_rejects_empty_chat_message_before_starting_agent():
    client = create_test_client(StubRunManager())

    response = client.post(
        "/api/runs",
        json={"message": ""},
        headers={"Idempotency-Key": "chat-test-0002"},
    )

    assert response.status_code == 422


def test_api_lists_and_reads_only_the_authenticated_users_conversations():
    manager = StubRunManager()
    manager.memory.append("test-user", "conversation-a", role="user", content="查询 ACOS")
    manager.memory.append("another-user", "conversation-a", role="user", content="不应泄露")
    client = create_test_client(manager)

    summaries = client.get("/api/conversations")
    messages = client.get("/api/conversations/conversation-a/messages")

    assert summaries.status_code == 200
    assert summaries.json() == [{"conversation_id": "conversation-a", "preview": "查询 ACOS"}]
    assert messages.status_code == 200
    assert messages.json() == [{"role": "user", "content": "查询 ACOS", "kind": "message"}]


def test_api_deletes_only_the_authenticated_users_conversation():
    manager = StubRunManager()
    manager.memory.append("test-user", "conversation-a", role="user", content="删除我")
    manager.memory.append("another-user", "conversation-a", role="user", content="保留我")
    client = create_test_client(manager)

    response = client.delete("/api/conversations/conversation-a")

    assert response.status_code == 204
    assert manager.memory.history("test-user", "conversation-a") == []
    assert manager.memory.history("another-user", "conversation-a") == [
        {"role": "user", "content": "保留我", "kind": "message"}
    ]


def test_same_idempotency_key_replays_run_and_rejects_different_payload():
    manager = StubRunManager()
    client = create_test_client(manager)
    payload = {"message": "ACOS 是什么？"}
    headers = {"Idempotency-Key": "chat-retry-0001"}

    first = client.post("/api/runs", json=payload, headers=headers)
    replay = client.post("/api/runs", json=payload, headers=headers)
    conflict = client.post(
        "/api/runs",
        json={**payload, "message": "ACOS 是什么？（变更）"},
        headers=headers,
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["run_id"] == first.json()["run_id"]
    assert replay.json()["conversation_id"] == first.json()["conversation_id"]
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert manager.submit_calls == 1
    assert conflict.status_code == 409


def test_task_creation_requires_a_valid_idempotency_key():
    manager = StubRunManager()
    client = create_test_client(manager)

    missing = client.post("/api/runs", json={"message": "ACOS 是什么？"})
    invalid = client.post(
        "/api/runs",
        json={"message": "ACOS 是什么？"},
        headers={"Idempotency-Key": "short"},
    )

    assert missing.status_code == 422
    assert invalid.status_code == 422
    assert manager.submit_calls == 0


def test_business_api_rejects_unauthenticated_request():
    client = TestClient(create_app(StubRunManager(), idempotency_registry=InMemoryIdempotencyRegistry(), auth_store=StubAuthStore()))
    assert client.post("/api/runs", json={"message": "ACOS 是什么？"}, headers={"Idempotency-Key": "auth-test-0001"}).status_code == 401


def test_admin_can_create_an_account_directly():
    client = TestClient(create_app(StubRunManager(), idempotency_registry=InMemoryIdempotencyRegistry(), auth_store=StubAdminAuthStore()), headers={"Authorization": "Bearer test-token"})
    response = client.post("/api/auth/admin/users", json={"username": "operator1", "password": "secret6", "role": "operator"})
    assert response.status_code == 201
    assert response.json()["username"] == "operator1"


def test_username_login_identifiers_are_normalized_and_validated():
    assert normalize_username(" Ops_User-1 ") == "ops_user-1"

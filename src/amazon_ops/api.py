from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Literal, Mapping
from urllib.parse import quote
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .deepseek_runtime import DeepSeekModelRoles, build_deepseek_model_roles
from .llm import DeepSeekModelName, DeepSeekReasoningEffort
from .graph import build_controller_graph
from .interfaces import DeterministicAggregator
from .events import (
    TERMINAL_EVENT_TYPES,
    InMemoryEventHub,
    StageController,
    StageEventType,
    StageName,
)
from .idempotency import (
    IdempotencyConflictError,
    IdempotencyKeyError,
    IdempotencyRegistry,
    IdempotencyStorageError,
    InMemoryIdempotencyRegistry,
    PostgresIdempotencyRegistry,
    request_fingerprint,
)
from .memory import (
    ConversationMemoryStorageError,
    ConversationStore,
    InMemoryConversationStore,
    PostgresConversationStore,
    summarize_conversation,
)
from .nl2sql import NL2SQLError, NL2SQLService
from .nl2sql_mcp_client import NL2SQLMCPClient
from .advertising import (
    AdDiagnosticRequest,
    AdShop,
    AdvertisingRunManager,
    AdvertisingRunRecord,
    ImportedAdvertisingReportSpecialist,
)
from .models import SpecialistName
from .advertising.history import InMemoryAdvertisingRunHistoryStore, PostgresAdvertisingRunHistoryStore
from .advertising.imports import AdvertisingImportService, ImportDataSummary, ImportResult, InMemoryImportStore, PostgresImportStore, ReportType
from .sse import SSE_RESPONSE_HEADERS, stage_sse_stream
from .auth import AuthStore, AuthUser, bearer_token, require_admin, require_user
from .customs_declaration import CustomsDeclarationService, CustomsPreview
from .customs_declaration.service import CustomsGenerationError, MAX_FILE_SIZE
from .european_customs_declaration import EuropeanCustomsDeclarationService, EuropeanCustomsPreview
from .european_customs_declaration.service import EuropeanCustomsGenerationError


DEFAULT_DATABASE_URL = "postgresql://amazon_ops:amazon_ops@127.0.0.1:5432/amazon_ops"


def _env_text(name: str, default: str) -> str:
    """Return the default for empty values entered in managed-host UIs."""

    return os.getenv(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    return int(_env_text(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env_text(name, str(default)))


def _uses_serverless_test_storage() -> bool:
    """Vercel functions have no colocated PostgreSQL service or durable disk."""

    return os.getenv("VERCEL") == "1"


class ImageAttachment(BaseModel):
    """A small, in-request image used only by the selected vision model."""

    data_url: str = Field(min_length=32, max_length=7_000_000)

    @field_validator("data_url")
    @classmethod
    def validate_data_url(cls, value: str) -> str:
        allowed_prefixes = (
            "data:image/jpeg;base64,",
            "data:image/png;base64,",
            "data:image/webp;base64,",
            "data:image/gif;base64,",
        )
        if not value.startswith(allowed_prefixes):
            raise ValueError("仅支持 PNG、JPEG、WebP 或 GIF 图片。")
        return value


class CreateRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: str = Field(
        default_factory=lambda: f"conversation-{uuid4().hex}",
        min_length=8,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    user_context: dict[str, Any] = Field(default_factory=dict)
    shop_directory: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    model: DeepSeekModelName | None = None
    reasoning_effort: DeepSeekReasoningEffort | None = None
    image_attachments: list[ImageAttachment] = Field(default_factory=list, max_length=4)


class CreateRunResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: str = "accepted"
    model: DeepSeekModelName


class ConversationSummaryResponse(BaseModel):
    conversation_id: str
    preview: str


class ConversationMessageResponse(BaseModel):
    role: str
    content: str
    kind: Literal["message", "summary"] = "message"


class RunRecord(BaseModel):
    run_id: str
    status: str
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class CreateAdvertisingRunResponse(BaseModel):
    run_id: str
    trace_id: str
    status: str = "accepted"


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=1, max_length=1024)
    remember: bool = False


class PasswordRequest(BaseModel):
    password: str = Field(min_length=6, max_length=1024)


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class InviteRequest(EmailRequest):
    role: str = Field(pattern="^(admin|operator)$")


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    role: str = Field(pattern="^(admin|operator)$")
    password: str = Field(min_length=6, max_length=1024)


def user_payload(user: AuthUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "active": user.active,
    }


class AgentRunManager:
    def __init__(
        self,
        *,
        roles: DeepSeekModelRoles | None = None,
        memory: ConversationStore | None = None,
        max_workers: int = 4,
    ) -> None:
        self.roles = roles or build_deepseek_model_roles()
        self._roles_by_model: dict[
            tuple[DeepSeekModelName, DeepSeekReasoningEffort | None],
            DeepSeekModelRoles,
        ] = {
            (self.roles.llm.config.model, self.roles.llm.config.reasoning_effort): self.roles
        }
        self.memory = memory or InMemoryConversationStore()
        self.hub = InMemoryEventHub()
        self.stages = StageController(self.hub)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="amazon-ops-run"
        )
        self._lock = RLock()
        self._runs: dict[str, RunRecord] = {}
        self._owners: dict[str, str | None] = {}
        self.advertising_imports: AdvertisingImportService | None = None
        self.nl2sql: Any | None = None
        self.specialists: dict[str, Any] = self._specialists_for(self.roles)
        self.graph = self._graph_for(self.roles)

    def _specialists_for(self, roles: DeepSeekModelRoles) -> dict[str, Any]:
        return {
            SpecialistName.ADVERTISING.value: ImportedAdvertisingReportSpecialist(
                lambda: self.nl2sql,
                lambda: roles.advertising_react_model,
            )
        }

    def _graph_for(self, roles: DeepSeekModelRoles) -> Any:
        return build_controller_graph(
            interpreter=roles.request_interpreter,
            specialists=self._specialists_for(roles),
            aggregator=DeterministicAggregator(),
            responder=roles.direct_responder,
            stages=self.stages,
        )

    def _roles_for(
        self,
        model: DeepSeekModelName,
        reasoning_effort: DeepSeekReasoningEffort | None = None,
    ) -> DeepSeekModelRoles:
        key = (model, reasoning_effort)
        with self._lock:
            selected = self._roles_by_model.get(key)
            if selected is None:
                selected = build_deepseek_model_roles(
                    model=model, reasoning_effort=reasoning_effort
                )
                self._roles_by_model[key] = selected
            return selected

    def submit(self, request: CreateRunRequest, *, owner_id: str | None = None) -> str:
        selected_model = request.model or self.roles.llm.config.model
        if request.image_attachments and selected_model != "deepseek-v4-flash-vision-exp":
            raise ValueError("图片分析请使用 Vision（看图）模型。")
        selected_effort = request.reasoning_effort
        roles = self._roles_for(selected_model, selected_effort)
        graph = self.graph if roles is self.roles else self._graph_for(roles)
        run_id = f"run-{uuid4().hex}"
        record = RunRecord(run_id=run_id, status="running")
        with self._lock:
            self._runs[run_id] = record
            self._owners[run_id] = owner_id
        previous_messages = self.memory.history(owner_id, request.conversation_id)
        current_message = {"role": "user", "content": request.message}
        self.memory.append(
            owner_id, request.conversation_id, role="user", content=request.message, turn_id=run_id
        )
        state = {
            "request_id": run_id,
            "owner_id": owner_id,
            "messages": [*previous_messages, current_message],
            "user_context": request.user_context,
            "image_attachments": [item.data_url for item in request.image_attachments],
            "shop_directory": request.shop_directory,
            "system_capabilities": {
                "llm": {
                    "provider": "deepseek",
                    "model": selected_model,
                    "configured": bool(
                        os.getenv(self.roles.llm.config.api_key_env, "").strip()
                    ),
                },
                "mcp": {
                    "seller_sprite": {
                        "configured": bool(
                            os.getenv("SELLER_SPRITE_MCP_SECRET", "").strip()
                        ),
                        "purpose": "关键词挖掘、竞品关键词与流量证据",
                    },
                    "sif": {
                        "configured": bool(os.getenv("SIF_MCP_SECRET", "").strip()),
                        "purpose": "关键词、流量和 Listing 关键词分布验证",
                    },
                    "imported_advertising_reports": {
                        "configured": self.nl2sql is not None,
                        "purpose": "团队共享已导入广告报表的只读查询",
                    },
                },
                "agents": {
                    name: {"specialist_registered": True}
                    for name in sorted(self.specialists)
                },
            },
            "current_time": datetime.now(timezone.utc).isoformat(),
        }
        self._executor.submit(self._execute, run_id, request.conversation_id, state, owner_id, graph, roles)
        return run_id

    def get(self, run_id: str, *, owner_id: str | None = None) -> RunRecord | None:
        with self._lock:
            item = self._runs.get(run_id)
            return item.model_copy(deep=True) if item and self._owners.get(run_id) == owner_id else None

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "llm": {
                "provider": "deepseek",
                "model": self.roles.llm.config.model,
                "configured": bool(os.getenv(self.roles.llm.config.api_key_env, "").strip()),
            },
            "specialists": sorted(self.specialists),
            "mcp": {
                "seller_sprite_configured": bool(
                    os.getenv("SELLER_SPRITE_MCP_SECRET", "").strip()
                ),
                "sif_configured": bool(os.getenv("SIF_MCP_SECRET", "").strip()),
                "lingxing_configured": bool(
                    os.getenv("LINGXING_MCP_SECRET", "").strip()
                ),
            },
            "memory": {
                "type": self.memory.health()["backend"],
                "max_turns": self.memory.max_turns,
                "conversation_count": self.memory.count(),
            },
        }

    def _execute(
        self,
        run_id: str,
        conversation_id: str,
        state: dict[str, Any],
        owner_id: str | None,
        graph: Any,
        roles: DeepSeekModelRoles,
    ) -> None:
        try:
            result = graph.invoke(state)
            final_response = result.get("final_response") or {}
            answer = final_response.get("answer")
            if isinstance(answer, str):
                self.memory.append(
                    owner_id, conversation_id, role="assistant", content=answer, turn_id=run_id
                )
                try:
                    self.memory.compact(
                        owner_id,
                        conversation_id,
                        lambda previous, messages: summarize_conversation(
                            roles.llm, previous, messages
                        ),
                    )
                except ConversationMemoryStorageError:
                    # A completed answer remains valid even when best-effort
                    # background compaction is temporarily unavailable.
                    pass
            record = RunRecord(
                run_id=run_id,
                status=(
                    "waiting"
                    if result.get("route") in {"clarify", "approval"}
                    or result.get("clarification_question")
                    else "completed"
                ),
                result=final_response,
            )
        except Exception as exc:
            events = self.hub.events_after(run_id)
            terminal = any(StageEventType(item.event) in TERMINAL_EVENT_TYPES for item in events)
            if not terminal:
                self.stages.fail(
                    run_id,
                    StageName.UNDERSTANDING,
                    self._safe_error_message(exc),
                    code=getattr(exc, "code", "AGENT_RUN_FAILED"),
                )
            record = RunRecord(
                run_id=run_id,
                status="failed",
                error={
                    "code": getattr(exc, "code", "AGENT_RUN_FAILED"),
                    "message": self._safe_error_message(exc),
                },
            )
        with self._lock:
            self._runs[run_id] = record

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        if code == "DEEPSEEK_API_KEY_MISSING":
            return "DeepSeek API Key 尚未配置。"
        if code == "DEEPSEEK_AUTH_FAILED":
            return "DeepSeek API Key 无效或无权限。"
        if code == "DEEPSEEK_RATE_LIMITED":
            return "DeepSeek 请求过于频繁，请稍后重试。"
        if code in {
            "DEEPSEEK_CONNECTION_FAILED",
            "DEEPSEEK_OUTPUT_TRUNCATED",
            "DEEPSEEK_EMPTY_OUTPUT",
        }:
            return str(exc)
        return "Agent 运行失败，请查看服务端日志。"


def create_app(
    manager: AgentRunManager | None = None,
    advertising_manager: AdvertisingRunManager | None = None,
    idempotency_registry: IdempotencyRegistry | None = None,
    auth_store: AuthStore | None = None,
    advertising_import_service: AdvertisingImportService | None = None,
    customs_declaration_service: CustomsDeclarationService | None = None,
    european_customs_declaration_service: EuropeanCustomsDeclarationService | None = None,
) -> FastAPI:
    serverless_test_storage = _uses_serverless_test_storage()
    database_url = _env_text("DATABASE_URL", DEFAULT_DATABASE_URL)
    runtime = manager or AgentRunManager(
        memory=(
            InMemoryConversationStore(
                max_turns=_env_int("CONVERSATION_MEMORY_MAX_TURNS", 30),
                compact_turns=_env_int("CONVERSATION_MEMORY_COMPACT_TURNS", 20),
            )
            if serverless_test_storage
            else PostgresConversationStore(
                database_url,
                max_turns=_env_int("CONVERSATION_MEMORY_MAX_TURNS", 30),
                compact_turns=_env_int("CONVERSATION_MEMORY_COMPACT_TURNS", 20),
                max_pool_size=_env_int("CONVERSATION_MEMORY_DB_POOL_SIZE", 10),
            )
        )
    )
    shared_llm = getattr(getattr(runtime, "roles", None), "llm", None)
    advertising_runtime = advertising_manager or AdvertisingRunManager(
        llm=shared_llm,
        history_store=(
            InMemoryAdvertisingRunHistoryStore()
            if serverless_test_storage
            else PostgresAdvertisingRunHistoryStore(
                database_url,
                max_pool_size=_env_int("AD_DIAGNOSTIC_HISTORY_DB_POOL_SIZE", 10),
            )
        ),
    )
    idempotency = idempotency_registry or (
        InMemoryIdempotencyRegistry()
        if serverless_test_storage
        else PostgresIdempotencyRegistry(
            database_url,
            ttl_seconds=_env_float("IDEMPOTENCY_TTL_SECONDS", 86400),
            max_pool_size=_env_int("IDEMPOTENCY_DB_POOL_SIZE", 10),
        )
    )
    auth = auth_store or AuthStore(
        database_url, max_pool_size=_env_int("AUTH_DB_POOL_SIZE", 10)
    )
    advertising_imports = advertising_import_service or AdvertisingImportService(
        InMemoryImportStore() if serverless_test_storage else PostgresImportStore(database_url)
    )
    customs_declarations = customs_declaration_service or CustomsDeclarationService()
    european_customs_declarations = (
        european_customs_declaration_service or EuropeanCustomsDeclarationService()
    )
    runtime.advertising_imports = advertising_imports
    runtime.nl2sql = (
        NL2SQLMCPClient()
        if shared_llm and NL2SQLService.from_env(shared_llm) is not None
        else None
    )
    app = FastAPI(title="Amazon Ops Agent API", version="0.1.0")
    app.state.run_manager = runtime
    app.state.advertising_run_manager = advertising_runtime
    app.state.idempotency_registry = idempotency
    app.state.auth_store = auth
    app.state.advertising_import_service = advertising_imports
    app.state.customs_declaration_service = customs_declarations
    app.state.european_customs_declaration_service = european_customs_declarations
    start_advertising_recovery = getattr(advertising_runtime, "start_recovery_monitor", None)
    if callable(start_advertising_recovery) and not serverless_test_storage:
        # Durable tasks retain their original identifiers; recovery only claims
        # stale records protected by the database lease.
        app.router.add_event_handler("startup", start_advertising_recovery)
    close_idempotency = getattr(idempotency, "close", None)
    if callable(close_idempotency):
        app.router.add_event_handler("shutdown", close_idempotency)
    close_memory = getattr(getattr(runtime, "memory", None), "close", None)
    if callable(close_memory):
        app.router.add_event_handler("shutdown", close_memory)
    close_ad_history = getattr(getattr(advertising_runtime, "history_store", None), "close", None)
    if callable(close_ad_history):
        app.router.add_event_handler("shutdown", close_ad_history)
    close_advertising = getattr(advertising_runtime, "close", None)
    if callable(close_advertising):
        app.router.add_event_handler("shutdown", close_advertising)
    app.router.add_event_handler("shutdown", auth.close)
    app.router.add_event_handler("shutdown", advertising_imports.store.close)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3001", "http://127.0.0.1:3001"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "Idempotency-Key"],
        expose_headers=["Idempotency-Key", "Idempotency-Replayed"],
    )

    @app.middleware("http")
    async def authenticate_api_requests(request: Request, call_next):
        public_auth_paths = {
            "/api/auth/login",
            "/api/auth/password-reset",
        }
        is_public_auth_path = (
            request.url.path in public_auth_paths
            or request.url.path.startswith("/api/auth/password-reset/")
            or request.url.path.startswith("/api/auth/invitations/")
        )
        if (
            request.url.path.startswith("/api/")
            and request.url.path != "/api/health"
            and not is_public_auth_path
        ):
            token = bearer_token(request)
            user = auth.user_for_token(token) if token else None
            if not user:
                return Response(content='{"detail":"请先登录"}', status_code=401, media_type="application/json")
            request.state.auth_user = user
        return await call_next(request)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            payload = runtime.health()
        except ConversationMemoryStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL 会话记忆存储不可用。",
            ) from exc
        try:
            payload["idempotency"] = idempotency.health()
        except IdempotencyStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL 幂等存储不可用。",
            ) from exc
        return payload

    @app.post("/api/auth/login")
    def login(request: LoginRequest, raw_request: Request) -> dict[str, Any]:
        try:
            user, token, expires_at = auth.login(
                request.username, request.password, request.remember,
                raw_request.client.host if raw_request.client else "unknown",
            )
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {"user": user_payload(user), "session_token": token, "expires_at": expires_at.isoformat()}

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(raw_request: Request) -> Response:
        token = bearer_token(raw_request)
        if token: auth.revoke_session(token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/auth/me")
    def me(raw_request: Request) -> dict[str, Any]:
        token = bearer_token(raw_request); user = auth.user_for_token(token) if token else None
        if not user: raise HTTPException(status_code=401, detail="请先登录")
        return user_payload(user)

    @app.post("/api/auth/invitations/{token}/accept")
    def accept_invitation(token: str, request: PasswordRequest) -> dict[str, Any]:
        raise HTTPException(status_code=410, detail="邮件邀请已关闭，请联系管理员创建用户名账号")

    @app.post("/api/auth/password-reset")
    def request_password_reset(request: EmailRequest) -> dict[str, str]:
        raise HTTPException(status_code=410, detail="当前仅支持管理员创建用户名和密码，邮件找回已关闭")

    @app.post("/api/auth/password-reset/{token}/confirm", status_code=status.HTTP_204_NO_CONTENT)
    def confirm_password_reset(token: str, request: PasswordRequest) -> Response:
        raise HTTPException(status_code=410, detail="邮件重置密码已关闭")

    @app.get("/api/auth/admin/users")
    def admin_users(raw_request: Request) -> list[dict[str, Any]]:
        require_admin(raw_request); return [user_payload(user) for user in auth.list_users()]

    @app.post("/api/auth/admin/users", status_code=status.HTTP_201_CREATED)
    def create_user(request: CreateUserRequest, raw_request: Request) -> dict[str, Any]:
        require_admin(raw_request)
        try:
            user = auth.create_user(password=request.password, role=request.role, username=request.username)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return user_payload(user)

    @app.post("/api/auth/admin/invitations", status_code=status.HTTP_201_CREATED)
    def create_invitation(request: InviteRequest, raw_request: Request) -> dict[str, str]:
        require_admin(raw_request)
        raise HTTPException(status_code=410, detail="邮件邀请已关闭，请直接创建用户名和密码")

    @app.post("/api/auth/admin/users/{user_id}/deactivate", status_code=status.HTTP_204_NO_CONTENT)
    def deactivate_user(user_id: str, raw_request: Request) -> Response:
        user = require_admin(raw_request)
        if user.id == user_id: raise HTTPException(status_code=400, detail="不能停用当前管理员账号")
        auth.deactivate(user_id); return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/api/auth/admin/users/{user_id}/password-reset")
    def admin_password_reset(user_id: str, raw_request: Request) -> dict[str, str]:
        require_admin(raw_request)
        raise HTTPException(status_code=410, detail="邮件重置密码已关闭")

    @app.post(
        "/api/runs",
        response_model=CreateRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_run(
        request: CreateRunRequest,
        response: Response,
        raw_request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> CreateRunResponse:
        user = require_user(raw_request)
        if (
            request.image_attachments
            and (request.model or runtime.roles.llm.config.model)
            != "deepseek-v4-flash-vision-exp"
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="图片分析请使用 Vision（看图）模型。",
            )
        fingerprint_payload = request.model_dump(mode="json")
        if "conversation_id" not in request.model_fields_set:
            fingerprint_payload["conversation_id"] = "__server_generated__"
        try:
            resolution = idempotency.resolve(
                namespace=f"agent-runs:{user.id}",
                key=idempotency_key,
                fingerprint=request_fingerprint(fingerprint_payload),
                factory=lambda: CreateRunResponse(
                    run_id=runtime.submit(request, owner_id=user.id),
                    conversation_id=request.conversation_id,
                    model=request.model or runtime.roles.llm.config.model,
                ),
            )
        except IdempotencyKeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该幂等键已经用于不同的任务请求。",
            ) from exc
        except IdempotencyStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="幂等存储暂时不可用，请稍后使用同一个幂等键重试。",
            ) from exc
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["Idempotency-Replayed"] = str(resolution.replayed).lower()
        return CreateRunResponse.model_validate(resolution.value)

    @app.get("/api/runs/{run_id}", response_model=RunRecord)
    def get_run(run_id: str, raw_request: Request) -> RunRecord:
        record = runtime.get(run_id, owner_id=require_user(raw_request).id)
        if record is None:
            raise HTTPException(status_code=404, detail="run not found")
        return record

    @app.get("/api/conversations", response_model=list[ConversationSummaryResponse])
    def list_conversations(raw_request: Request) -> list[ConversationSummaryResponse]:
        try:
            return [
                ConversationSummaryResponse.model_validate(item)
                for item in runtime.memory.conversations(require_user(raw_request).id)
            ]
        except ConversationMemoryStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL 会话记忆存储不可用。",
            ) from exc

    @app.get(
        "/api/conversations/{conversation_id}/messages",
        response_model=list[ConversationMessageResponse],
    )
    def get_conversation_messages(
        conversation_id: str, raw_request: Request
    ) -> list[ConversationMessageResponse]:
        try:
            return [
                ConversationMessageResponse.model_validate(item)
                for item in runtime.memory.history(require_user(raw_request).id, conversation_id)
            ]
        except ConversationMemoryStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL 会话记忆存储不可用。",
            ) from exc

    @app.delete("/api/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_conversation(conversation_id: str, raw_request: Request) -> Response:
        try:
            runtime.memory.clear(require_user(raw_request).id, conversation_id)
        except ConversationMemoryStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL 会话记忆存储不可用。",
            ) from exc
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/api/runs/{run_id}/events")
    async def run_events(
        run_id: str,
        raw_request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if runtime.get(run_id, owner_id=require_user(raw_request).id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return StreamingResponse(
            stage_sse_stream(
                runtime.hub,
                run_id,
                last_event_id=last_event_id,
            ),
            headers=SSE_RESPONSE_HEADERS,
            media_type="text/event-stream",
        )

    @app.get("/api/ad-diagnostics/shops", response_model=list[AdShop])
    def advertising_shops() -> list[AdShop]:
        try:
            return advertising_runtime.list_shops()
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=advertising_runtime._safe_error_message(exc),
            ) from exc

    @app.get("/api/ad-diagnostics/selection-directory")
    def advertising_selection_directory(raw_request: Request) -> dict[str, Any]:
        user = require_user(raw_request)
        try:
            return advertising_runtime.selection_directory(owner_id=user.id)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=advertising_runtime._safe_error_message(exc),
            ) from exc

    @app.post(
        "/api/ad-diagnostics/runs",
        response_model=CreateAdvertisingRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def create_advertising_run(
        request: AdDiagnosticRequest,
        response: Response,
        raw_request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key"),
    ) -> CreateAdvertisingRunResponse:
        user = require_user(raw_request)
        try:
            request, execution_scope = advertising_runtime.resolve_selection(request, owner_id=user.id)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="所选诊断范围无权限。") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        def submit_run() -> CreateAdvertisingRunResponse:
            owner_id = user.id
            run_id = advertising_runtime.submit(request, owner_id=owner_id, execution_scope=execution_scope)
            record = advertising_runtime.get(run_id, owner_id=owner_id)
            if record is None:
                raise RuntimeError("广告巡检任务未能初始化")
            return CreateAdvertisingRunResponse(
                run_id=run_id,
                trace_id=record.trace_id,
            )

        try:
            resolution = idempotency.resolve(
                namespace=f"advertising-runs:{user.id}",
                key=idempotency_key,
                fingerprint=request_fingerprint(request),
                factory=submit_run,
            )
        except IdempotencyKeyError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该幂等键已经用于不同的广告巡检请求。",
            ) from exc
        except IdempotencyStorageError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="幂等存储暂时不可用，请稍后使用同一个幂等键重试。",
            ) from exc
        response.headers["Idempotency-Key"] = idempotency_key
        response.headers["Idempotency-Replayed"] = str(resolution.replayed).lower()
        return CreateAdvertisingRunResponse.model_validate(resolution.value)

    @app.get("/api/ad-report-imports/templates/{report_type}")
    def advertising_import_template(report_type: ReportType, raw_request: Request) -> Response:
        require_user(raw_request)
        return Response(content=advertising_imports.template(report_type), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{report_type.value}-template.csv"'})

    @app.post("/api/ad-report-imports", response_model=ImportResult)
    async def import_advertising_report(raw_request: Request, report_type: ReportType | None = Form(default=None), file: UploadFile = File()) -> ImportResult:
        user = require_user(raw_request)
        try:
            return advertising_imports.import_file(uploaded_by=user.id, report_type=report_type, file_name=file.filename or "", content=await file.read(20 * 1024 * 1024 + 1))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="广告报表导入服务暂时不可用，请稍后重试。") from exc

    @app.post("/api/customs-declarations/preview", response_model=CustomsPreview)
    async def preview_customs_declarations(
        raw_request: Request,
        shipment_file: UploadFile = File(),
        fba_file: UploadFile = File(),
    ) -> CustomsPreview:
        require_user(raw_request)
        return customs_declarations.preview(
            shipment_file_name=shipment_file.filename or "",
            shipment_content=await shipment_file.read(MAX_FILE_SIZE + 1),
            fba_file_name=fba_file.filename or "",
            fba_content=await fba_file.read(MAX_FILE_SIZE + 1),
        )

    @app.post("/api/customs-declarations/generate")
    async def generate_customs_declarations(
        raw_request: Request,
        shipment_file: UploadFile = File(),
        fba_file: UploadFile = File(),
    ) -> Response:
        require_user(raw_request)
        try:
            file_name, payload = customs_declarations.generate(
                shipment_file_name=shipment_file.filename or "",
                shipment_content=await shipment_file.read(MAX_FILE_SIZE + 1),
                fba_file_name=fba_file.filename or "",
                fba_content=await fba_file.read(MAX_FILE_SIZE + 1),
            )
        except CustomsGenerationError as exc:
            return Response(
                content=exc.preview.model_dump_json(),
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                media_type="application/json",
            )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f"attachment; filename=customs-declarations.zip; "
                    f"filename*=UTF-8''{quote(file_name)}"
                )
            },
        )

    @app.post("/api/european-customs-declarations/preview", response_model=EuropeanCustomsPreview)
    async def preview_european_customs_declarations(
        raw_request: Request, file: UploadFile = File()
    ) -> EuropeanCustomsPreview:
        require_user(raw_request)
        return european_customs_declarations.preview(
            file_name=file.filename or "",
            content=await file.read(MAX_FILE_SIZE + 1),
        )

    @app.post("/api/european-customs-declarations/generate")
    async def generate_european_customs_declarations(
        raw_request: Request, file: UploadFile = File()
    ) -> Response:
        require_user(raw_request)
        try:
            file_name, payload = european_customs_declarations.generate(
                file_name=file.filename or "",
                content=await file.read(MAX_FILE_SIZE + 1),
            )
        except EuropeanCustomsGenerationError as exc:
            return Response(
                content=exc.preview.model_dump_json(),
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                media_type="application/json",
            )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    "attachment; filename=european-customs-declarations.zip; "
                    f"filename*=UTF-8''{quote(file_name)}"
                )
            },
        )

    @app.get("/api/ad-report-imports/summary", response_model=ImportDataSummary)
    def advertising_import_summary(raw_request: Request) -> ImportDataSummary:
        require_user(raw_request)
        return ImportDataSummary.model_validate(advertising_imports.store.query_shared_summary())

    @app.get(
        "/api/ad-diagnostics/history",
        response_model=list[AdvertisingRunRecord],
    )
    def list_advertising_history(raw_request: Request, limit: int = 20) -> list[AdvertisingRunRecord]:
        try:
            return advertising_runtime.list_history(limit, owner_id=require_user(raw_request).id)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="广告诊断历史记录暂时不可用。",
            ) from exc

    @app.delete("/api/ad-diagnostics/history/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_advertising_history(run_id: str, raw_request: Request) -> Response:
        try:
            deleted = advertising_runtime.delete_history(run_id, owner_id=require_user(raw_request).id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="运行中的巡检不能删除。") from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="广告诊断历史记录暂时不可用。",
            ) from exc
        if not deleted:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="advertising run not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get(
        "/api/ad-diagnostics/runs/{run_id}",
        response_model=AdvertisingRunRecord,
    )
    def get_advertising_run(run_id: str, raw_request: Request) -> AdvertisingRunRecord:
        record = advertising_runtime.get(run_id, owner_id=require_user(raw_request).id)
        if record is None:
            raise HTTPException(status_code=404, detail="advertising run not found")
        return record

    @app.get("/api/ad-diagnostics/runs/{run_id}/events")
    async def advertising_run_events(
        run_id: str,
        raw_request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        if advertising_runtime.get(run_id, owner_id=require_user(raw_request).id) is None:
            raise HTTPException(status_code=404, detail="advertising run not found")
        return StreamingResponse(
            stage_sse_stream(
                advertising_runtime.hub,
                run_id,
                last_event_id=last_event_id,
            ),
            headers=SSE_RESPONSE_HEADERS,
            media_type="text/event-stream",
        )

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "amazon_ops.api:app",
        host=os.getenv("AMAZON_OPS_API_HOST", "127.0.0.1"),
        port=_env_int("AMAZON_OPS_API_PORT", 8000),
        reload=False,
    )

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .deepseek_runtime import DeepSeekModelRoles, build_deepseek_model_roles
from .events import (
    TERMINAL_EVENT_TYPES,
    InMemoryEventHub,
    StageController,
    StageEventType,
    StageName,
)
from .graph import build_controller_graph
from .interfaces import SpecialistAgent
from .idempotency import (
    IdempotencyConflictError,
    IdempotencyKeyError,
    IdempotencyRegistry,
    IdempotencyStorageError,
    PostgresIdempotencyRegistry,
    request_fingerprint,
)
from .memory import InMemoryConversationStore
from .models import SpecialistName
from .listing import (
    DeterministicListingValidator,
    ListingSpecialistAgent,
    ListingWorkflowServices,
    build_keyword_research_gateway,
)
from .listing.mcp import mcp_trace_context
from .advertising import (
    AdDiagnosticRequest,
    AdShop,
    AdvertisingRunManager,
    AdvertisingRunRecord,
)
from .advertising.history import PostgresAdvertisingRunHistoryStore
from .sse import SSE_RESPONSE_HEADERS, stage_sse_stream
from .auth import AuthStore, AuthUser, bearer_token, require_admin, require_user


DEFAULT_DATABASE_URL = "postgresql://amazon_ops:amazon_ops@127.0.0.1:5432/amazon_ops"


def _env_text(name: str, default: str) -> str:
    """Return the default for empty values entered in managed-host UIs."""

    return os.getenv(name, "").strip() or default


def _env_int(name: str, default: int) -> int:
    return int(_env_text(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env_text(name, str(default)))


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


class CreateRunResponse(BaseModel):
    run_id: str
    conversation_id: str
    status: str = "accepted"


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
        specialists: Mapping[str, SpecialistAgent] | None = None,
        memory: InMemoryConversationStore | None = None,
        max_workers: int = 4,
    ) -> None:
        self.roles = roles or build_deepseek_model_roles()
        if specialists is None:
            listing_services = ListingWorkflowServices(
                researcher=build_keyword_research_gateway(),
                copywriter=self.roles.listing_copywriter,
                validator=DeterministicListingValidator(),
            )
            specialists = {
                SpecialistName.LISTING_CONTENT.value: ListingSpecialistAgent(
                    listing_services
                )
            }
        self.specialists = dict(specialists)
        self.memory = memory or InMemoryConversationStore(max_messages=20)
        self.hub = InMemoryEventHub()
        self.stages = StageController(self.hub)
        self.graph = build_controller_graph(
            interpreter=self.roles.request_interpreter,
            specialists=self.specialists,
            aggregator=self.roles.result_aggregator,
            responder=self.roles.direct_responder,
            stages=self.stages,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="amazon-ops-run"
        )
        self._lock = RLock()
        self._runs: dict[str, RunRecord] = {}
        self._owners: dict[str, str | None] = {}

    def submit(self, request: CreateRunRequest, *, owner_id: str | None = None) -> str:
        run_id = f"run-{uuid4().hex}"
        record = RunRecord(run_id=run_id, status="running")
        with self._lock:
            self._runs[run_id] = record
            self._owners[run_id] = owner_id
        previous_messages = self.memory.history(request.conversation_id)
        current_message = {"role": "user", "content": request.message}
        self.memory.append(
            request.conversation_id, role="user", content=request.message
        )
        state = {
            "request_id": run_id,
            "messages": [*previous_messages, current_message],
            "user_context": request.user_context,
            "shop_directory": request.shop_directory,
            "system_capabilities": {
                "llm": {
                    "provider": "deepseek",
                    "model": self.roles.llm.config.model,
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
                    "lingxing": {
                        "configured": bool(
                            os.getenv("LINGXING_MCP_SECRET", "").strip()
                        ),
                        "purpose": "店铺经营、商品、广告、库存和利润数据",
                    },
                },
                "agents": {
                    name: {"specialist_registered": True}
                    for name in sorted(self.specialists)
                },
            },
            "current_time": datetime.now(timezone.utc).isoformat(),
        }
        self._executor.submit(self._execute, run_id, request.conversation_id, state)
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
                "type": "in_memory",
                "max_messages": self.memory.max_messages,
                "conversation_count": self.memory.count(),
            },
        }

    def _execute(
        self, run_id: str, conversation_id: str, state: dict[str, Any]
    ) -> None:
        try:
            with mcp_trace_context(run_id=run_id):
                result = self.graph.invoke(state)
            final_response = result.get("final_response") or {}
            answer = final_response.get("answer")
            if isinstance(answer, str):
                self.memory.append(conversation_id, role="assistant", content=answer)
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
) -> FastAPI:
    runtime = manager or AgentRunManager()
    shared_llm = getattr(getattr(runtime, "roles", None), "llm", None)
    database_url = _env_text("DATABASE_URL", DEFAULT_DATABASE_URL)
    advertising_runtime = advertising_manager or AdvertisingRunManager(
        llm=shared_llm,
        history_store=PostgresAdvertisingRunHistoryStore(
            database_url,
            max_pool_size=_env_int("AD_DIAGNOSTIC_HISTORY_DB_POOL_SIZE", 10),
        ),
    )
    idempotency = idempotency_registry or PostgresIdempotencyRegistry(
        database_url,
        ttl_seconds=_env_float("IDEMPOTENCY_TTL_SECONDS", 86400),
        max_pool_size=_env_int("IDEMPOTENCY_DB_POOL_SIZE", 10),
    )
    auth = auth_store or AuthStore(
        database_url, max_pool_size=_env_int("AUTH_DB_POOL_SIZE", 10)
    )
    app = FastAPI(title="Amazon Ops Agent API", version="0.1.0")
    app.state.run_manager = runtime
    app.state.advertising_run_manager = advertising_runtime
    app.state.idempotency_registry = idempotency
    app.state.auth_store = auth
    start_advertising_recovery = getattr(advertising_runtime, "start_recovery_monitor", None)
    if callable(start_advertising_recovery):
        # Durable tasks retain their original identifiers; recovery only claims
        # stale records protected by the database lease.
        app.router.add_event_handler("startup", start_advertising_recovery)
    close_idempotency = getattr(idempotency, "close", None)
    if callable(close_idempotency):
        app.router.add_event_handler("shutdown", close_idempotency)
    close_ad_history = getattr(getattr(advertising_runtime, "history_store", None), "close", None)
    if callable(close_ad_history):
        app.router.add_event_handler("shutdown", close_ad_history)
    close_advertising = getattr(advertising_runtime, "close", None)
    if callable(close_advertising):
        app.router.add_event_handler("shutdown", close_advertising)
    app.router.add_event_handler("shutdown", auth.close)
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
        payload = runtime.health()
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
        def submit_run() -> CreateAdvertisingRunResponse:
            owner_id = user.id
            run_id = advertising_runtime.submit(request, owner_id=owner_id)
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

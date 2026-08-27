from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from dotenv import load_dotenv
from langsmith import get_current_run_tree, traceable
from pydantic import BaseModel, Field, ValidationError


TModel = TypeVar("TModel", bound=BaseModel)


class LLMError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class StructuredLLM(Protocol):
    @traceable(name="deepseek_structured_completion", run_type="llm")
    def complete(
        self,
        *,
        system_prompt: str,
        context: str,
        output_model: type[TModel],
        max_tokens: int | None = None,
    ) -> TModel: ...


class DeepSeekConfig(BaseModel):
    api_key_env: str = "DEEPSEEK_API_KEY"
    base_url: str = Field(default="https://api.deepseek.com", pattern=r"^https://")
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = Field(default=120, gt=0, le=600)
    max_tokens: int = Field(default=6000, ge=256, le=384_000)
    temperature: float = Field(default=0.3, ge=0, le=2)
    thinking_enabled: bool = False

    @classmethod
    def from_env(cls, *, env_file: str | Path = ".env") -> "DeepSeekConfig":
        load_dotenv(env_file, override=False)
        # Some managed hosts retain variables entered as an empty string. Treat
        # an empty optional override the same as an unset value so the API can
        # still boot and expose its health endpoint.
        base_url = os.getenv("DEEPSEEK_BASE_URL", "").strip().rstrip("/")
        return cls(
            base_url=base_url or "https://api.deepseek.com",
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        )


class DeepSeekStructuredLLM:
    """Shared JSON-output client for every model-backed role in the project."""

    def __init__(
        self,
        *,
        config: DeepSeekConfig | None = None,
        env_file: str | Path = ".env",
        api_key: str | None = None,
        http_client: httpx.Client | None = None,
        trust_env: bool = False,
    ) -> None:
        self.config = config or DeepSeekConfig.from_env(env_file=env_file)
        load_dotenv(env_file, override=False)
        self._api_key = (api_key or os.getenv(self.config.api_key_env, "")).strip()
        self._owns_client = http_client is None
        self._http = http_client or httpx.Client(
            timeout=self.config.timeout_seconds,
            follow_redirects=True,
            trust_env=trust_env,
        )
        self.last_usage: dict[str, int] = {}
        self.last_request_id: str | None = None

    def complete(
        self,
        *,
        system_prompt: str,
        context: str,
        output_model: type[TModel],
        max_tokens: int | None = None,
    ) -> TModel:
        current_run = get_current_run_tree()
        if current_run is not None:
            current_run.metadata.update(
                {
                    "ls_provider": "deepseek",
                    "ls_model_name": self.config.model,
                    "output_schema": output_model.__name__,
                }
            )
        if not self._api_key:
            raise LLMError(
                "DeepSeek API Key 尚未配置。",
                code="DEEPSEEK_API_KEY_MISSING",
            )

        schema = output_model.model_json_schema()
        schema_instruction = (
            "\n\n你必须只返回一个合法 JSON 对象，不要使用 Markdown 代码块。"
            "JSON 必须符合以下 JSON Schema：\n"
            f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        body: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt + schema_instruction},
                {"role": "user", "content": context},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": self.config.temperature,
            "thinking": {
                "type": "enabled" if self.config.thinking_enabled else "disabled"
            },
            "stream": False,
        }
        try:
            response = self._http.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise LLMError(
                "DeepSeek API is temporarily unreachable",
                code="DEEPSEEK_CONNECTION_FAILED",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            code = (
                "DEEPSEEK_AUTH_FAILED"
                if response.status_code in {401, 403}
                else "DEEPSEEK_RATE_LIMITED"
                if response.status_code == 429
                else "DEEPSEEK_API_ERROR"
            )
            raise LLMError(
                f"DeepSeek API returned HTTP {response.status_code}",
                code=code,
                retryable=response.status_code in {408, 429, 500, 502, 503, 504},
            )

        try:
            payload = response.json()
            choice = payload["choices"][0]
            # Structured responses occasionally need a little more room than
            # the stage budget. Retrying once is safe: this endpoint has no
            # side effects and the failed response cannot be parsed anyway.
            if choice.get("finish_reason") == "length":
                retry_budget = min(max(int(body["max_tokens"]) * 2, 4096), 12000)
                retry_body = {**body, "max_tokens": retry_budget}
                retry_response = self._http.post(
                    f"{self.config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=retry_body,
                )
                if retry_response.status_code >= 400:
                    raise LLMError(
                        f"DeepSeek retry returned HTTP {retry_response.status_code}",
                        code="DEEPSEEK_RATE_LIMITED" if retry_response.status_code == 429 else "DEEPSEEK_API_ERROR",
                        retryable=retry_response.status_code in {408, 429, 500, 502, 503, 504},
                    )
                payload = retry_response.json()
                choice = payload["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise LLMError(
                        "DeepSeek JSON output was truncated after retry",
                        code="DEEPSEEK_OUTPUT_TRUNCATED",
                        retryable=True,
                    )
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise LLMError(
                    "DeepSeek returned empty JSON content",
                    code="DEEPSEEK_EMPTY_OUTPUT",
                    retryable=True,
                )
            parsed = json.loads(content)
            result = output_model.model_validate(parsed)
        except LLMError:
            raise
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise LLMError(
                "DeepSeek returned an invalid structured response",
                code="DEEPSEEK_INVALID_OUTPUT",
            ) from exc

        usage = payload.get("usage") or {}
        self.last_usage = {
            key: int(value)
            for key, value in usage.items()
            if isinstance(value, int) and not isinstance(value, bool)
        }
        request_id = payload.get("id")
        self.last_request_id = str(request_id) if request_id else None
        return result

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "DeepSeekStructuredLLM":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def build_deepseek_llm(
    *, env_file: str | Path = ".env", trust_env: bool = False
) -> DeepSeekStructuredLLM:
    return DeepSeekStructuredLLM(env_file=env_file, trust_env=trust_env)

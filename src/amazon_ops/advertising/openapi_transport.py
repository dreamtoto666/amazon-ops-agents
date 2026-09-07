"""Minimal, server-only LingXing Open API transport.

Credentials remain in the process environment; callers only receive decoded
payloads or a redacted error.  This module deliberately exposes no write API.
"""
from __future__ import annotations

import json
import os
import base64
import hashlib
from dataclasses import dataclass
from threading import RLock
from time import monotonic, time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from dotenv import load_dotenv


class LingxingOpenAPIError(RuntimeError):
    """A safe error suitable for API responses and logs."""


@dataclass(frozen=True)
class _Token:
    value: str
    expires_at: float


class LingxingOpenAPITransport:
    """Read-only Open API client with cached access-token exchange."""

    def __init__(self) -> None:
        load_dotenv(override=False)
        self._token: _Token | None = None
        self._lock = RLock()

    def get(self, path: str, *, query: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, query=query)

    def post(self, path: str, *, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
        return self._request("POST", path, payload=payload, headers=headers)

    def _request(self, method: str, path: str, *, payload: dict[str, Any] | None = None, query: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        token = self._access_token()
        app_id, _ = self._credentials()
        signed = {**(query or {}), "access_token": token, "app_key": app_id, "timestamp": str(int(time()))}
        signed["sign"] = self._sign({**signed, **(payload or {})}, app_id)
        suffix = f"?{urlencode(signed, doseq=True)}"
        body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        request_headers = {"Accept": "application/json", **(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        return self._decode(Request(self._url(path) + suffix, data=body, headers=request_headers, method=method))

    def _access_token(self) -> str:
        with self._lock:
            if self._token and self._token.expires_at > monotonic() + 60:
                return self._token.value
            app_id, secret = self._credentials()
            boundary = "----amazonopslingxing"
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"appId\"\r\n\r\n{app_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"appSecret\"\r\n\r\n{secret}\r\n--{boundary}--\r\n").encode()
            response = self._decode(Request(self._url("/api/auth-server/oauth/access-token"), data=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST"))
            data = response.get("data")
            if str(response.get("code")) != "200" or not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                raise LingxingOpenAPIError("领星 Open API 鉴权失败，请检查本机配置和 IP 白名单。")
            self._token = _Token(data["access_token"], monotonic() + max(60, int(data.get("expires_in", 3600))))
            return self._token.value

    def _credentials(self) -> tuple[str, str]:
        app_id = os.getenv("LINGXING_OPEN_API_APP_ID", "").strip()
        secret = os.getenv("LINGXING_OPEN_API_APP_SECRET", "").strip()
        if not app_id or not secret:
            raise LingxingOpenAPIError("领星 Open API 凭证尚未配置。")
        return app_id, secret

    def _url(self, path: str) -> str:
        base = os.getenv("LINGXING_OPEN_API_BASE_URL", "").rstrip("/")
        if not base.startswith("https://"):
            raise LingxingOpenAPIError("领星 Open API 地址尚未配置。")
        return base + path

    @staticmethod
    def _sign(parameters: dict[str, Any], app_id: str) -> str:
        """Official Open API signing: sorted values, MD5, then AES/ECB/PKCS5."""
        def value(item: Any) -> str:
            if isinstance(item, (dict, list)):
                return json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            return str(item)
        canonical = "&".join(f"{key}={value(item)}" for key, item in sorted(parameters.items()) if item != "")
        digest = hashlib.md5(canonical.encode()).hexdigest().upper().encode()
        padding = 16 - len(digest) % 16
        encryptor = Cipher(algorithms.AES(app_id.encode()), modes.ECB()).encryptor()
        encrypted = encryptor.update(digest + bytes([padding]) * padding) + encryptor.finalize()
        return base64.b64encode(encrypted).decode()

    @staticmethod
    def _decode(request: Request) -> dict[str, Any]:
        try:
            # The Open API is a fixed first-party endpoint.  Do not inherit a
            # developer-machine proxy (often a stopped localhost port), which
            # otherwise makes a healthy LingXing API look unavailable.
            with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise LingxingOpenAPIError("领星 Open API 拒绝访问：请确认当前出口 IP 已加入领星白名单，并检查应用权限。") from None
            if exc.code == 429:
                raise LingxingOpenAPIError("领星 Open API 请求频率受限，请稍后重试。") from None
            raise LingxingOpenAPIError("领星 Open API 服务暂时不可用，请稍后重试。") from None
        except (URLError, TimeoutError, ValueError):
            raise LingxingOpenAPIError("领星 Open API 暂时不可用，请稍后重试。") from None
        if not isinstance(value, dict):
            raise LingxingOpenAPIError("领星 Open API 返回格式无效。")
        return value

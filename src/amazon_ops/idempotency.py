from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from threading import Lock, RLock
from time import monotonic
from typing import Any, Callable, Protocol, TypeVar

from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout
from pydantic import BaseModel


T = TypeVar("T")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


class IdempotencyKeyError(ValueError):
    pass


class IdempotencyConflictError(RuntimeError):
    pass


class IdempotencyStorageError(RuntimeError):
    pass


@dataclass(frozen=True)
class IdempotencyResolution:
    value: Any
    replayed: bool


class IdempotencyRegistry(Protocol):
    def resolve(
        self,
        *,
        namespace: str,
        key: str,
        fingerprint: str,
        factory: Callable[[], T],
    ) -> IdempotencyResolution: ...

    def health(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class _Entry:
    fingerprint: str
    value: Any
    created_at: float


class InMemoryIdempotencyRegistry:
    """Small dependency-injected registry retained for isolated unit tests."""

    def __init__(self, *, ttl_seconds: float = 86_400, max_entries: int = 10_000) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._lock = RLock()
        self._entries: dict[tuple[str, str], _Entry] = {}

    def resolve(
        self,
        *,
        namespace: str,
        key: str,
        fingerprint: str,
        factory: Callable[[], T],
    ) -> IdempotencyResolution:
        normalized = validate_idempotency_key(key)
        registry_key = (namespace, normalized)
        with self._lock:
            self._prune_locked()
            existing = self._entries.get(registry_key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise IdempotencyConflictError(
                        "idempotency key was already used with a different request"
                    )
                return IdempotencyResolution(value=existing.value, replayed=True)

            value = factory()
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=lambda item: self._entries[item].created_at)
                self._entries.pop(oldest, None)
            self._entries[registry_key] = _Entry(
                fingerprint=fingerprint,
                value=value,
                created_at=monotonic(),
            )
            return IdempotencyResolution(value=value, replayed=False)

    def _prune_locked(self) -> None:
        cutoff = monotonic() - self.ttl_seconds
        expired = [key for key, entry in self._entries.items() if entry.created_at < cutoff]
        for key in expired:
            self._entries.pop(key, None)

    def health(self) -> dict[str, str]:
        return {"backend": "memory", "status": "ok"}


class PostgresIdempotencyRegistry:
    """PostgreSQL-backed registry shared safely by multiple API processes."""

    def __init__(
        self,
        database_url: str,
        *,
        ttl_seconds: float = 86_400,
        max_pool_size: int = 10,
    ) -> None:
        if not database_url.strip():
            raise ValueError("database_url must not be empty")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_pool_size < 1:
            raise ValueError("max_pool_size must be positive")
        self.ttl_seconds = ttl_seconds
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=0,
            max_size=max_pool_size,
            open=False,
            kwargs={"autocommit": False},
        )
        self._schema_lock = Lock()
        self._schema_ready = False

    def resolve(
        self,
        *,
        namespace: str,
        key: str,
        fingerprint: str,
        factory: Callable[[], T],
    ) -> IdempotencyResolution:
        normalized = validate_idempotency_key(key)
        self._ensure_schema()

        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    connection.execute(
                        """
                        DELETE FROM idempotency_records
                        WHERE expires_at <= CURRENT_TIMESTAMP
                        """,
                    )
                    inserted = connection.execute(
                        """
                        INSERT INTO idempotency_records (
                            namespace,
                            idempotency_key,
                            request_fingerprint,
                            expires_at
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            CURRENT_TIMESTAMP + (%s * INTERVAL '1 second')
                        )
                        ON CONFLICT (namespace, idempotency_key) DO NOTHING
                        RETURNING idempotency_key
                        """,
                        (namespace, normalized, fingerprint, self.ttl_seconds),
                    ).fetchone()

                    if inserted is not None:
                        value = factory()
                        connection.execute(
                            """
                            UPDATE idempotency_records
                            SET response_payload = %s
                            WHERE namespace = %s AND idempotency_key = %s
                            """,
                            (Jsonb(_json_compatible(value)), namespace, normalized),
                        )
                        return IdempotencyResolution(value=value, replayed=False)

                    existing = connection.execute(
                        """
                        SELECT request_fingerprint, response_payload
                        FROM idempotency_records
                        WHERE namespace = %s AND idempotency_key = %s
                        FOR UPDATE
                        """,
                        (namespace, normalized),
                    ).fetchone()
                    if existing is None or existing[1] is None:
                        raise IdempotencyStorageError(
                            "idempotency reservation has no stored response"
                        )
                    if existing[0] != fingerprint:
                        raise IdempotencyConflictError(
                            "idempotency key was already used with a different request"
                        )
                    return IdempotencyResolution(value=existing[1], replayed=True)
        except (IdempotencyConflictError, IdempotencyStorageError):
            raise
        except (PsycopgError, PoolTimeout) as exc:
            raise IdempotencyStorageError("PostgreSQL idempotency storage failed") from exc

    def close(self) -> None:
        self._pool.close()

    def health(self) -> dict[str, str]:
        self._ensure_schema()
        try:
            with self._pool.connection() as connection:
                connection.execute("SELECT 1").fetchone()
        except (PsycopgError, PoolTimeout) as exc:
            raise IdempotencyStorageError("PostgreSQL idempotency storage failed") from exc
        return {"backend": "postgresql", "status": "ok"}

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            try:
                self._pool.open(wait=True, timeout=10)
                with self._pool.connection() as connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS idempotency_records (
                            namespace VARCHAR(100) NOT NULL,
                            idempotency_key VARCHAR(128) NOT NULL,
                            request_fingerprint CHAR(64) NOT NULL,
                            response_payload JSONB,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                            expires_at TIMESTAMPTZ NOT NULL,
                            PRIMARY KEY (namespace, idempotency_key)
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE INDEX IF NOT EXISTS idempotency_records_expires_at_idx
                        ON idempotency_records (expires_at)
                        """
                    )
                self._schema_ready = True
            except Exception as exc:
                raise IdempotencyStorageError(
                    "Could not initialize PostgreSQL idempotency storage"
                ) from exc


def validate_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(normalized):
        raise IdempotencyKeyError(
            "Idempotency-Key must contain 8-128 letters, numbers, dots, colons, underscores, or hyphens"
        )
    return normalized


def request_fingerprint(value: BaseModel | dict[str, Any]) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))

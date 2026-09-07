from __future__ import annotations

from threading import Lock, RLock
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from psycopg import Error as PsycopgError
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool, PoolTimeout


class AdvertisingRunHistoryStore(Protocol):
    def save(self, record: dict[str, Any], request: dict[str, Any] | None, owner_id: str | None = None) -> None: ...

    def get(self, run_id: str, owner_id: str | None = None) -> dict[str, Any] | None: ...

    def list(self, limit: int = 20, owner_id: str | None = None) -> list[dict[str, Any]]: ...

    def delete(self, run_id: str, owner_id: str | None = None) -> bool: ...

    def save_checkpoint(self, run_id: str, checkpoint_key: str, state: dict[str, Any]) -> None: ...

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None: ...

    def reserve_recovery(self, run_id: str, *, lease_owner: str, stale_before: datetime) -> dict[str, Any] | None: ...

    def heartbeat(self, run_id: str, *, lease_owner: str) -> None: ...

    def recoverable_runs(self, *, stale_before: datetime) -> list[dict[str, Any]]: ...

    def reserve_call(self, run_id: str, logical_key: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def complete_call(self, call_id: str, result: dict[str, Any]) -> None: ...

    def close(self) -> None: ...


class InMemoryAdvertisingRunHistoryStore:
    """Default for isolated tests; production uses the PostgreSQL implementation."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] = {}
        self._checkpoints: dict[str, list[dict[str, Any]]] = {}
        self._calls: dict[tuple[str, str], dict[str, Any]] = {}

    def save(self, record: dict[str, Any], request: dict[str, Any] | None, owner_id: str | None = None) -> None:
        with self._lock:
            self._records[record["run_id"]] = {"record": record, "request": request, "owner_id": owner_id}

    def get(self, run_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            value = self._records.get(run_id)
            return dict(value["record"]) if value and value["owner_id"] == owner_id else None

    def list(self, limit: int = 20, owner_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(item["record"])
                for item in [item for item in self._records.values() if item["owner_id"] == owner_id][::-1][:max(1, limit)]
            ]

    def delete(self, run_id: str, owner_id: str | None = None) -> bool:
        with self._lock:
            value = self._records.get(run_id)
            if value is None or value["owner_id"] != owner_id:
                return False
            del self._records[run_id]
            self._checkpoints.pop(run_id, None)
            return True

    def save_checkpoint(self, run_id: str, checkpoint_key: str, state: dict[str, Any]) -> None:
        with self._lock:
            entries = self._checkpoints.setdefault(run_id, [])
            entries[:] = [item for item in entries if item["checkpoint_key"] != checkpoint_key]
            entries.append({"checkpoint_key": checkpoint_key, "state": dict(state), "created_at": datetime.now(timezone.utc)})

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            entries = self._checkpoints.get(run_id, [])
            return dict(entries[-1]) if entries else None

    def reserve_recovery(self, run_id: str, *, lease_owner: str, stale_before: datetime) -> dict[str, Any] | None:
        with self._lock:
            item = self._records.get(run_id)
            if not item or item["record"].get("status") != "running":
                return None
            record = item["record"]
            if record.get("recovery_count", 0) >= 2:
                return None
            heartbeat = self._as_datetime(record.get("last_heartbeat_at"))
            if heartbeat and heartbeat > stale_before:
                return None
            record["recovery_count"] = record.get("recovery_count", 0) + 1
            record["lease_owner"] = lease_owner
            record["lease_expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=5)
            record["last_heartbeat_at"] = datetime.now(timezone.utc)
            return {"record": dict(record), "request": dict(item["request"] or {})}

    def heartbeat(self, run_id: str, *, lease_owner: str) -> None:
        with self._lock:
            item = self._records.get(run_id)
            if item and item["record"].get("lease_owner") == lease_owner:
                item["record"]["last_heartbeat_at"] = datetime.now(timezone.utc)
                item["record"]["lease_expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=5)

    def recoverable_runs(self, *, stale_before: datetime) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item["record"]) for item in self._records.values() if item["record"].get("status") == "running" and (not self._as_datetime(item["record"].get("last_heartbeat_at")) or self._as_datetime(item["record"].get("last_heartbeat_at")) < stale_before)]

    def reserve_call(self, run_id: str, logical_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            value = self._calls.setdefault((run_id, logical_key), {"call_id": f"call-{len(self._calls) + 1}", "status": "reserved", **payload})
            return dict(value)

    def complete_call(self, call_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            for value in self._calls.values():
                if value["call_id"] == call_id:
                    value.update({"status": "succeeded", "result": dict(result)})
                    return

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return None

    def close(self) -> None:
        return None


class PostgresAdvertisingRunHistoryStore:
    def __init__(self, database_url: str, *, max_pool_size: int = 10) -> None:
        self._pool = ConnectionPool(
            conninfo=database_url,
            min_size=0,
            max_size=max_pool_size,
            open=False,
            kwargs={"autocommit": False},
        )
        self._schema_lock = Lock()
        self._schema_ready = False

    def save(self, record: dict[str, Any], request: dict[str, Any] | None, owner_id: str | None = None) -> None:
        self._ensure_schema()
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    connection.execute(
                        """
                        INSERT INTO advertising_diagnostic_runs (
                            run_id, trace_id, span_id, stage, status, owner_id,
                            request_payload, result_payload, error_payload, detail_call_quotas,
                            recovery_count, last_heartbeat_at, lease_owner, lease_expires_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id) DO UPDATE SET
                            trace_id = EXCLUDED.trace_id,
                            span_id = EXCLUDED.span_id,
                            stage = EXCLUDED.stage,
                            status = EXCLUDED.status,
                            owner_id = EXCLUDED.owner_id,
                            request_payload = COALESCE(EXCLUDED.request_payload, advertising_diagnostic_runs.request_payload),
                            result_payload = EXCLUDED.result_payload,
                            error_payload = EXCLUDED.error_payload,
                            detail_call_quotas = EXCLUDED.detail_call_quotas,
                            recovery_count = EXCLUDED.recovery_count,
                            last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                            lease_owner = EXCLUDED.lease_owner,
                            lease_expires_at = EXCLUDED.lease_expires_at,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            record["run_id"],
                            record["trace_id"],
                            record["span_id"],
                            record["stage"],
                            record["status"],
                            owner_id,
                            Jsonb(request) if request is not None else None,
                            Jsonb(record["result"]) if record.get("result") is not None else None,
                            Jsonb(record["error"]) if record.get("error") is not None else None,
                            Jsonb(record.get("detail_call_quotas", [])),
                            record.get("recovery_count", 0), record.get("last_heartbeat_at"),
                            record.get("lease_owner"), record.get("lease_expires_at"),
                        ),
                    )
        except (PsycopgError, PoolTimeout) as exc:
            raise RuntimeError("广告诊断历史记录存储失败") from exc

    def get(self, run_id: str, owner_id: str | None = None) -> dict[str, Any] | None:
        self._ensure_schema()
        try:
            with self._pool.connection() as connection:
                row = connection.execute(
                    """
                    SELECT run_id, trace_id, span_id, stage, status, result_payload, error_payload, detail_call_quotas, created_at, recovery_count, last_heartbeat_at, lease_owner, lease_expires_at
                    FROM advertising_diagnostic_runs WHERE run_id = %s AND owner_id IS NOT DISTINCT FROM %s
                    """,
                    (run_id, owner_id),
                ).fetchone()
        except (PsycopgError, PoolTimeout) as exc:
            raise RuntimeError("广告诊断历史记录读取失败") from exc
        if row is None:
            return None
        return {
            "run_id": row[0], "trace_id": row[1], "span_id": row[2],
            "stage": row[3], "status": row[4], "result": row[5], "error": row[6], "detail_call_quotas": row[7] or [], "created_at": row[8], "recovery_count": row[9], "last_heartbeat_at": row[10], "lease_owner": row[11], "lease_expires_at": row[12],
        }

    def list(self, limit: int = 20, owner_id: str | None = None) -> list[dict[str, Any]]:
        self._ensure_schema()
        safe_limit = max(1, min(limit, 100))
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT run_id, trace_id, span_id, stage, status, result_payload, error_payload, detail_call_quotas, created_at, recovery_count, last_heartbeat_at, lease_owner, lease_expires_at
                    FROM advertising_diagnostic_runs
                    WHERE owner_id IS NOT DISTINCT FROM %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (owner_id, safe_limit),
                ).fetchall()
        except (PsycopgError, PoolTimeout) as exc:
            raise RuntimeError("广告诊断历史记录读取失败") from exc
        return [
            {"run_id": row[0], "trace_id": row[1], "span_id": row[2], "stage": row[3],
             "status": row[4], "result": row[5], "error": row[6], "detail_call_quotas": row[7] or [], "created_at": row[8], "recovery_count": row[9], "last_heartbeat_at": row[10], "lease_owner": row[11], "lease_expires_at": row[12]}
            for row in rows
        ]

    def delete(self, run_id: str, owner_id: str | None = None) -> bool:
        self._ensure_schema()
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    result = connection.execute(
                        "DELETE FROM advertising_diagnostic_runs WHERE run_id = %s AND owner_id IS NOT DISTINCT FROM %s",
                        (run_id, owner_id),
                    )
                    return result.rowcount > 0
        except (PsycopgError, PoolTimeout) as exc:
            raise RuntimeError("广告诊断历史记录删除失败") from exc

    def save_checkpoint(self, run_id: str, checkpoint_key: str, state: dict[str, Any]) -> None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("""
                    INSERT INTO advertising_diagnostic_checkpoints (run_id, checkpoint_key, state_payload)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (run_id, checkpoint_key) DO UPDATE SET state_payload = EXCLUDED.state_payload, created_at = CURRENT_TIMESTAMP
                """, (run_id, checkpoint_key, Jsonb(state)))

    def latest_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            row = connection.execute("""
                SELECT checkpoint_key, state_payload, created_at FROM advertising_diagnostic_checkpoints
                WHERE run_id = %s ORDER BY created_at DESC LIMIT 1
            """, (run_id,)).fetchone()
        return {"checkpoint_key": row[0], "state": row[1], "created_at": row[2]} if row else None

    def reserve_recovery(self, run_id: str, *, lease_owner: str, stale_before: datetime) -> dict[str, Any] | None:
        self._ensure_schema()
        now = datetime.now(timezone.utc)
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute("""
                    UPDATE advertising_diagnostic_runs
                    SET recovery_count = recovery_count + 1, lease_owner = %s,
                        lease_expires_at = %s, last_heartbeat_at = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE run_id = %s AND status = 'running' AND result_payload IS NULL
                      AND recovery_count < 2
                      AND (last_heartbeat_at IS NULL OR last_heartbeat_at < %s)
                      AND (lease_expires_at IS NULL OR lease_expires_at < %s)
                    RETURNING run_id, trace_id, span_id, stage, status, request_payload, recovery_count
                """, (lease_owner, now + timedelta(minutes=5), now, run_id, stale_before, now)).fetchone()
        return {"record": {"run_id": row[0], "trace_id": row[1], "span_id": row[2], "stage": row[3], "status": row[4], "recovery_count": row[6]}, "request": row[5]} if row else None

    def heartbeat(self, run_id: str, *, lease_owner: str) -> None:
        self._ensure_schema()
        now = datetime.now(timezone.utc)
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("""UPDATE advertising_diagnostic_runs SET last_heartbeat_at = %s, lease_expires_at = %s, updated_at = CURRENT_TIMESTAMP WHERE run_id = %s AND lease_owner = %s AND status = 'running'""", (now, now + timedelta(minutes=5), run_id, lease_owner))

    def recoverable_runs(self, *, stale_before: datetime) -> list[dict[str, Any]]:
        self._ensure_schema()
        with self._pool.connection() as connection:
            rows = connection.execute("""SELECT run_id, trace_id, span_id, stage, status, recovery_count FROM advertising_diagnostic_runs WHERE status = 'running' AND result_payload IS NULL AND recovery_count < 2 AND (last_heartbeat_at IS NULL OR last_heartbeat_at < %s)""", (stale_before,)).fetchall()
        return [{"run_id": row[0], "trace_id": row[1], "span_id": row[2], "stage": row[3], "status": row[4], "recovery_count": row[5]} for row in rows]

    def reserve_call(self, run_id: str, logical_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._ensure_schema()
        call_id = payload["call_id"]
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute("""
                    INSERT INTO advertising_diagnostic_call_ledger
                    (call_id, run_id, logical_key, stage, attribution_round, tool, request_payload, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'reserved')
                    ON CONFLICT (run_id, logical_key) DO UPDATE SET last_attempt_at = CURRENT_TIMESTAMP
                    RETURNING call_id, status, result_payload
                """, (call_id, run_id, logical_key, payload["stage"], payload.get("attribution_round"), payload["tool"], Jsonb(payload["request"]))).fetchone()
        return {"call_id": row[0], "status": row[1], "result": row[2]}

    def complete_call(self, call_id: str, result: dict[str, Any]) -> None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("""UPDATE advertising_diagnostic_call_ledger SET status = 'succeeded', result_payload = %s, completed_at = CURRENT_TIMESTAMP WHERE call_id = %s""", (Jsonb(result), call_id))

    def close(self) -> None:
        self._pool.close()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self._pool.open(wait=True, timeout=10)
            with self._pool.connection() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS advertising_diagnostic_runs (
                        run_id VARCHAR(80) PRIMARY KEY,
                        trace_id VARCHAR(80) NOT NULL,
                        span_id VARCHAR(80) NOT NULL,
                        stage VARCHAR(80) NOT NULL,
                        status VARCHAR(40) NOT NULL,
                        owner_id UUID,
                        request_payload JSONB,
                        result_payload JSONB,
                        error_payload JSONB,
                        detail_call_quotas JSONB NOT NULL DEFAULT '[]'::jsonb,
                        recovery_count INTEGER NOT NULL DEFAULT 0,
                        last_heartbeat_at TIMESTAMPTZ,
                        lease_owner VARCHAR(80),
                        lease_expires_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                connection.execute(
                    "ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS owner_id UUID"
                )
                connection.execute(
                    "ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS detail_call_quotas JSONB NOT NULL DEFAULT '[]'::jsonb"
                )
                connection.execute("ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS recovery_count INTEGER NOT NULL DEFAULT 0")
                connection.execute("ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ")
                connection.execute("ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS lease_owner VARCHAR(80)")
                connection.execute("ALTER TABLE advertising_diagnostic_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ")
                connection.execute("""CREATE TABLE IF NOT EXISTS advertising_diagnostic_checkpoints (
                    run_id VARCHAR(80) NOT NULL REFERENCES advertising_diagnostic_runs(run_id) ON DELETE CASCADE,
                    checkpoint_key VARCHAR(100) NOT NULL, state_payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (run_id, checkpoint_key))""")
                connection.execute("""CREATE TABLE IF NOT EXISTS advertising_diagnostic_call_ledger (
                    call_id VARCHAR(80) PRIMARY KEY, run_id VARCHAR(80) NOT NULL REFERENCES advertising_diagnostic_runs(run_id) ON DELETE CASCADE,
                    logical_key VARCHAR(255) NOT NULL, stage VARCHAR(80) NOT NULL, attribution_round INTEGER,
                    tool VARCHAR(120) NOT NULL, request_payload JSONB NOT NULL, result_payload JSONB,
                    status VARCHAR(20) NOT NULL, completed_at TIMESTAMPTZ, last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (run_id, logical_key))""")
                connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS advertising_diagnostic_runs_created_at_idx
                    ON advertising_diagnostic_runs (created_at DESC)
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS advertising_diagnostic_runs_owner_created_at_idx ON advertising_diagnostic_runs (owner_id, created_at DESC)"
                )
                connection.execute("COMMENT ON TABLE advertising_diagnostic_runs IS '广告异常诊断的持久化运行记录，包含阶段、结果、恢复租约和追踪标识。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_runs.owner_id IS '发起诊断的租户/用户标识，用于数据隔离。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_runs.trace_id IS '一次完整广告诊断链路的可追溯标识。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_runs.request_payload IS '经验证的诊断请求范围与阈值 JSON。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_runs.result_payload IS '四阶段诊断完成后的结构化报告 JSON。'")
                connection.execute("COMMENT ON TABLE advertising_diagnostic_checkpoints IS '广告诊断阶段检查点，用于进程中断后的安全恢复。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_checkpoints.state_payload IS '恢复所需的结构化工作流状态，不包含未压缩 MCP 原始大负载。'")
                connection.execute("COMMENT ON TABLE advertising_diagnostic_call_ledger IS '广告诊断只读数据调用账本，用于去重、恢复及调用配额审计。'")
                connection.execute("COMMENT ON COLUMN advertising_diagnostic_call_ledger.logical_key IS '由阶段、轮次、工具和规范化参数生成的幂等读取键。'")
            self._schema_ready = True

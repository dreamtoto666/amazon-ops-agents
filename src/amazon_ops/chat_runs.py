"""Durable checkpoints and leases for ordinary controller chat runs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool


def _jsonb(value: dict[str, Any] | None) -> Jsonb | None:
    if value is None:
        return None
    return Jsonb(json.loads(json.dumps(value, ensure_ascii=False, default=str)))


class PostgresChatRunStore:
    LEASE_SECONDS = 300

    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(conninfo=database_url, min_size=0, max_size=5, open=False)
        self._ready = False
        self._lock = RLock()

    def initialize(self) -> None:
        if self._ready: return
        with self._lock:
            if self._ready: return
            self._pool.open(wait=True, timeout=10)
            with self._pool.connection() as conn, conn.transaction():
                conn.execute("CREATE TABLE IF NOT EXISTS controller_chat_runs (run_id TEXT PRIMARY KEY, owner_id UUID, conversation_id TEXT NOT NULL, status TEXT NOT NULL, request_payload JSONB NOT NULL, result_payload JSONB, recovery_count INTEGER NOT NULL DEFAULT 0, last_heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, lease_owner TEXT, lease_expires_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                conn.execute("CREATE TABLE IF NOT EXISTS controller_chat_checkpoints (run_id TEXT NOT NULL REFERENCES controller_chat_runs(run_id) ON DELETE CASCADE, checkpoint_key TEXT NOT NULL, state_payload JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (run_id, checkpoint_key))")
                conn.execute("CREATE INDEX IF NOT EXISTS controller_chat_runs_recovery_idx ON controller_chat_runs (status, last_heartbeat_at)")
                conn.execute("COMMENT ON TABLE controller_chat_runs IS '主控普通聊天的持久化运行、心跳及恢复租约。'")
                conn.execute("COMMENT ON TABLE controller_chat_checkpoints IS '主控聊天各业务节点完成后的结构化恢复检查点。'")
            self._ready = True

    def create(self, run_id: str, owner_id: str | None, conversation_id: str, state: dict[str, Any]) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO controller_chat_runs (run_id,owner_id,conversation_id,status,request_payload) VALUES (%s,%s,%s,'running',%s)", (run_id, owner_id, conversation_id, _jsonb(state)))

    def checkpoint(self, run_id: str, key: str, state: dict[str, Any]) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO controller_chat_checkpoints (run_id,checkpoint_key,state_payload) VALUES (%s,%s,%s) ON CONFLICT (run_id,checkpoint_key) DO UPDATE SET state_payload=EXCLUDED.state_payload,created_at=CURRENT_TIMESTAMP", (run_id, key, _jsonb(state)))
            conn.execute("UPDATE controller_chat_runs SET last_heartbeat_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE run_id=%s AND status='running'", (run_id,))

    def finish(self, run_id: str, status: str, result: dict[str, Any] | None = None) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("UPDATE controller_chat_runs SET status=%s,result_payload=%s,lease_owner=NULL,lease_expires_at=NULL,updated_at=CURRENT_TIMESTAMP WHERE run_id=%s", (status, _jsonb(result), run_id))

    def claim_stale(self, stale_after: timedelta) -> list[dict[str, Any]]:
        self.initialize(); now = datetime.now(timezone.utc); stale = now - stale_after
        with self._pool.connection() as conn, conn.transaction():
            rows = conn.execute("UPDATE controller_chat_runs SET recovery_count=recovery_count+1,lease_owner=run_id,lease_expires_at=%s,last_heartbeat_at=%s,updated_at=%s WHERE status='running' AND recovery_count<2 AND last_heartbeat_at<%s AND (lease_expires_at IS NULL OR lease_expires_at<%s) RETURNING run_id,owner_id,conversation_id", (now + timedelta(seconds=self.LEASE_SECONDS), now, now, stale, now)).fetchall()
            claimed = []
            for row in rows:
                checkpoint = conn.execute("SELECT checkpoint_key,state_payload FROM controller_chat_checkpoints WHERE run_id=%s ORDER BY created_at DESC LIMIT 1", (row[0],)).fetchone()
                if checkpoint: claimed.append({"run_id": row[0], "owner_id": str(row[1]) if row[1] else None, "conversation_id": row[2], "checkpoint_key": checkpoint[0], "state": checkpoint[1]})
            return claimed

    def close(self) -> None: self._pool.close()

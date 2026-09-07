"""Read-only NL2SQL for the shared imported advertising reports."""
from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from typing import Any

import sqlglot
from pydantic import BaseModel, Field
from psycopg_pool import ConnectionPool

from .llm import StructuredLLM

TABLE = "shared_imported_advertising_report_rows"
COLUMNS = {"report_type", "profile_id", "report_date", "campaign_id", "ad_group_id", "keyword_id", "target_id", "search_term", "match_type", "impressions", "clicks", "spend", "sales", "orders", "ad_units"}
TABLE_DESCRIPTION = "团队共享的标准化广告报表明细；它是只读广告分析的事实来源。"
COLUMN_DESCRIPTIONS = {
    "report_type": "报表类型，用于区分 Campaign、关键词、搜索词等不同粒度。",
    "profile_id": "Amazon 广告店铺/授权 Profile ID；用于区分店铺，不是 ERP sid。",
    "report_date": "报表业务日期，用于日期范围筛选和周期比较。",
    "campaign_id": "Amazon 广告活动 ID。",
    "ad_group_id": "广告组 ID；没有广告组维度时为空字符串。",
    "keyword_id": "关键词 ID；仅适用于关键词相关报表，否则为空字符串。",
    "target_id": "投放目标 ID；仅适用于投放相关报表，否则为空字符串。",
    "search_term": "用户实际搜索词；仅适用于搜索词相关报表，否则为空字符串。",
    "match_type": "匹配方式或投放类型；缺失时为空字符串。",
    "impressions": "广告曝光量。",
    "clicks": "广告点击量。",
    "spend": "广告花费，币种以导入报表为准。",
    "sales": "广告归因销售额。",
    "orders": "广告归因订单数。",
    "ad_units": "广告归因销售件数。",
}


class NL2SQLDraft(BaseModel):
    sql: str = Field(min_length=1, max_length=8000)
    explanation: str = Field(min_length=1, max_length=500)


class NL2SQLResult(BaseModel):
    answer: str
    evidence: dict[str, Any]
    rows: list[dict[str, Any]] = Field(default_factory=list)


class NL2SQLError(RuntimeError):
    pass


@dataclass
class NL2SQLService:
    llm: StructuredLLM
    readonly_url: str

    @classmethod
    def from_env(cls, llm: StructuredLLM) -> "NL2SQLService | None":
        url = os.getenv("DATABASE_READONLY_URL", "").strip()
        return cls(llm, url) if url else None

    def query(self, *, question: str) -> NL2SQLResult:
        draft = self.llm.complete(system_prompt=self._prompt(), context=question, output_model=NL2SQLDraft, max_tokens=1200)
        sql = self._validate(draft.sql)
        started = time.monotonic()
        pool = ConnectionPool(conninfo=self.readonly_url, min_size=0, max_size=2)
        try:
            with pool.connection() as conn, conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '5000ms'")
                cursor = conn.execute(sql)
                rows = cursor.fetchall()
                columns = [item.name for item in cursor.description or []]
        finally:
            pool.close()
        # psycopg cursor metadata is intentionally read before closing in a real execution path.
        # Keep a stable, non-sensitive answer even when the result has no columns.
        elapsed = round((time.monotonic() - started) * 1000)
        records = [
            {column: str(value) if value is not None else None for column, value in zip(columns, row)}
            for row in rows
        ]
        preview = "；".join("，".join(f"{key}={value}" for key, value in record.items()) for record in records)
        return NL2SQLResult(answer=f"查询完成，共返回 {len(rows)} 行。{draft.explanation}" + (f"\n{preview}" if preview else ""), evidence={"rows": len(rows), "duration_ms": elapsed, "sql_fingerprint": hashlib.sha256(sql.encode()).hexdigest()[:16], "source": TABLE}, rows=records)

    def _validate(self, sql: str) -> str:
        try:
            statements = sqlglot.parse(sql, read="postgres")
        except Exception as exc: raise NL2SQLError("模型生成的 SQL 无法解析") from exc
        if len(statements) != 1 or statements[0].key != "select": raise NL2SQLError("只允许单条 SELECT 查询")
        normalized = statements[0].sql(dialect="postgres")
        lowered = normalized.lower()
        if "owner_id" in lowered:
            raise NL2SQLError("共享广告报表不支持按上传用户筛选")
        for table in statements[0].find_all(sqlglot.exp.Table):
            if table.name != TABLE: raise NL2SQLError("查询包含未授权数据表")
        aliases = {alias.alias for alias in statements[0].find_all(sqlglot.exp.Alias) if alias.alias}
        for column in statements[0].find_all(sqlglot.exp.Column):
            if column.name and column.name not in COLUMNS | aliases: raise NL2SQLError("查询包含未授权字段")
        if ";" in sql.rstrip(";"): raise NL2SQLError("只允许单条 SQL")
        return normalized

    @staticmethod
    def _prompt() -> str:
        schema = "\n".join(
            f"- {name}: {COLUMN_DESCRIPTIONS[name]}"
            for name in sorted(COLUMNS)
        )
        return f"""你是只读广告报表 SQL 生成器。只输出 PostgreSQL 单条 SELECT。

唯一允许的表：{TABLE}
表说明：{TABLE_DESCRIPTION}
字段说明：
{schema}

这是团队共享数据，不能按上传用户或 owner_id 筛选。不得使用 INSERT/UPDATE/DELETE/DDL、子查询、系统表、SQL 注释或多语句。不得查询 payload、identity_key、uploaded_by 或其他未列字段。用户问题：按其要求筛选、聚合、排序。"""

"""Validated, shared advertising report imports.

Files are never retained. A valid upload is immediately upserted into the
shared reporting dataset after validation; there is no preview/confirmation
batch.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from openpyxl import load_workbook
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 100_000
PREVIEW_TTL = timedelta(minutes=30)


class ReportType(str, Enum):
    GENERIC = "generic"
    CAMPAIGN = "campaign"
    AD_GROUP = "ad_group"
    KEYWORD = "keyword"
    TARGETING = "targeting"
    SEARCH_TERM = "search_term"
    SEARCH_TERM_SHARE = "search_term_share"
    PURCHASED_PRODUCT = "purchased_product"
    ADVERTISED_PRODUCT = "advertised_product"
    PLACEMENT = "placement"


COMMON = ("Profile ID", "日期", "曝光", "点击", "花费", "销售额", "订单", "广告销量")
HEADERS: dict[ReportType, tuple[str, ...]] = {
    ReportType.GENERIC: ("Profile ID", "日期", "Campaign ID", "Campaign 名称", "广告类型", "Ad Group ID", "Ad Group 名称", "Keyword ID", "关键词", "Target ID", "投放目标", "投放类型", "Search Term", "匹配类型", "曝光", "点击", "花费", "销售额", "订单", "广告销量"),
    ReportType.CAMPAIGN: (*COMMON[:2], "Campaign ID", "Campaign 名称", "广告类型", *COMMON[2:]),
    ReportType.AD_GROUP: (*COMMON[:2], "Campaign ID", "Campaign 名称", "Ad Group ID", "Ad Group 名称", *COMMON[2:]),
    ReportType.KEYWORD: (*COMMON[:2], "Campaign ID", "Campaign 名称", "Ad Group ID", "Ad Group 名称", "Keyword ID", "关键词", "匹配类型", *COMMON[2:]),
    ReportType.TARGETING: (*COMMON[:2], "Campaign ID", "Campaign 名称", "Ad Group ID", "Ad Group 名称", "Target ID", "投放目标", "投放类型", "匹配类型", *COMMON[2:]),
    ReportType.SEARCH_TERM: (*COMMON[:2], "Campaign ID", "Campaign 名称", "Ad Group ID", "Ad Group 名称", "Search Term", "匹配类型", *COMMON[2:]),
}

GENERIC_HEADER_ALIASES = {
    "profile id": "Profile ID", "profile_id": "Profile ID", "店铺 profile id": "Profile ID",
    "date": "日期", "report date": "日期", "日期": "日期",
    "campaign id": "Campaign ID", "campaign name": "Campaign 名称", "ad type": "广告类型",
    "ad group id": "Ad Group ID", "ad group name": "Ad Group 名称",
    "keyword id": "Keyword ID", "keyword": "关键词", "target id": "Target ID",
    "targeting": "投放目标", "targeting type": "投放类型", "search term": "Search Term", "match type": "匹配类型",
    "impressions": "曝光", "clicks": "点击", "spend": "花费", "cost": "花费", "sales": "销售额", "orders": "订单", "units": "广告销量", "ad units": "广告销量",
}


class ImportErrorRow(BaseModel):
    row_number: int
    field: str | None = None
    message: str


class ImportPreview(BaseModel):
    batch_id: str
    report_type: ReportType
    file_name: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    estimated_overwrites: int
    errors: list[ImportErrorRow] = Field(default_factory=list)
    expires_at: datetime
    can_confirm: bool


class ImportConfirmation(BaseModel):
    batch_id: str
    status: str
    inserted_rows: int
    overwritten_rows: int


class ImportBatchSummary(BaseModel):
    batch_id: str
    report_type: ReportType
    status: str
    file_name: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    inserted_rows: int = 0
    overwritten_rows: int = 0
    created_at: datetime
    expires_at: datetime | None = None


class ImportDataSummary(BaseModel):
    row_count: int = 0
    profile_ids: list[str] = Field(default_factory=list)
    date_start: str | None = None
    date_end: str | None = None


class ImportResult(BaseModel):
    report_type: ReportType
    file_name: str
    total_rows: int
    inserted_rows: int
    overwritten_rows: int


class ImportStore(Protocol):
    def upsert_shared(self, uploaded_by: str, report_type: ReportType, rows: list[dict[str, Any]]) -> tuple[int, int]: ...
    def query_shared_summary(self) -> dict[str, Any]: ...
    def create_preview(self, owner_id: str, report_type: ReportType, file_name: str, rows: list[dict[str, Any]], errors: list[ImportErrorRow]) -> ImportPreview: ...
    def confirm(self, batch_id: str, owner_id: str) -> ImportConfirmation | None: ...
    def get(self, batch_id: str, owner_id: str) -> ImportBatchSummary | None: ...
    def estimate_overwrites(self, owner_id: str, report_type: ReportType, rows: list[dict[str, Any]]) -> int: ...
    def query_summary(self, owner_id: str) -> dict[str, Any]: ...
    def close(self) -> None: ...


def _now() -> datetime: return datetime.now(timezone.utc)
def _identity(report_type: ReportType, row: dict[str, Any]) -> str:
    keys = [row["profile_id"], row["report_date"], row["campaign_id"]]
    if report_type in {ReportType.AD_GROUP, ReportType.KEYWORD, ReportType.TARGETING, ReportType.SEARCH_TERM}: keys.append(row["ad_group_id"])
    if report_type == ReportType.KEYWORD: keys.append(row["keyword_id"])
    if report_type == ReportType.TARGETING: keys.append(row["target_id"])
    if report_type == ReportType.SEARCH_TERM: keys.extend([row["search_term"], row["match_type"]])
    if report_type == ReportType.GENERIC:
        keys.extend([row["ad_group_id"], row["keyword_id"], row["target_id"], row["search_term"], row["match_type"]])
    if report_type in {ReportType.SEARCH_TERM, ReportType.SEARCH_TERM_SHARE}:
        keys.extend([row["ad_group_id"], row["target_id"], row["search_term"], row["match_type"]])
    if report_type in {ReportType.PURCHASED_PRODUCT, ReportType.ADVERTISED_PRODUCT, ReportType.PLACEMENT}:
        keys.extend([row["ad_group_id"], row["target_id"], row["match_type"]])
    return hashlib.sha256("\x1f".join(keys).encode()).hexdigest()


class InMemoryImportStore:
    def __init__(self) -> None:
        self._lock = RLock(); self._batches: dict[str, dict[str, Any]] = {}; self._rows: dict[tuple[str, str, str], dict[str, Any]] = {}; self._shared_rows: dict[tuple[str, str], dict[str, Any]] = {}
    def upsert_shared(self, uploaded_by: str, report_type: ReportType, rows: list[dict[str, Any]]) -> tuple[int, int]:
        with self._lock:
            overwritten = sum((report_type.value, row["identity_key"]) in self._shared_rows for row in rows)
            for row in rows:
                self._shared_rows[(report_type.value, row["identity_key"])] = {**row, "uploaded_by": uploaded_by}
        return len(rows) - overwritten, overwritten
    def query_shared_summary(self) -> dict[str, Any]:
        rows = list(self._shared_rows.values())
        if not rows: return {"row_count": 0}
        return {"row_count": len(rows), "profile_ids": sorted({item["profile_id"] for item in rows}), "date_start": min(item["report_date"] for item in rows), "date_end": max(item["report_date"] for item in rows), "spend": sum(item["spend"] for item in rows), "sales": sum(item["sales"] for item in rows), "orders": sum(item["orders"] for item in rows)}
    def _cleanup(self) -> None:
        now = _now()
        for batch_id in [key for key, item in self._batches.items() if item["status"] == "pending" and item["expires_at"] < now]: self._batches[key]["status"] = "expired"
    def estimate_overwrites(self, owner_id: str, report_type: ReportType, rows: list[dict[str, Any]]) -> int:
        return sum((owner_id, report_type.value, row["identity_key"]) in self._rows for row in rows)
    def create_preview(self, owner_id: str, report_type: ReportType, file_name: str, rows: list[dict[str, Any]], errors: list[ImportErrorRow]) -> ImportPreview:
        self._cleanup(); batch_id = f"ad-import-{uuid4().hex}"; expires = _now() + PREVIEW_TTL
        item = {"owner_id": owner_id, "report_type": report_type, "file_name": file_name, "rows": rows, "errors": errors, "status": "pending", "created_at": _now(), "expires_at": expires, "inserted": 0, "overwritten": 0}
        with self._lock: self._batches[batch_id] = item
        return ImportPreview(batch_id=batch_id, report_type=report_type, file_name=file_name, total_rows=len(rows)+len(errors), valid_rows=len(rows), invalid_rows=len(errors), estimated_overwrites=self.estimate_overwrites(owner_id, report_type, rows), errors=errors[:100], expires_at=expires, can_confirm=not errors and bool(rows))
    def confirm(self, batch_id: str, owner_id: str) -> ImportConfirmation | None:
        self._cleanup()
        with self._lock:
            item = self._batches.get(batch_id)
            if not item or item["owner_id"] != owner_id: return None
            if item["status"] == "confirmed": return ImportConfirmation(batch_id=batch_id, status="confirmed", inserted_rows=item["inserted"], overwritten_rows=item["overwritten"])
            if item["status"] != "pending" or item["errors"] or not item["rows"]: return ImportConfirmation(batch_id=batch_id, status=item["status"], inserted_rows=0, overwritten_rows=0)
            overwritten = self.estimate_overwrites(owner_id, item["report_type"], item["rows"])
            for row in item["rows"]: self._rows[(owner_id, item["report_type"].value, row["identity_key"])] = row
            item.update(status="confirmed", inserted=len(item["rows"])-overwritten, overwritten=overwritten)
            return ImportConfirmation(batch_id=batch_id, status="confirmed", inserted_rows=item["inserted"], overwritten_rows=overwritten)
    def get(self, batch_id: str, owner_id: str) -> ImportBatchSummary | None:
        self._cleanup(); item = self._batches.get(batch_id)
        if not item or item["owner_id"] != owner_id: return None
        return ImportBatchSummary(batch_id=batch_id, report_type=item["report_type"], status=item["status"], file_name=item["file_name"], total_rows=len(item["rows"])+len(item["errors"]), valid_rows=len(item["rows"]), invalid_rows=len(item["errors"]), inserted_rows=item["inserted"], overwritten_rows=item["overwritten"], created_at=item["created_at"], expires_at=item["expires_at"] if item["status"] == "pending" else None)
    def query_summary(self, owner_id: str) -> dict[str, Any]:
        rows = [item for (owner, _, _), item in self._rows.items() if owner == owner_id]
        if not rows: return {"row_count": 0}
        return {"row_count": len(rows), "profile_ids": sorted({item["profile_id"] for item in rows}), "date_start": min(item["report_date"] for item in rows), "date_end": max(item["report_date"] for item in rows), "spend": sum(item["spend"] for item in rows), "sales": sum(item["sales"] for item in rows), "orders": sum(item["orders"] for item in rows)}
    def close(self) -> None: return None


class PostgresImportStore:
    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(conninfo=database_url, min_size=0, max_size=10, open=False); self._ready = False; self._lock = RLock()
    def _ensure(self) -> None:
        if self._ready: return
        with self._lock:
            if self._ready: return
            self._pool.open(wait=True, timeout=10)
            with self._pool.connection() as conn, conn.transaction():
                conn.execute("CREATE TABLE IF NOT EXISTS shared_imported_advertising_report_rows (report_type TEXT NOT NULL, identity_key TEXT NOT NULL, profile_id TEXT NOT NULL, report_date DATE NOT NULL, campaign_id TEXT NOT NULL, ad_group_id TEXT NOT NULL DEFAULT '', keyword_id TEXT NOT NULL DEFAULT '', target_id TEXT NOT NULL DEFAULT '', search_term TEXT NOT NULL DEFAULT '', match_type TEXT NOT NULL DEFAULT '', payload JSONB NOT NULL, impressions DOUBLE PRECISION NOT NULL, clicks DOUBLE PRECISION NOT NULL, spend DOUBLE PRECISION NOT NULL, sales DOUBLE PRECISION NOT NULL, orders DOUBLE PRECISION NOT NULL, ad_units DOUBLE PRECISION NOT NULL, uploaded_by UUID, updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (report_type, identity_key))")
                conn.execute("CREATE INDEX IF NOT EXISTS shared_imported_ad_rows_profile_date_idx ON shared_imported_advertising_report_rows (profile_id, report_date)")
                if conn.execute("SELECT to_regclass('public.imported_advertising_report_rows')").fetchone()[0]:
                    conn.execute("""INSERT INTO shared_imported_advertising_report_rows
                        (report_type, identity_key, profile_id, report_date, campaign_id, ad_group_id, keyword_id, target_id, search_term, match_type, payload, impressions, clicks, spend, sales, orders, ad_units, uploaded_by, updated_at)
                        SELECT DISTINCT ON (report_type, identity_key)
                        report_type, identity_key, profile_id, report_date, campaign_id, ad_group_id, keyword_id, target_id, search_term, match_type, payload, impressions, clicks, spend, sales, orders, ad_units, owner_id, updated_at
                        FROM imported_advertising_report_rows
                        ORDER BY report_type, identity_key, updated_at DESC
                        ON CONFLICT (report_type, identity_key) DO NOTHING""")
                conn.execute("COMMENT ON TABLE shared_imported_advertising_report_rows IS '全员共享的已确认广告报表明细；所有登录用户查询同一份数据，不按上传者隔离。'")
                conn.execute("COMMENT ON COLUMN shared_imported_advertising_report_rows.profile_id IS 'Amazon 广告店铺/授权 Profile ID；用于区分同一团队下的不同店铺。'")
                conn.execute("COMMENT ON COLUMN shared_imported_advertising_report_rows.uploaded_by IS '最近一次写入该共享报表行的用户，仅用于审计，不参与数据隔离。'")
            self._ready = True
    def upsert_shared(self, uploaded_by: str, report_type: ReportType, rows: list[dict[str, Any]]) -> tuple[int, int]:
        self._ensure()
        if not rows: return 0, 0
        keys = [row["identity_key"] for row in rows]
        with self._pool.connection() as conn, conn.transaction():
            overwritten = conn.execute("SELECT count(*) FROM shared_imported_advertising_report_rows WHERE report_type=%s AND identity_key = ANY(%s)", (report_type.value, keys)).fetchone()[0]
            with conn.cursor() as cursor:
                cursor.executemany("""INSERT INTO shared_imported_advertising_report_rows
                    (report_type,identity_key,profile_id,report_date,campaign_id,ad_group_id,keyword_id,target_id,search_term,match_type,payload,impressions,clicks,spend,sales,orders,ad_units,uploaded_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (report_type,identity_key) DO UPDATE SET
                    profile_id=EXCLUDED.profile_id,report_date=EXCLUDED.report_date,campaign_id=EXCLUDED.campaign_id,ad_group_id=EXCLUDED.ad_group_id,keyword_id=EXCLUDED.keyword_id,target_id=EXCLUDED.target_id,search_term=EXCLUDED.search_term,match_type=EXCLUDED.match_type,payload=EXCLUDED.payload,impressions=EXCLUDED.impressions,clicks=EXCLUDED.clicks,spend=EXCLUDED.spend,sales=EXCLUDED.sales,orders=EXCLUDED.orders,ad_units=EXCLUDED.ad_units,uploaded_by=EXCLUDED.uploaded_by,updated_at=CURRENT_TIMESTAMP""", [(report_type.value,row['identity_key'],row['profile_id'],row['report_date'],row['campaign_id'],row['ad_group_id'],row['keyword_id'],row['target_id'],row['search_term'],row['match_type'],Jsonb(row),row['impressions'],row['clicks'],row['spend'],row['sales'],row['orders'],row['ad_units'],uploaded_by) for row in rows])
        return len(rows) - overwritten, overwritten
    def query_shared_summary(self) -> dict[str, Any]:
        self._ensure()
        with self._pool.connection() as conn:
            row = conn.execute("SELECT count(*),min(report_date),max(report_date),coalesce(sum(spend),0),coalesce(sum(sales),0),coalesce(sum(orders),0) FROM shared_imported_advertising_report_rows").fetchone()
            profiles = conn.execute("SELECT array_agg(DISTINCT profile_id) FROM shared_imported_advertising_report_rows").fetchone()[0]
        return {"row_count":row[0],"profile_ids":profiles or [],"date_start":row[1].isoformat() if row[1] else None,"date_end":row[2].isoformat() if row[2] else None,"spend":row[3],"sales":row[4],"orders":row[5]}
    def _cleanup(self) -> None:
        self._ensure()
        with self._pool.connection() as conn, conn.transaction(): conn.execute("UPDATE advertising_report_import_batches SET status='expired' WHERE status='pending' AND expires_at < %s", (_now(),))
    def estimate_overwrites(self, owner_id: str, report_type: ReportType, rows: list[dict[str, Any]]) -> int:
        self._ensure()
        if not rows: return 0
        keys = [row["identity_key"] for row in rows]
        with self._pool.connection() as conn: return conn.execute("SELECT count(*) FROM imported_advertising_report_rows WHERE owner_id=%s AND report_type=%s AND identity_key = ANY(%s)", (owner_id, report_type.value, keys)).fetchone()[0]
    def create_preview(self, owner_id: str, report_type: ReportType, file_name: str, rows: list[dict[str, Any]], errors: list[ImportErrorRow]) -> ImportPreview:
        self._cleanup(); batch_id = f"ad-import-{uuid4().hex}"; now = _now(); expires = now + PREVIEW_TTL; overwrites = self.estimate_overwrites(owner_id, report_type, rows)
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO advertising_report_import_batches (id,owner_id,report_type,file_name,status,total_rows,valid_rows,invalid_rows,created_at,expires_at) VALUES (%s,%s,%s,%s,'pending',%s,%s,%s,%s,%s)", (batch_id, owner_id, report_type.value, file_name, len(rows)+len(errors), len(rows), len(errors), now, expires))
            with conn.cursor() as cursor:
                if rows: cursor.executemany("INSERT INTO advertising_report_import_staging (batch_id,row_payload) VALUES (%s,%s)", [(batch_id, Jsonb(row)) for row in rows])
                if errors: cursor.executemany("INSERT INTO advertising_report_import_errors (batch_id,row_number,field_name,message) VALUES (%s,%s,%s,%s)", [(batch_id, err.row_number, err.field, err.message) for err in errors])
        return ImportPreview(batch_id=batch_id, report_type=report_type, file_name=file_name, total_rows=len(rows)+len(errors), valid_rows=len(rows), invalid_rows=len(errors), estimated_overwrites=overwrites, errors=errors[:100], expires_at=expires, can_confirm=not errors and bool(rows))
    def confirm(self, batch_id: str, owner_id: str) -> ImportConfirmation | None:
        self._cleanup()
        with self._pool.connection() as conn, conn.transaction():
            batch = conn.execute("SELECT report_type,status,valid_rows,invalid_rows,inserted_rows,overwritten_rows FROM advertising_report_import_batches WHERE id=%s AND owner_id=%s FOR UPDATE", (batch_id,owner_id)).fetchone()
            if not batch: return None
            if batch[1] == 'confirmed': return ImportConfirmation(batch_id=batch_id,status='confirmed',inserted_rows=batch[4],overwritten_rows=batch[5])
            if batch[1] != 'pending' or batch[3] or not batch[2]: return ImportConfirmation(batch_id=batch_id,status=batch[1],inserted_rows=0,overwritten_rows=0)
            rows = [row[0] for row in conn.execute("SELECT row_payload FROM advertising_report_import_staging WHERE batch_id=%s", (batch_id,)).fetchall()]
            keys = [row['identity_key'] for row in rows]; overwritten = conn.execute("SELECT count(*) FROM imported_advertising_report_rows WHERE owner_id=%s AND report_type=%s AND identity_key=ANY(%s)", (owner_id,batch[0],keys)).fetchone()[0]
            with conn.cursor() as cursor:
                cursor.executemany("INSERT INTO imported_advertising_report_rows (owner_id,report_type,identity_key,profile_id,report_date,campaign_id,ad_group_id,keyword_id,target_id,search_term,match_type,payload,impressions,clicks,spend,sales,orders,ad_units) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (owner_id,report_type,identity_key) DO UPDATE SET payload=EXCLUDED.payload,impressions=EXCLUDED.impressions,clicks=EXCLUDED.clicks,spend=EXCLUDED.spend,sales=EXCLUDED.sales,orders=EXCLUDED.orders,ad_units=EXCLUDED.ad_units,updated_at=CURRENT_TIMESTAMP", [(owner_id,batch[0],row['identity_key'],row['profile_id'],row['report_date'],row['campaign_id'],row['ad_group_id'],row['keyword_id'],row['target_id'],row['search_term'],row['match_type'],Jsonb(row),row['impressions'],row['clicks'],row['spend'],row['sales'],row['orders'],row['ad_units']) for row in rows])
            conn.execute("UPDATE advertising_report_import_batches SET status='confirmed',inserted_rows=%s,overwritten_rows=%s,expires_at=NULL WHERE id=%s", (len(rows)-overwritten,overwritten,batch_id))
        return ImportConfirmation(batch_id=batch_id,status='confirmed',inserted_rows=len(rows)-overwritten,overwritten_rows=overwritten)
    def get(self, batch_id: str, owner_id: str) -> ImportBatchSummary | None:
        self._cleanup()
        with self._pool.connection() as conn: row=conn.execute("SELECT id,report_type,status,file_name,total_rows,valid_rows,invalid_rows,inserted_rows,overwritten_rows,created_at,expires_at FROM advertising_report_import_batches WHERE id=%s AND owner_id=%s",(batch_id,owner_id)).fetchone()
        return ImportBatchSummary(batch_id=row[0],report_type=row[1],status=row[2],file_name=row[3],total_rows=row[4],valid_rows=row[5],invalid_rows=row[6],inserted_rows=row[7],overwritten_rows=row[8],created_at=row[9],expires_at=row[10]) if row else None
    def query_summary(self, owner_id: str) -> dict[str, Any]:
        self._ensure()
        with self._pool.connection() as conn: row=conn.execute("SELECT count(*),min(report_date),max(report_date),coalesce(sum(spend),0),coalesce(sum(sales),0),coalesce(sum(orders),0) FROM imported_advertising_report_rows WHERE owner_id=%s",(owner_id,)).fetchone(); profiles=conn.execute("SELECT array_agg(DISTINCT profile_id) FROM imported_advertising_report_rows WHERE owner_id=%s",(owner_id,)).fetchone()[0]
        return {"row_count":row[0],"profile_ids":profiles or [],"date_start":row[1].isoformat() if row[1] else None,"date_end":row[2].isoformat() if row[2] else None,"spend":row[3],"sales":row[4],"orders":row[5]}
    def close(self) -> None: self._pool.close()


class AdvertisingImportService:
    def __init__(self, store: ImportStore) -> None: self.store = store
    def template(self, report_type: ReportType) -> str: return '\ufeff' + ','.join(HEADERS[report_type]) + '\n'
    def import_file(self, *, uploaded_by: str, report_type: ReportType | None, file_name: str, content: bytes) -> ImportResult:
        if not file_name or len(content) == 0: raise ValueError("文件不能为空")
        if len(content) > MAX_FILE_BYTES: raise ValueError("文件不能超过 20 MB")
        resolved_type, rows, errors = self._parse(report_type, file_name, content)
        if errors:
            first = errors[0]
            field = f"（{first.field}）" if first.field else ""
            raise ValueError(f"第 {first.row_number} 行{field}：{first.message}；请修正后重新上传。")
        if not rows: raise ValueError("文件没有可导入的数据行")
        inserted, overwritten = self.store.upsert_shared(uploaded_by, resolved_type, rows)
        return ImportResult(report_type=resolved_type, file_name=file_name, total_rows=len(rows), inserted_rows=inserted, overwritten_rows=overwritten)
    def preview(self, *, owner_id: str, report_type: ReportType | None, file_name: str, content: bytes) -> ImportPreview:
        if not file_name or len(content) == 0: raise ValueError("文件不能为空")
        if len(content) > MAX_FILE_BYTES: raise ValueError("文件不能超过 20 MB")
        report_type, rows, errors = self._parse(report_type, file_name, content)
        return self.store.create_preview(owner_id,report_type,file_name,rows,errors)
    def _parse(self, report_type: ReportType | None, file_name: str, content: bytes) -> tuple[ReportType,list[dict[str,Any]],list[ImportErrorRow]]:
        suffix=file_name.rsplit('.',1)[-1].lower() if '.' in file_name else ''
        if suffix == 'csv': raw=list(csv.reader(io.StringIO(self._decode_csv(content))))
        elif suffix == 'xlsx':
            try:
                workbook=load_workbook(io.BytesIO(content),read_only=True,data_only=True); workbook.active.reset_dimensions(); raw=[["" if value is None else str(value) for value in row] for row in workbook.active.iter_rows(values_only=True)]; workbook.close()
            except Exception as exc: raise ValueError("XLSX 文件无法读取") from exc
        else: raise ValueError("仅支持 CSV 或 XLSX 文件")
        if not raw: raise ValueError("文件不能为空")
        headers=[item.strip().lstrip('\ufeff') for item in raw[0]]
        while headers and not headers[-1]: headers.pop()
        if len(headers)!=len(set(headers)): raise ValueError("表头存在重复字段")
        native_type = self._native_report_type(headers)
        if report_type is None and native_type is not None:
            report_type = native_type
        if report_type is None:
            raise ValueError("无法识别 Amazon 广告报表类型，请上传搜索词、展示量份额、已购买商品、推广商品或广告位报告")
        if native_type is not None:
            store_key = self._store_key(file_name)
            valid=[]; errors=[]
            for number,cells in enumerate(raw[1:],start=2):
                while cells and not str(cells[-1]).strip(): cells.pop()
                if not any(str(item).strip() for item in cells): continue
                if len(cells)!=len(headers): errors.append(ImportErrorRow(row_number=number,message="列数与表头不一致")); continue
                result,error=self._normalize_native(report_type,dict(zip(headers,[str(item).strip() for item in cells])),store_key)
                if error: errors.append(ImportErrorRow(row_number=number,field=error[0],message=error[1]))
                else: valid.append(result)
            if not valid and not errors: raise ValueError("文件没有可导入的数据行")
            return report_type,valid,errors
        if report_type == ReportType.GENERIC:
            canonical_headers = [self._generic_header(item) for item in headers]
            if len(canonical_headers) != len(set(canonical_headers)):
                raise ValueError("表头存在重复或同义字段")
            missing = [name for name in ("Profile ID", "日期", "Campaign ID", "曝光", "点击", "花费", "销售额", "订单", "广告销量") if name not in canonical_headers]
            if missing: raise ValueError(f"通用模板缺少必填列：{', '.join(missing)}")
        elif headers != list(HEADERS[report_type]): raise ValueError("表头与所选报表模板不匹配")
        if len(raw)-1 > MAX_ROWS: raise ValueError("文件不能超过 10 万行")
        valid=[]; errors=[]
        for number,cells in enumerate(raw[1:],start=2):
            if not any(str(item).strip() for item in cells): continue
            while cells and not str(cells[-1]).strip(): cells.pop()
            if len(cells)!=len(headers): errors.append(ImportErrorRow(row_number=number,message="列数与模板不一致")); continue
            keys = canonical_headers if report_type == ReportType.GENERIC else headers
            data=dict(zip(keys,[str(item).strip() for item in cells])); result,error=self._normalize(report_type,data)
            if error: errors.append(ImportErrorRow(row_number=number,field=error[0],message=error[1]))
            else: valid.append(result)
        if not valid and not errors: raise ValueError("文件没有可导入的数据行")
        return report_type,valid,errors
    @staticmethod
    def _decode_csv(content: bytes) -> str:
        for encoding in ('utf-8-sig','utf-8','gb18030'):
            try: return content.decode(encoding)
            except UnicodeDecodeError: continue
        raise ValueError("CSV 编码仅支持 UTF-8 或 GB18030")
    @staticmethod
    def _generic_header(value: str) -> str:
        normalized = " ".join(value.replace("_", " ").split()).lower()
        return GENERIC_HEADER_ALIASES.get(normalized, value)
    @staticmethod
    def _store_key(file_name: str) -> str:
        prefix = file_name.split("_", 1)[0].strip()
        match = re.match(r"^(.+?)-(?:\d{1,2}月|\d{4}[-_]\d{1,2})-商品推广", prefix)
        return (match.group(1) if match else prefix) or "未命名店铺"
    @staticmethod
    def _native_report_type(headers: list[str]) -> ReportType | None:
        values=set(headers)
        if "搜索词展示量排名" in values and "搜索词展示量份额" in values: return ReportType.SEARCH_TERM_SHARE
        if "已购买的ASIN" in values: return ReportType.PURCHASED_PRODUCT
        if "放置" in values: return ReportType.PLACEMENT
        if "广告SKU" in values and "客户搜索词" not in values and "投放" not in values: return ReportType.ADVERTISED_PRODUCT
        if "客户搜索词" in values: return ReportType.SEARCH_TERM
        return None
    def _normalize_native(self, report_type: ReportType, data: dict[str,str], store_key: str) -> tuple[dict[str,Any]|None,tuple[str,str]|None]:
        for name in ("日期", "广告活动名称"):
            if not data.get(name): return None,(name,"必填字段不能为空")
        try: report_date=date.fromisoformat(data["日期"][:10]).isoformat()
        except ValueError: return None,("日期","日期必须有效")
        def number(*names: str) -> float:
            for name in names:
                value=data.get(name)
                if value not in (None, ""):
                    parsed=float(value.replace(",",""))
                    if parsed < 0: raise ValueError
                    return parsed
            return 0.0
        try:
            metrics={"impressions":number("展示量"),"clicks":number("点击量"),"spend":number("花费"),"sales":number("7天总销售额","7 天内的总销售额"),"orders":number("7天总订单数(#)","7 天内的总订单量 (#)"),"ad_units":number("7天总销售量(#)","7天内广告SKU销售量(#)")}
        except ValueError: return None,("绩效指标","必须为非负数值")
        target = data.get("投放", "")
        if report_type == ReportType.PURCHASED_PRODUCT: target=data.get("已购买的ASIN", "")
        elif report_type == ReportType.ADVERTISED_PRODUCT: target=data.get("广告ASIN", data.get("广告SKU", ""))
        elif report_type == ReportType.PLACEMENT: target=data.get("放置", "")
        row={"profile_id":store_key,"report_date":report_date,"campaign_id":data["广告活动名称"],"ad_group_id":data.get("广告组名称", ""),"keyword_id":"","target_id":target,"search_term":data.get("客户搜索词", ""),"match_type":data.get("匹配类型", ""),**metrics,"source":data}
        row["identity_key"]=_identity(report_type,row)
        return row,None
    def _normalize(self, report_type: ReportType, data: dict[str,str]) -> tuple[dict[str,Any]|None,tuple[str,str]|None]:
        required=['Profile ID','日期','Campaign ID']
        extras={ReportType.AD_GROUP:['Ad Group ID'],ReportType.KEYWORD:['Ad Group ID','Keyword ID'],ReportType.TARGETING:['Ad Group ID','Target ID'],ReportType.SEARCH_TERM:['Ad Group ID','Search Term','匹配类型']}
        required.extend(extras.get(report_type,[]))
        for name in required:
            if not data.get(name): return None,(name,'必填字段不能为空')
        try: report_date=date.fromisoformat(data['日期']).isoformat()
        except ValueError: return None,('日期','日期必须为 YYYY-MM-DD')
        metrics={}
        for name,key in [('曝光','impressions'),('点击','clicks'),('花费','spend'),('销售额','sales'),('订单','orders'),('广告销量','ad_units')]:
            try:
                value=float(data[name].replace(',',''))
                if value < 0: raise ValueError
                metrics[key]=value
            except ValueError: return None,(name,'必须为非负数值')
        row={'profile_id':data['Profile ID'],'report_date':report_date,'campaign_id':data['Campaign ID'],'ad_group_id':data.get('Ad Group ID',''),'keyword_id':data.get('Keyword ID',''),'target_id':data.get('Target ID',''),'search_term':data.get('Search Term',''),'match_type':data.get('匹配类型',''),**metrics,'source':data}
        row['identity_key']=_identity(report_type,row); return row,None

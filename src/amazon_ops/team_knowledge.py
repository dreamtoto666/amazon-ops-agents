"""Versioned, team-shared Obsidian Markdown knowledge base with Wiki retrieval."""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_MARKDOWN_FILES = 2_000
MAX_CHUNK_CHARS = 1_600
MAX_RETRIEVAL_CHUNKS = 6
MAX_POSTGRES_CANDIDATES = 200


class TeamKnowledgeError(RuntimeError):
    pass


class TeamKnowledgeUnavailable(TeamKnowledgeError):
    pass


class KnowledgeStatus(BaseModel):
    status: str
    version_id: str | None = None
    uploaded_by: str | None = None
    uploaded_at: datetime | None = None
    document_count: int = 0
    chunk_count: int = 0
    error: str | None = None


class KnowledgeSnippet(BaseModel):
    path: str
    heading: str | None = None
    content: str
    score: float = 0
    untrusted_business_data: bool = True


class KnowledgeStore(Protocol):
    def initialize(self) -> None: ...
    def create_candidate(self, uploaded_by: str) -> str: ...
    def replace_candidate(self, version_id: str, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None: ...
    def activate(self, version_id: str) -> None: ...
    def fail(self, version_id: str, message: str) -> None: ...
    def status(self) -> KnowledgeStatus: ...
    def search(self, question: str, limit: int) -> list[KnowledgeSnippet]: ...
    def close(self) -> None: ...


class InMemoryKnowledgeStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._versions: dict[str, dict[str, Any]] = {}
        self._active: str | None = None

    def initialize(self) -> None:
        return None

    def create_candidate(self, uploaded_by: str) -> str:
        version_id = f"vault-{uuid4().hex}"
        with self._lock:
            self._versions[version_id] = {"uploaded_by": uploaded_by, "uploaded_at": _now(), "status": "indexing", "documents": [], "chunks": [], "error": None}
        return version_id

    def replace_candidate(self, version_id: str, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        with self._lock:
            self._versions[version_id].update(documents=documents, chunks=chunks)

    def activate(self, version_id: str) -> None:
        with self._lock:
            candidate = self._versions.get(version_id)
            if candidate is None or candidate["status"] != "indexing":
                raise TeamKnowledgeError("知识库版本状态不允许激活")
            if self._active:
                self._versions[self._active]["status"] = "archived"
            candidate["status"] = "active"
            self._active = version_id

    def fail(self, version_id: str, message: str) -> None:
        with self._lock:
            if version_id in self._versions:
                self._versions[version_id].update(status="failed", error=message)

    def status(self) -> KnowledgeStatus:
        with self._lock:
            if self._active:
                item = self._versions[self._active]
                return KnowledgeStatus(status="active", version_id=self._active, uploaded_by=item["uploaded_by"], uploaded_at=item["uploaded_at"], document_count=len(item["documents"]), chunk_count=len(item["chunks"]))
            candidate = next((item for item in self._versions.values() if item["status"] == "indexing"), None)
            return KnowledgeStatus(status="indexing" if candidate else "empty")

    def search(self, question: str, limit: int) -> list[KnowledgeSnippet]:
        with self._lock:
            chunks = list(self._versions.get(self._active, {}).get("chunks", []))
        return _rank_chunks(chunks, question, limit)

    def close(self) -> None:
        return None


class PostgresKnowledgeStore:
    def __init__(self, database_url: str) -> None:
        self._pool = ConnectionPool(conninfo=database_url, min_size=0, max_size=5, open=False)
        self._ready = False
        self._lock = RLock()

    def initialize(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            self._pool.open(wait=True, timeout=10)
            with self._pool.connection() as conn, conn.transaction():
                conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                conn.execute("CREATE TABLE IF NOT EXISTS team_knowledge_versions (id TEXT PRIMARY KEY, uploaded_by UUID NOT NULL, uploaded_at TIMESTAMPTZ NOT NULL, status TEXT NOT NULL, error_message TEXT, active_at TIMESTAMPTZ)")
                conn.execute("CREATE TABLE IF NOT EXISTS team_knowledge_documents (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES team_knowledge_versions(id) ON DELETE CASCADE, path TEXT NOT NULL, frontmatter JSONB NOT NULL, content_hash TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', tags TEXT[] NOT NULL DEFAULT '{}', wiki_links TEXT[] NOT NULL DEFAULT '{}', UNIQUE(version_id,path))")
                conn.execute("CREATE TABLE IF NOT EXISTS team_knowledge_chunks (id TEXT PRIMARY KEY, version_id TEXT NOT NULL REFERENCES team_knowledge_versions(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES team_knowledge_documents(id) ON DELETE CASCADE, path TEXT NOT NULL, heading TEXT, content TEXT NOT NULL, search_text TEXT NOT NULL DEFAULT '', wiki_terms TEXT[] NOT NULL DEFAULT '{}')")
                # Upgrade the earlier semantic-index schema without losing its Markdown content.
                conn.execute("ALTER TABLE team_knowledge_documents ADD COLUMN IF NOT EXISTS title TEXT NOT NULL DEFAULT ''")
                conn.execute("ALTER TABLE team_knowledge_documents ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}'")
                conn.execute("ALTER TABLE team_knowledge_documents ADD COLUMN IF NOT EXISTS wiki_links TEXT[] NOT NULL DEFAULT '{}'")
                conn.execute("ALTER TABLE team_knowledge_chunks ADD COLUMN IF NOT EXISTS search_text TEXT NOT NULL DEFAULT ''")
                conn.execute("ALTER TABLE team_knowledge_chunks ADD COLUMN IF NOT EXISTS wiki_terms TEXT[] NOT NULL DEFAULT '{}'")
                conn.execute("UPDATE team_knowledge_chunks SET search_text=concat_ws(' ', path, heading, content) WHERE search_text=''")
                conn.execute("ALTER TABLE team_knowledge_chunks DROP COLUMN IF EXISTS embedding")
                conn.execute("CREATE INDEX IF NOT EXISTS team_knowledge_chunks_version_idx ON team_knowledge_chunks(version_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS team_knowledge_chunks_search_trgm_idx ON team_knowledge_chunks USING GIN (search_text gin_trgm_ops)")
                conn.execute("CREATE INDEX IF NOT EXISTS team_knowledge_chunks_terms_idx ON team_knowledge_chunks USING GIN (wiki_terms)")
                conn.execute("COMMENT ON TABLE team_knowledge_versions IS '团队共享 Obsidian Vault 的候选、当前和历史版本。'")
                conn.execute("COMMENT ON TABLE team_knowledge_documents IS '当前或历史 Vault 中解析出的 Markdown 笔记及其 Wiki 元数据。'")
                conn.execute("COMMENT ON TABLE team_knowledge_chunks IS 'Markdown 笔记的 Wiki 检索分块，按标题、路径、标签、双链和正文短语匹配。'")
            self._ready = True

    def create_candidate(self, uploaded_by: str) -> str:
        self.initialize()
        version_id = f"vault-{uuid4().hex}"
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("INSERT INTO team_knowledge_versions (id,uploaded_by,uploaded_at,status) VALUES (%s,%s,%s,'indexing')", (version_id, uploaded_by, _now()))
        return version_id

    def replace_candidate(self, version_id: str, documents: list[dict[str, Any]], chunks: list[dict[str, Any]]) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction(), conn.cursor() as cursor:
            cursor.executemany("INSERT INTO team_knowledge_documents (id,version_id,path,frontmatter,content_hash,title,tags,wiki_links) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [(item["id"], version_id, item["path"], Jsonb(item["frontmatter"]), item["content_hash"], item["title"], item["tags"], item["wiki_links"]) for item in documents])
            cursor.executemany("INSERT INTO team_knowledge_chunks (id,version_id,document_id,path,heading,content,search_text,wiki_terms) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", [(item["id"], version_id, item["document_id"], item["path"], item["heading"], item["content"], item["search_text"], item["wiki_terms"]) for item in chunks])

    def activate(self, version_id: str) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("UPDATE team_knowledge_versions SET status='archived' WHERE status='active'")
            activated = conn.execute("UPDATE team_knowledge_versions SET status='active',active_at=%s,error_message=NULL WHERE id=%s AND status='indexing'", (_now(), version_id))
            if activated.rowcount == 0:
                # Raising here rolls the transaction back, so the previously
                # active version is not archived into an empty knowledge base.
                raise TeamKnowledgeError("知识库版本状态不允许激活")

    def fail(self, version_id: str, message: str) -> None:
        self.initialize()
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("UPDATE team_knowledge_versions SET status='failed',error_message=%s WHERE id=%s AND status='indexing'", (message[:500], version_id))

    def status(self) -> KnowledgeStatus:
        self.initialize()
        with self._pool.connection() as conn:
            row = conn.execute("SELECT v.id,v.uploaded_by,v.uploaded_at,v.status,v.error_message,(SELECT count(*) FROM team_knowledge_documents d WHERE d.version_id=v.id),(SELECT count(*) FROM team_knowledge_chunks c WHERE c.version_id=v.id) FROM team_knowledge_versions v WHERE v.status IN ('active','indexing') ORDER BY CASE v.status WHEN 'active' THEN 0 ELSE 1 END,uploaded_at DESC LIMIT 1").fetchone()
        return KnowledgeStatus(status=row[3], version_id=row[0], uploaded_by=str(row[1]), uploaded_at=row[2], error=row[4], document_count=row[5], chunk_count=row[6]) if row else KnowledgeStatus(status="empty")

    def search(self, question: str, limit: int) -> list[KnowledgeSnippet]:
        self.initialize()
        normalized = _normalize(question)
        if not normalized:
            return []
        terms = _query_terms(question)
        patterns = [f"%{term}%" for term in terms if len(term) >= 2]
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT c.path,c.heading,c.content,c.search_text,c.wiki_terms FROM team_knowledge_chunks c JOIN team_knowledge_versions v ON v.id=c.version_id WHERE v.status='active' AND (c.wiki_terms && %s::text[] OR c.search_text ILIKE ANY(%s::text[]) OR similarity(c.search_text,%s) > 0.04) ORDER BY similarity(c.search_text,%s) DESC LIMIT %s", (terms, patterns or ["%__never__%"], normalized, normalized, MAX_POSTGRES_CANDIDATES)).fetchall()
        candidates = [{"path": row[0], "heading": row[1], "content": row[2], "search_text": row[3], "wiki_terms": row[4]} for row in rows]
        return _rank_chunks(candidates, question, limit)

    def close(self) -> None:
        self._pool.close()


@dataclass
class TeamKnowledgeService:
    store: KnowledgeStore
    archive_dir: Path

    def initialize(self) -> None:
        self.store.initialize()
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def status(self) -> KnowledgeStatus:
        try:
            return self.store.status()
        except Exception:
            return KnowledgeStatus(status="unavailable")

    def upload(self, *, uploaded_by: str, file_name: str, content: bytes) -> KnowledgeStatus:
        if not file_name.lower().endswith(".zip"):
            raise TeamKnowledgeError("仅支持 ZIP 格式的 Obsidian Vault")
        if not content or len(content) > MAX_ZIP_BYTES:
            raise TeamKnowledgeError("ZIP 文件不能为空且不能超过 50 MB")
        try:
            version_id = self.store.create_candidate(uploaded_by)
        except Exception as exc:
            raise TeamKnowledgeUnavailable("知识库暂时不可用，请稍后重试") from exc
        archive = self.archive_dir / f"{version_id}.zip"
        try:
            documents = _parse_vault(content)
            chunks = _build_chunks(documents)
            archive.write_bytes(content)
            self.store.replace_candidate(version_id, documents, chunks)
            self.store.activate(version_id)
            return self.store.status()
        except TeamKnowledgeError as exc:
            archive.unlink(missing_ok=True)
            self.store.fail(version_id, str(exc))
            raise
        except Exception as exc:
            archive.unlink(missing_ok=True)
            self.store.fail(version_id, "知识库索引失败")
            raise TeamKnowledgeUnavailable("知识库索引失败，请检查 ZIP 内容") from exc

    def search(self, question: str) -> list[KnowledgeSnippet]:
        if self.status().status != "active":
            return []
        try:
            return self.store.search(question, MAX_RETRIEVAL_CHUNKS)
        except Exception:
            return []


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_member(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts:
        raise TeamKnowledgeError("ZIP 包含不安全路径")
    if any(part.startswith(".") for part in path.parts) or ".obsidian" in path.parts:
        return None
    return path


def _parse_vault(content: bytes) -> list[dict[str, Any]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise TeamKnowledgeError("ZIP 文件无法读取") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise TeamKnowledgeError("ZIP 文件数量超出限制")
        # ``file_size`` is declared by the archive, but it is still a safe bound:
        # ``read`` stops at the declared size, and a declared size that disagrees
        # with the actual deflate stream fails the CRC check below.
        if sum(item.file_size for item in infos) > MAX_UNCOMPRESSED_BYTES:
            raise TeamKnowledgeError("ZIP 解压后内容超出限制")
        paths: set[str] = set()
        documents: list[dict[str, Any]] = []
        for info in infos:
            if info.is_dir() or (info.external_attr >> 16) & 0o170000 == 0o120000:
                continue
            path = _safe_member(info.filename)
            if path is None or path.suffix.lower() != ".md":
                continue
            key = path.as_posix().casefold()
            if key in paths:
                raise TeamKnowledgeError("ZIP 包含重复笔记路径")
            paths.add(key)
            if len(documents) >= MAX_MARKDOWN_FILES:
                raise TeamKnowledgeError("Markdown 笔记数量超出限制")
            if info.flag_bits & 0x1:
                # Reading an encrypted entry raises a bare RuntimeError, which is
                # too broad to catch; the archive must say so instead.
                raise TeamKnowledgeError(f"ZIP 包含加密笔记，无法读取：{path}")
            try:
                raw = archive.read(info).decode("utf-8-sig")
            except zipfile.BadZipFile as exc:
                # A truncated or corrupted entry is bad input, not a service fault.
                raise TeamKnowledgeError(f"笔记内容已损坏，无法解压：{path}") from exc
            except UnicodeDecodeError as exc:
                raise TeamKnowledgeError(f"笔记编码无效：{path}") from exc
            frontmatter, body = _frontmatter(raw)
            if body.strip():
                documents.append({"id": f"doc-{uuid4().hex}", "path": path.as_posix(), "frontmatter": frontmatter, "body": body, "content_hash": hashlib.sha256(raw.encode()).hexdigest(), "title": _document_title(path, body), "tags": _tags(frontmatter, raw), "wiki_links": _wiki_links(raw)})
    if not documents:
        raise TeamKnowledgeError("ZIP 中没有可索引的 Markdown 笔记")
    return documents


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    values: dict[str, str] = {}
    active_key: str | None = None
    for line in text[4:end].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            active_key = key.strip()[:100]
            values[active_key] = value.strip()[:1000]
        elif active_key and re.match(r"^\s*-\s+", line):
            item = re.sub(r"^\s*-\s+", "", line).strip()[:300]
            values[active_key] = ",".join(filter(None, [values[active_key], item]))[:1000]
        elif line.strip():
            active_key = None
    return values, text[end + 4 :].lstrip("\n")


def _document_title(path: PurePosixPath, body: str) -> str:
    match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    return (match.group(1).strip() if match else path.stem)[:300]


def _wiki_links(text: str) -> list[str]:
    links: set[str] = set()
    for value in re.findall(r"\[\[([^\]]+)\]\]", text):
        target, _, alias = value.partition("|")
        for item in (target.split("#", 1)[0], alias):
            if item.strip():
                links.add(item.strip()[:300])
    return sorted(links)


def _tags(frontmatter: dict[str, str], text: str) -> list[str]:
    values: set[str] = set()
    for key, value in frontmatter.items():
        if key.casefold() in {"tag", "tags", "标签"}:
            values.update(re.findall(r"[\w\-/#\u4e00-\u9fff]+", value))
    values.update(re.findall(r"(?<!\w)#([\w\-\u4e00-\u9fff/]+)", text))
    return sorted(value[:300] for value in values if value)


def _build_chunks(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for document in documents:
        heading: str | None = None
        buffer = ""
        for line in document["body"].splitlines():
            match = re.match(r"^#{1,6}\s+(.+)$", line)
            if match:
                chunks.extend(_split_buffer(document, heading, buffer))
                buffer = ""
                heading = match.group(1).strip()[:300]
            else:
                buffer += line + "\n"
        chunks.extend(_split_buffer(document, heading, buffer))
    return chunks


def _split_buffer(document: dict[str, Any], heading: str | None, buffer: str) -> list[dict[str, Any]]:
    text = re.sub(r"\n{3,}", "\n\n", buffer).strip()
    chunks: list[dict[str, Any]] = []
    for index in range(0, len(text), MAX_CHUNK_CHARS):
        content = text[index : index + MAX_CHUNK_CHARS].strip()
        if not content:
            continue
        fields = [document["path"], document["title"], heading or "", *document["tags"], *document["wiki_links"], *document["frontmatter"].keys(), *document["frontmatter"].values(), content]
        chunks.append({"id": f"chunk-{uuid4().hex}", "document_id": document["id"], "path": document["path"], "heading": heading, "content": content, "search_text": "\n".join(str(item) for item in fields if item), "wiki_terms": _wiki_terms(fields)})
    return chunks


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _query_terms(question: str) -> list[str]:
    normalized = _normalize(question)
    terms = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", normalized))
    for phrase in re.findall(r"[\u4e00-\u9fff]{3,}", normalized):
        terms.update(phrase[index : index + 3] for index in range(len(phrase) - 2))
    return sorted(terms)


def _wiki_terms(fields: list[Any]) -> list[str]:
    terms: set[str] = set()
    for field in fields:
        terms.update(_query_terms(str(field)))
    return sorted(terms)


def _rank_chunks(chunks: list[dict[str, Any]], question: str, limit: int) -> list[KnowledgeSnippet]:
    normalized = _normalize(question)
    terms = _query_terms(question)

    def score(item: dict[str, Any]) -> float:
        path = _normalize(item["path"])
        heading = _normalize(item.get("heading") or "")
        content = _normalize(item["content"])
        search_text = _normalize(item.get("search_text") or content)
        wiki_terms = {_normalize(value) for value in item.get("wiki_terms", [])}
        value = 0.0
        if normalized and normalized in path:
            value += 100
        if normalized and normalized in heading:
            value += 90
        if normalized and normalized in content:
            value += 25
        for term in terms:
            if term in wiki_terms:
                value += 30
            if term in path:
                value += 25
            if term in heading:
                value += 22
            if term in content:
                value += 5
            elif term in search_text:
                value += 8
        return value

    ranked = sorted(((score(item), item) for item in chunks), key=lambda item: item[0], reverse=True)
    return [KnowledgeSnippet(path=item["path"], heading=item.get("heading"), content=item["content"], score=value) for value, item in ranked[:limit] if value > 0]

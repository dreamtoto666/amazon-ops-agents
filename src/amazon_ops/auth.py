"""Invitation-only authentication for the Amazon Ops API.

Tokens are opaque random values; PostgreSQL stores only their SHA-256 digest.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import smtplib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from getpass import getpass
from threading import Lock
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request, status
from psycopg import Error as PsycopgError
from psycopg_pool import ConnectionPool, PoolTimeout

PASSWORD_HASHER = PasswordHasher()
SESSION_HOURS = 12
REMEMBER_SESSION_DAYS = 30
TOKEN_HOURS = 24


@dataclass(frozen=True)
class AuthUser:
    id: str
    email: str | None
    role: str
    active: bool
    username: str | None = None


class AuthStore:
    def __init__(self, database_url: str, *, max_pool_size: int = 10) -> None:
        self._pool = ConnectionPool(conninfo=database_url, min_size=0, max_size=max_pool_size, open=False)
        self._schema_lock = Lock()
        self._schema_ready = False

    def close(self) -> None:
        self._pool.close()

    def create_user(
        self,
        email: str | None = None,
        password: str = "",
        role: str = "admin",
        *,
        username: str | None = None,
    ) -> AuthUser:
        self._ensure_schema()
        email = normalize_email(email) if email else None
        username = normalize_username(username) if username else None
        if not username:
            raise ValueError("请设置用户名")
        validate_password(password)
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    row = connection.execute(
                        """INSERT INTO auth_users (id, email, username, password_hash, role)
                           VALUES (%s, %s, %s, %s, %s)
                           RETURNING id, email, role, active, username""",
                        (str(uuid4()), email, username, PASSWORD_HASHER.hash(password), role),
                    ).fetchone()
        except PsycopgError as exc:
            raise ValueError("该工作邮箱或用户名已被使用") from exc
        return AuthUser(*row)

    def login(self, identifier: str, password: str, remember: bool, ip: str) -> tuple[AuthUser, str, datetime]:
        self._ensure_schema()
        identifier = normalize_username(identifier)
        now = utcnow()
        with self._pool.connection() as connection:
            with connection.transaction():
                self._assert_not_limited(connection, identifier, ip, now)
                row = connection.execute(
                    """SELECT id, email, password_hash, role, active, username
                       FROM auth_users WHERE username = %s""",
                    (identifier,),
                ).fetchone()
                valid = bool(row and row[4] and verify_password(row[2], password))
                if not valid:
                    connection.execute("INSERT INTO auth_login_attempts (email, ip_address, attempted_at) VALUES (%s, %s, %s)", (identifier, ip, now))
                    raise ValueError("用户名或密码不正确")
                connection.execute("DELETE FROM auth_login_attempts WHERE email = %s", (identifier,))
                expires_at = now + timedelta(days=REMEMBER_SESSION_DAYS if remember else 0, hours=0 if remember else SESSION_HOURS)
                token = secrets.token_urlsafe(48)
                connection.execute(
                    "INSERT INTO auth_sessions (id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)",
                    (str(uuid4()), row[0], digest(token), expires_at),
                )
        return AuthUser(row[0], row[1], row[3], bool(row[4]), row[5]), token, expires_at

    def user_for_token(self, token: str) -> AuthUser | None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            row = connection.execute(
                """SELECT u.id, u.email, u.role, u.active, u.username FROM auth_sessions s
                   JOIN auth_users u ON u.id = s.user_id
                   WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s""",
                (digest(token), utcnow()),
            ).fetchone()
        return AuthUser(*row) if row and row[3] else None

    def revoke_session(self, token: str) -> None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("UPDATE auth_sessions SET revoked_at = %s WHERE token_hash = %s", (utcnow(), digest(token)))

    def list_users(self) -> list[AuthUser]:
        self._ensure_schema()
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT id, email, role, active, username FROM auth_users ORDER BY email"
            ).fetchall()
        return [AuthUser(*row) for row in rows]

    def invite(self, email: str, role: str) -> str:
        self._ensure_schema()
        email = normalize_email(email)
        if role not in {"admin", "operator"}:
            raise ValueError("无效的角色")
        token = secrets.token_urlsafe(48)
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute(
                    """INSERT INTO auth_invites (id, email, role, token_hash, expires_at)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (str(uuid4()), email, role, digest(token), utcnow() + timedelta(hours=TOKEN_HOURS)),
                )
        return token

    def accept_invite(self, token: str, password: str) -> AuthUser:
        self._ensure_schema(); validate_password(password)
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT id, email, role FROM auth_invites WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s",
                    (digest(token), utcnow()),
                ).fetchone()
                if not row:
                    raise ValueError("邀请链接无效或已过期")
                user = connection.execute(
                    """INSERT INTO auth_users (id, email, password_hash, role) VALUES (%s, %s, %s, %s)
                       RETURNING id, email, role, active, username""",
                    (str(uuid4()), row[1], PASSWORD_HASHER.hash(password), row[2]),
                ).fetchone()
                connection.execute("UPDATE auth_invites SET used_at = %s WHERE id = %s", (utcnow(), row[0]))
        return AuthUser(*user)

    def reset_token(self, email: str) -> str | None:
        self._ensure_schema(); email = normalize_email(email)
        token = secrets.token_urlsafe(48)
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute("SELECT id FROM auth_users WHERE email = %s AND active", (email,)).fetchone()
                if not row:
                    return None
                connection.execute("INSERT INTO auth_password_resets (id, user_id, token_hash, expires_at) VALUES (%s, %s, %s, %s)", (str(uuid4()), row[0], digest(token), utcnow() + timedelta(hours=TOKEN_HOURS)))
        return token

    def reset_password(self, token: str, password: str) -> None:
        self._ensure_schema(); validate_password(password)
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute("SELECT id, user_id FROM auth_password_resets WHERE token_hash = %s AND used_at IS NULL AND expires_at > %s", (digest(token), utcnow())).fetchone()
                if not row:
                    raise ValueError("重置链接无效或已过期")
                connection.execute("UPDATE auth_users SET password_hash = %s WHERE id = %s", (PASSWORD_HASHER.hash(password), row[1]))
                connection.execute("UPDATE auth_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (utcnow(), row[1]))
                connection.execute("UPDATE auth_password_resets SET used_at = %s WHERE id = %s", (utcnow(), row[0]))

    def deactivate(self, user_id: str) -> None:
        self._ensure_schema()
        with self._pool.connection() as connection:
            with connection.transaction():
                connection.execute("UPDATE auth_users SET active = FALSE WHERE id = %s", (user_id,))
                connection.execute("UPDATE auth_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL", (utcnow(), user_id))

    def _assert_not_limited(self, connection, email: str, ip: str, now: datetime) -> None:
        since = now - timedelta(minutes=15)
        counts = connection.execute("SELECT count(*) FILTER (WHERE email = %s), count(*) FILTER (WHERE ip_address = %s) FROM auth_login_attempts WHERE attempted_at > %s", (email, ip, since)).fetchone()
        if counts[0] >= 5 or counts[1] >= 20:
            raise ValueError("登录尝试过多，请 15 分钟后重试")

    def _ensure_schema(self) -> None:
        if self._schema_ready: return
        with self._schema_lock:
            if self._schema_ready: return
            self._pool.open(wait=True, timeout=10)
            with self._pool.connection() as connection:
                with connection.transaction():
                    connection.execute("CREATE TABLE IF NOT EXISTS auth_users (id UUID PRIMARY KEY, email TEXT UNIQUE, username TEXT, password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','operator')), active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    connection.execute("ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS username TEXT")
                    connection.execute("ALTER TABLE auth_users ALTER COLUMN email DROP NOT NULL")
                    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS auth_users_username_unique_idx ON auth_users (username) WHERE username IS NOT NULL")
                    connection.execute("""UPDATE auth_users
                        SET username = lower(split_part(email, '@', 1))
                        WHERE username IS NULL
                          AND lower(split_part(email, '@', 1)) ~ '^[a-z0-9._-]{3,32}$'""")
                    connection.execute("CREATE TABLE IF NOT EXISTS auth_sessions (id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES auth_users(id), token_hash TEXT UNIQUE NOT NULL, expires_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    connection.execute("CREATE TABLE IF NOT EXISTS auth_invites (id UUID PRIMARY KEY, email TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','operator')), token_hash TEXT UNIQUE NOT NULL, expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    connection.execute("CREATE TABLE IF NOT EXISTS auth_password_resets (id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES auth_users(id), token_hash TEXT UNIQUE NOT NULL, expires_at TIMESTAMPTZ NOT NULL, used_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    connection.execute("CREATE TABLE IF NOT EXISTS auth_login_attempts (id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL, ip_address TEXT NOT NULL, attempted_at TIMESTAMPTZ NOT NULL)")
                    connection.execute("CREATE INDEX IF NOT EXISTS auth_login_attempts_time_idx ON auth_login_attempts (attempted_at)")
            self._schema_ready = True


def utcnow() -> datetime: return datetime.now(timezone.utc)
def digest(value: str) -> str: return hashlib.sha256(value.encode()).hexdigest()
def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value or len(value) > 254: raise ValueError("请输入有效的工作邮箱")
    return value
def normalize_username(username: str) -> str:
    value = username.strip().lower()
    if not 3 <= len(value) <= 32 or not all(char.isascii() and (char.isalnum() or char in "._-") for char in value):
        raise ValueError("用户名须为 3–32 位字母、数字、点、下划线或连字符")
    return value
def validate_password(password: str) -> None:
    if len(password) < 6: raise ValueError("密码至少需要 6 个字符")
def verify_password(password_hash: str, password: str) -> bool:
    try: return PASSWORD_HASHER.verify(password_hash, password)
    except (InvalidHashError, VerifyMismatchError): return False

def bearer_token(request: Request) -> str | None:
    value = request.headers.get("authorization", "")
    return value[7:] if value.startswith("Bearer ") else None

def require_user(request: Request) -> AuthUser:
    user = getattr(request.state, "auth_user", None)
    if not user: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user
def require_admin(request: Request) -> AuthUser:
    user = require_user(request)
    if user.role != "admin": raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行此操作")
    return user

def send_email(recipient: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip()
    if not host or not sender: raise RuntimeError("SMTP 尚未配置，无法发送邮件")
    message = EmailMessage(); message["From"] = sender; message["To"] = recipient; message["Subject"] = subject; message.set_content(body)
    with smtplib.SMTP(host, int(os.getenv("SMTP_PORT", "587")), timeout=10) as client:
        if os.getenv("SMTP_STARTTLS", "true").lower() == "true": client.starttls()
        username, password = os.getenv("SMTP_USERNAME", ""), os.getenv("SMTP_PASSWORD", "")
        if username: client.login(username, password)
        client.send_message(message)

def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=["create-admin"]); args = parser.parse_args()
    if args.command == "create-admin":
        username = input("管理员用户名: "); password = getpass("管理员密码（至少 6 位）: "); confirm = getpass("确认密码: ")
        if password != confirm: raise SystemExit("两次密码不一致")
        user = AuthStore(os.getenv("DATABASE_URL", "postgresql://amazon_ops:amazon_ops@127.0.0.1:5432/amazon_ops")).create_user(password=password, role="admin", username=username)
        print(f"已创建管理员：{user.username}")
if __name__ == "__main__": main()

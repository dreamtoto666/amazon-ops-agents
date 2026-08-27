# 计划：登录功能（Login）

## Context

项目目前前后端都没有任何认证机制：`/dashboard/*` 任何访客都可以直接访问，API 也是开放的。
需要增加登录功能，让用户凭账号密码进入系统。

## 现状（探索结论）

- 后端 `src/amazon_ops/api.py`：FastAPI 工厂 `create_app()`，无认证、无 auth 依赖（无 python-jose / passlib / itsdangerous）
- PostgreSQL 已有成熟使用模式：`idempotency.py` / `advertising/history.py` 的 psycopg 连接池 + `_ensure_schema()` 建表
- 前端 Next.js 16 App Router，页面都在 `/dashboard/*`；`frontend/src/components/layout/user-nav.tsx` 是 `return null` 的预留用户菜单位
- `/dashboard/users` 用户管理页目前是 faker 假数据（`mock-api-users.ts`），与真实账号无关
- 前端通过 `frontend/src/lib/agent-backend.ts`（`AGENT_API_BASE_URL`）代理后端 API

## Approach（待定，等用户确认）

## Files to modify（待定）

## Reuse

- psycopg 连接池 + `_ensure_schema()` 建表模式：`src/amazon_ops/idempotency.py`
- 前端代理模式：`frontend/src/lib/agent-backend.ts`、`frontend/src/app/api/*/route.ts`
- 用户菜单预留位：`frontend/src/components/layout/user-nav.tsx`

## Steps（待定）

## Verification（待定）

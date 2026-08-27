# 任务创建幂等键

总控任务和广告巡检任务的创建接口都要求请求头：

```http
Idempotency-Key: <8-128 位唯一键>
```

适用接口：

- `POST /api/runs`
- `POST /api/ad-diagnostics/runs`

处理规则：

1. 第一次提交创建任务，响应头 `Idempotency-Replayed: false`；
2. 24 小时内使用相同键和相同请求重试，返回第一次的任务 ID，响应头为 `Idempotency-Replayed: true`；
3. 相同键对应不同请求内容时返回 `409 Conflict`；
4. 对话任务即使由服务端自动生成 `conversation_id`，重放时也返回第一次生成的同一个 ID；
5. 总控任务和广告巡检使用独立命名空间，相同字符串不会跨业务冲突。

当前实现使用 PostgreSQL 持久化，`(namespace, idempotency_key)` 是联合主键。接口会先在事务中占用该键，再创建任务并保存首次响应；不同 API 进程共享同一张表。过期数据会在后续请求到达时自动清理。

默认保留 24 小时，数据库连接池默认 10 个连接，可通过以下环境变量调整：

```dotenv
DATABASE_URL=postgresql://amazon_ops:amazon_ops@127.0.0.1:5432/amazon_ops
IDEMPOTENCY_TTL_SECONDS=86400
IDEMPOTENCY_DB_POOL_SIZE=10
```

运营代办的 `dedupe_key` 属于业务层去重，与 HTTP 幂等键相互独立：前者防止重复代办，后者防止重复创建运行任务。

首次请求正常完成后，即使 API 重启，重试仍会得到原来的任务 ID。数据库不可用时接口返回 `503`，调用方应保留原幂等键稍后重试。

## Docker 启动

在项目根目录执行：

```bash
docker compose up --build -d
```

这会启动 PostgreSQL、Agent API 和前端：

- 前端：`http://localhost:3001`
- Agent API：`http://localhost:8000`
- PostgreSQL：`localhost:5432`

数据保存在 Docker 卷 `amazon_ops_postgres_data`，普通的容器重建不会删除幂等记录。只有明确执行 `docker compose down -v` 才会删除数据库卷。

# 03. API 契约

## 对话

接口：POST /api/chatbi/sessions/{session_id}/messages

请求字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| message | string | 用户问题，非空 |

响应字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| message_id | string | 消息唯一标识 |
| session_id | string | 会话标识 |
| answer | string | 最终回答 |
| charts | array | 图表 ID、标题和 URL |
| meta.intent | string | 路由结果 |
| meta.tools_used | array | 实际工具列表 |

## 会话

- POST /api/chatbi/sessions：创建。
- GET /api/chatbi/sessions/{session_id}/messages：查询历史。
- DELETE /api/chatbi/sessions/{session_id}：按项目策略软删除或删除。

所有接口复用目标项目既有认证，并按用户和租户校验。

# Ad Diagnostics HTTP and BFF Contract

所有浏览器请求通过 `frontend/src/app/api/` 的同源 BFF；BFF 将认证上下文转发给 Python API。浏览器不得访问领星 Open API、领星 MCP 或内部选择数据 MCP。

## GET `/api/ad-diagnostics/selection-directory`

返回当前已认证用户可见的选择目录。响应中的负责人显示名仅限工作台渲染；不得缓存到诊断结果、SSE、运行历史或前端分析事件。

```json
{
  "version": "opaque-snapshot-version",
  "stores": [
    {
      "shop_ref": "shop_opaque_ref",
      "label": "美国店铺 A",
      "responsibles": [
        {
          "responsible_ref": "responsible_opaque_ref",
          "label": "运营负责人",
          "products": [
            {
              "product_ref": "product_opaque_ref",
              "parent_asin": "B0EXAMPLE"
            }
          ]
        }
      ]
    }
  ]
}
```

`401/403` 表示未认证或无授权；`503` 必须明确表示目录暂不可取得，不能返回空目录伪装成无数据。

## POST `/api/ad-diagnostics/runs`

保持 `Idempotency-Key` 必填。请求以选择目录派生的安全范围引用提交，禁止提交负责人姓名、联系方式、员工 ID 或原始上游字段。

```json
{
  "selection_version": "opaque-snapshot-version",
  "shop_ref": "shop_opaque_ref",
  "product_refs": ["product_opaque_ref"],
  "current_period": { "start": "2026-08-01", "end": "2026-08-31" },
  "baseline_period": { "start": "2026-07-01", "end": "2026-07-31" },
  "goal": { "growth_priority": "balanced" },
  "trigger": "manual"
}
```

服务端必须验证目录版本、用户权限、店铺与产品的关联关系，并将其解析为身份无关的实际诊断范围。无效、过期或跨店铺组合返回 `422`；未授权返回 `403`；同一幂等键配不同有效范围返回 `409`。

成功仍返回：

```json
{ "run_id": "run-id", "trace_id": "trace-id", "status": "running" }
```

## Existing run, history and SSE contracts

`GET /runs/{run_id}`、`GET /history` 与 `GET /runs/{run_id}/events` 保持稳定事件名和阶段名。返回的有效范围可包含店铺/产品安全引用与父 ASIN，但不得包含负责人显示名或其他人员身份字段。

## Compatibility

旧的 `/shops` 可在迁移期间保留给未改造消费者；新工作台必须使用 `selection-directory`。移除旧路径前须完成调用方迁移、授权校验与契约回归测试。

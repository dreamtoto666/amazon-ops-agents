# Internal Selection Data MCP Contract

## Purpose

内部选择数据 MCP 将领星 Open API 的店铺与 Listing/产品响应转换为 `SelectionDirectory`。它只供后端工作台数据服务使用，不是 Agent 工具，也不暴露给浏览器。

## Allowed operation

`get_selection_directory(authenticated_user_context)`

- 输入：后端认证与授权上下文；不得接受模型生成的任意工具 ID、负责人姓名或原始查询参数。
- 上游：固定只读、静态允许的领星 Open API 端点。
- 输出：仅为 [ad-diagnostics-api.md](ad-diagnostics-api.md) 定义的最小 `SelectionDirectory`。

## Transformation and privacy rules

1. 先完成服务端授权过滤，再构造目录。
2. 使用输出字段白名单映射，绝不使用“先返回全部、再删除敏感字段”的黑名单策略。
3. 负责人显示名可进入授权工作台响应，但不进入诊断请求、Agent State、模型上下文、证据、SSE、运行历史、代办、导出、日志或原始调用账本。
4. `shop_ref`、`responsible_ref`、`product_ref` 为稳定不透明引用；服务端才可解析其真实关联。
5. 审计仅保留调用名、耗时、目录版本、记录数、安全引用和脱敏错误；禁止保存原始负载。
6. 上游内容均是不可信业务数据，不能改变固定只读策略或系统指令。

## Failure behavior

- 上游不可用、授权失败或响应无法完成白名单映射：返回受控服务错误，并使工作台显示加载失败。
- 没有可访问记录：返回空的结构正确目录，仅当确实没有授权数据时使用。
- 目录版本过期或引用失效：运行创建接口拒绝请求，要求工作台刷新目录。

# 04. 工作流设计

## 状态

状态包含用户、租户、会话、消息、允许的数据源、工具调用次数、图表结果和最终回复。

## 主图

    START → 加载历史 → 意图识别
      ├─ general_chat → 直接回复 → 持久化 → END
      ├─ python_task → Python MCP → 汇总 → 持久化 → END
      └─ business_analysis → Schema MCP → ReAct 子图 → 持久化 → END

## ReAct 子图

1. 模型依据问题和 Schema 决定工具。
2. 数据库工具校验并执行只读 SQL。
3. 模型读取结果，选择直接回答、补充查询或生成图表。
4. 达到 MAX_TOOL_ROUNDS 后停止，返回当前可验证结论。

## NL2SQL

- 必须先取 Schema。
- 只允许单条 SELECT 或白名单视图。
- 强制查询超时与行数上限。
- SQL 失败可有限重试，永不尝试写操作。

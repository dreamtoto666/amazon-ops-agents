# 02. 架构设计

## 组件图

    前端
      ↓ HTTPS
    ChatBI API（认证、会话、响应）
      ↓
    LangGraph Orchestrator
      ├─ Router MCP：意图分类
      ├─ Database MCP：Schema 与只读 SQL
      └─ ReAct 子图
          ├─ Database MCP
          └─ Python MCP：计算与图表
    PostgreSQL / 对象存储 / 模型服务

## 职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| 前端 | 输入与展示 | 直连数据库、执行代码 |
| ChatBI API | 鉴权、请求校验、调用工作流 | 执行 SQL |
| LangGraph | 状态、路由、工具编排 | 直接查库或执行 Python |
| Router MCP | 意图分类 | 数据查询 |
| Database MCP | Schema、只读 SQL | 数据写入 |
| Python MCP | 沙箱计算、图表 | 网络和数据库访问 |

## 约束

- MCP 地址从环境变量读取。
- 图表返回对象存储 URL 或受控下载 URL。
- 服务按最小权限部署。

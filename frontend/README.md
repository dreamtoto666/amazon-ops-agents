# Amazon Ops Console

亚马逊多 Agent 运营驾驶舱前端，基于 Next.js 16、React 19、TypeScript、Tailwind CSS 和 shadcn/ui。
本地默认地址为 [http://localhost:3001](http://localhost:3001)，避免与已有的 3000 端口服务冲突。

## 启动

```bash
npm install
npm run dev
```

仅启动前端时，真实 Agent API 默认应运行在 `http://127.0.0.1:8000`。建议在项目根目录使用
`./scripts/start-dev.sh` 同时启动前后端。

主要页面：

- `/dashboard/chat`：真实 AI 运营助手对话和阶段进度。

Next.js 使用 `/api/agent/*` 同源路由代理 Python API。如果后端地址不是默认值，通过服务端环境变量
`AGENT_API_BASE_URL` 覆盖。

## 检查

```bash
npm run typecheck
npm run lint
npm run build
npm audit --omit=dev
```

本项目由 MIT 许可的 Kiranism Next Shadcn Dashboard Starter 改造，原始许可见 `LICENSE`。

# ShopLens

基于 LangGraph **单 Agent** 的电商售前导购 Agent：以 MCP 接入多平台比价数据源，打通「澄清 → 比价 → 推荐 → 下单」交易闭环。

> 工程重心：可靠性（熔断降级 / 错误预算）+ 可量化评测（四把尺子）。

## 技术栈

Python 3.12 · LangGraph（单 Agent StateGraph）· FastAPI · SQLAlchemy 2.0(psycopg) · Chroma · PostgreSQL 16 · MCP(FastMCP) · Qwen(DashScope) · Vue3 + Vite

## 目录结构

```
backend/   LangGraph Agent + FastAPI 服务 + MCP 比价源
frontend/  Vue3 前端（黑金精奢 UI）
docs/      施工图纸（SPEC）与设计决策（DESIGN_NOTES）
```

## 快速开始

```bash
docker compose up -d                      # 起 PostgreSQL
cd backend && uv sync                     # 装依赖
uv run uvicorn app.main:app --reload      # 起服务
uv run pytest                             # 测试
```

> 完整里程碑与验收标准见 `docs/SPEC.md`，设计决策见 `docs/DESIGN_NOTES.md`。

## Langfuse 观测（可选）

项目默认保持离线运行。需要接入 Langfuse Cloud 或自托管实例时，在项目根目录 `.env` 中填写：

```env
LANGFUSE_ENABLED=1
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_TRACING_ENVIRONMENT=development
```

服务启动后，聊天与 resume 请求会按 `thread_id` 聚合为会话 trace，并记录 LangGraph/LangChain 调用、MCP 工具 span、错误和元数据。未启用或缺少密钥时不会向外部发送观测数据。

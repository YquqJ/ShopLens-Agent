"""FastAPI 入口：装配 checkpointer + MCP 工具（持久会话 + 熔断保护），挂载 SSE 路由。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import build_graph
from app.api.chat import router as chat_router
from app.core.config import settings
from app.infra.observability import setup_tracer
from app.infra.protect import SourceRegistry
from app.mcp import adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        await saver.setup()
        app.state.graph = build_graph(checkpointer=saver)
        # 持久会话装载 MCP 工具（subprocess 常驻，复用会话 RPC），再套熔断/重试保护
        toolbox = await adapter.build_persistent_toolbox()
        async with toolbox:
            registry = SourceRegistry()
            registry.tracer = setup_tracer()  # OpenTelemetry trace 插桩（可选）
            app.state.mcp_tools = registry.protect(toolbox.tools)
            app.state.source_registry = registry
            yield


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router, prefix="/api")
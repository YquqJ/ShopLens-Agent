"""FastAPI 入口：装配 checkpointer + MCP 工具，挂载 SSE 路由。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.agents.graph import build_graph
from app.api.chat import router as chat_router
from app.core.config import settings
from app.mcp import adapter


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncPostgresSaver.from_conn_string(settings.postgres_dsn) as saver:
        await saver.setup()
        app.state.graph = build_graph(checkpointer=saver)
        client = adapter.build_client()
        app.state.mcp_tools = {t.name: t for t in await client.get_tools()}
        yield


app = FastAPI(lifespan=lifespan)
app.include_router(chat_router, prefix="/api")
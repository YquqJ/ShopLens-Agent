"""会话历史 REST：读回 checkpointer 持久化的会话列表与最新状态（第一档）。

graph 由 main.py lifespan 注入 app.state，随进程共享，读接口仅取快照不落库。
"""
from fastapi import APIRouter, Request

from app.services import history

router = APIRouter()


@router.get("/conversations")
async def conversations_list(request: Request) -> list[dict]:
    return await history.list_conversations(request.app.state.graph)


@router.get("/conversations/{thread_id}")
async def conversations_detail(thread_id: str, request: Request) -> dict:
    return await history.get_conversation(request.app.state.graph, thread_id)
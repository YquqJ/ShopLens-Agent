"""会话历史 REST：读回 checkpointer 持久化的会话列表与最新状态（第一档）。

graph 由 main.py lifespan 注入 app.state，随进程共享，读接口仅取快照不落库。
"""
from fastapi import APIRouter, HTTPException, Request

from app.services import history

router = APIRouter()


@router.get("/conversations")
async def conversations_list(request: Request) -> list[dict]:
    return await history.list_conversations(request.app.state.graph)


@router.get("/conversations/{thread_id}")
async def conversations_detail(thread_id: str, request: Request) -> dict:
    return await history.get_conversation(request.app.state.graph, thread_id)


@router.delete("/conversations/{thread_id}")
async def conversations_delete(thread_id: str, request: Request) -> dict:
    if not await history.delete_conversation(request.app.state.graph, thread_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"deleted": True, "thread_id": thread_id}
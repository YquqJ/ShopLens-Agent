"""SSE 流式聊天 + resume 端点。

设计决策：
- graph.astream(stream_mode="updates") 逐节点分帧，排除 messages（含不可序列化的 Message 对象）；
- 命中 interrupt 时发 interrupt 事件（草稿订单），客户端再调 /resume 恢复下单；
- graph / mcp_tools 由 main.py lifespan 注入到 app.state，随进程共享长会话。
"""
import json
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langgraph.types import Command
from pydantic import BaseModel

from app.agents.state import initial_state
from app.services.order import ensure_conversation, require_conversation_owner

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    thread_id: str
    profile_id: str


class ResumeRequest(BaseModel):
    thread_id: str
    confirm: bool
    profile_id: str
    sku_id: str | None = None      # 方案 B：用户在候选间选择（可选）
    platform: str | None = None    # 方案 B：用户在平台间选择（可选）


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _make_cfg(request: Request, thread_id: str, profile_id: str | None = None) -> dict:
    configurable = {
        "thread_id": thread_id,
        "mcp_tools": request.app.state.mcp_tools,
    }
    if profile_id:
        configurable["profile_id"] = profile_id
    return {
        "configurable": configurable
    }


def _safe_node_update(node: str, update: dict) -> dict:
    """Keep durable profile values in graph state, but never stream them in trace frames."""
    safe = {key: value for key, value in update.items() if key != "messages"}
    if node in {"load_memory", "capture_memory"}:
        safe.pop("profile_preferences", None)
    return safe


async def _stream(graph, cfg, payload, is_resume: bool):
    """共用流式逻辑：节点分帧 + interrupt/done 收尾。"""
    async for event in graph.astream(payload, cfg, stream_mode="updates"):
        for node, update in event.items():
            if node == "__interrupt__" or not isinstance(update, dict):
                # 流末尾的 __interrupt__ 事件（值非 dict），交 aget_state 统一处理
                continue
            safe = _safe_node_update(node, update)
            yield _sse("node", {"node": node, **safe})
    snap = await graph.aget_state(cfg)
    if snap.interrupts:
        yield _sse("interrupt", snap.interrupts[0].value)
    else:
        yield _sse("done", snap.values.get("order"))


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    graph = request.app.state.graph
    # 每轮都登记会话行（首条消息即建），会话列表按 conversations 表取，非仅下单后
    try:
        await ensure_conversation(req.thread_id, req.profile_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    cfg = _make_cfg(request, req.thread_id, req.profile_id)

    async def gen() -> AsyncGenerator[str, None]:
        try:
            async for chunk in _stream(graph, cfg, initial_state(req.message), is_resume=False):
                yield chunk
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/resume")
async def resume(req: ResumeRequest, request: Request) -> StreamingResponse:
    graph = request.app.state.graph
    try:
        await require_conversation_owner(req.thread_id, req.profile_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc
    cfg = _make_cfg(request, req.thread_id, req.profile_id)

    async def gen() -> AsyncGenerator[str, None]:
        # resume 载荷：confirm + 可选候选/平台选择；空字段不传给 interrupt
        resume: dict = {"confirm": req.confirm}
        if req.sku_id:
            resume["sku_id"] = req.sku_id
        if req.platform:
            resume["platform"] = req.platform
        try:
            async for chunk in _stream(graph, cfg, Command(resume=resume), is_resume=True):
                yield chunk
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")

"""SSE 流式聊天 + resume 端点。

设计决策：
- graph.astream(stream_mode=["updates", "custom"]) 逐节点分帧，并转发节点主动发送的预览事件；
- 命中 interrupt 时发 interrupt 事件（草稿订单），客户端再调 /resume 恢复下单；
- graph / mcp_tools 由 main.py lifespan 注入到 app.state，随进程共享长会话。
"""
import json
import time
from typing import AsyncGenerator
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage
from langgraph.types import Command
from pydantic import BaseModel

from app.agents.state import initial_state, should_reset_session
from app.infra.langfuse import (
    create_callback_handler,
    langfuse_request_context,
    with_langfuse_config,
)
from app.services.order import ensure_conversation, require_conversation_owner

router = APIRouter()

_CANCEL_PRODUCT_REPLY = "好的，已取消本次商品选择。你可以继续说明购物需求，也可以说“退出导购”结束本轮。"


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


class SelectProductRequest(BaseModel):
    thread_id: str
    profile_id: str
    sku_id: str
    title: str
    url: str


class CancelProductRequest(BaseModel):
    thread_id: str
    profile_id: str


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _make_cfg(
    request: Request,
    thread_id: str,
    profile_id: str | None = None,
) -> dict:
    configurable = {
        "thread_id": thread_id,
        "mcp_tools": request.app.state.mcp_tools,
    }
    if profile_id:
        configurable["profile_id"] = profile_id
    return {
        "configurable": configurable
    }


async def build_chat_payload(graph, cfg: dict, message: str) -> dict:
    """下单成功后的下一条用户消息必须开始新的临时导购上下文。

    未完成下单时保留澄清进度（追问轮次、上一句追问），否则每轮都从 0 开始，
    缺字段时会被同一句追问无限循环。
    """
    snapshot = await graph.aget_state(cfg)
    previous_state = getattr(snapshot, "values", {}) or {}
    reset = should_reset_session(previous_state)
    payload = initial_state(
        message,
        reset_preferences=reset,
        optional_clarify_asked=False if reset else bool(previous_state.get("optional_clarify_asked")),
    )
    if not reset:
        payload["hop_count"] = int(previous_state.get("hop_count") or 0)
        payload["clarify_question"] = str(previous_state.get("clarify_question") or "")
    return payload


def _safe_node_update(node: str, update: dict) -> dict:
    """过滤消息对象与内部控制字段，保持 SSE 负载可序列化。"""
    safe = {key: value for key, value in update.items() if key != "messages"}
    preferences = safe.get("preferences")
    if isinstance(preferences, dict):
        safe["preferences"] = {
            key: value for key, value in preferences.items()
            if not str(key).startswith("__")
        }
    if node in {"load_memory", "capture_memory"}:
        safe.pop("profile_preferences", None)
    return safe


async def _stream(
    graph,
    cfg,
    payload,
    is_resume: bool,
    *,
    langfuse=None,
    trace_name: str = "shoplens.chat",
    trace_metadata: dict | None = None,
):
    """共用流式逻辑：节点分帧 + interrupt/done 收尾。"""
    configurable = cfg.get("configurable", {})
    with langfuse_request_context(
        langfuse,
        trace_name=trace_name,
        session_id=configurable.get("thread_id", "unknown"),
        user_id=configurable.get("profile_id"),
        metadata=trace_metadata,
    ):
        async for mode, event in graph.astream(payload, cfg, stream_mode=["updates", "custom"]):
            if mode == "custom":
                if isinstance(event, dict) and event.get("event") == "recommendation_preview":
                    yield _sse("recommendation_preview", {
                        "recommendation": event.get("recommendation", {}),
                        "recommendation_intro": event.get("recommendation_intro", ""),
                    })
                continue
            if mode != "updates" or not isinstance(event, dict):
                continue
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
    langfuse = getattr(request.app.state, "langfuse", None)
    handler = create_callback_handler(langfuse)
    cfg = with_langfuse_config(
        _make_cfg(request, req.thread_id, req.profile_id),
        handler,
        run_name="shoplens.chat",
        metadata={"operation": "chat", "thread_id": req.thread_id},
        tags=["chat"],
    )

    async def gen() -> AsyncGenerator[str, None]:
        try:
            payload = await build_chat_payload(graph, cfg, req.message)
            async for chunk in _stream(
                graph,
                cfg,
                payload,
                is_resume=False,
                langfuse=langfuse,
                trace_metadata={"operation": "chat"},
            ):
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
    langfuse = getattr(request.app.state, "langfuse", None)
    handler = create_callback_handler(langfuse)
    cfg = with_langfuse_config(
        _make_cfg(request, req.thread_id, req.profile_id),
        handler,
        run_name="shoplens.resume",
        metadata={"operation": "resume", "thread_id": req.thread_id},
        tags=["resume"],
    )

    async def gen() -> AsyncGenerator[str, None]:
        # resume 载荷：confirm + 可选候选/平台选择；空字段不传给 interrupt
        resume: dict = {"confirm": req.confirm}
        if req.sku_id:
            resume["sku_id"] = req.sku_id
        if req.platform:
            resume["platform"] = req.platform
        try:
            async for chunk in _stream(
                graph,
                cfg,
                Command(resume=resume),
                is_resume=True,
                langfuse=langfuse,
                trace_name="shoplens.resume",
                trace_metadata={"operation": "resume"},
            ):
                yield chunk
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/select-product")
async def select_product(req: SelectProductRequest, request: Request) -> dict:
    """Persist a real-source selection as an assistant message in the graph checkpoint."""
    try:
        await require_conversation_owner(req.thread_id, req.profile_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc

    parsed = urlparse(req.url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=400, detail="商品链接无效")

    graph = request.app.state.graph
    cfg = {"configurable": {"thread_id": req.thread_id}}
    snapshot = await graph.aget_state(cfg)
    recommendation = (snapshot.values or {}).get("recommendation") or {}
    item = next(
        (item for item in recommendation.get("items", []) if item.get("sku_id") == req.sku_id),
        None,
    )
    if item is None or item.get("url") != req.url or item.get("title") != req.title:
        raise HTTPException(status_code=400, detail="所选商品不在当前推荐结果中")

    message = AIMessage(
        f"已选择「{req.title}」\n链接：{req.url}",
        additional_kwargs={"ts": time.time()},
    )
    await graph.aupdate_state(cfg, {
        "messages": [message],
        "recommendation": {},
        "candidates": [],
        "session_reset_pending": True,
    })
    return {"selected": True}


@router.post("/cancel-product")
async def cancel_product(req: CancelProductRequest, request: Request) -> dict:
    """Persist a real-source cancellation and close the current recommendation loop."""
    try:
        await require_conversation_owner(req.thread_id, req.profile_id)
    except (PermissionError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="会话不存在") from exc

    graph = request.app.state.graph
    cfg = {"configurable": {"thread_id": req.thread_id}}
    await graph.aupdate_state(cfg, {
        "messages": [AIMessage(_CANCEL_PRODUCT_REPLY, additional_kwargs={"ts": time.time()})],
        "recommendation": {},
        "candidates": [],
    })
    return {"cancelled": True}

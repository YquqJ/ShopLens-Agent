"""会话历史服务：从 LangGraph checkpointer 读回会话（第一档：读最新状态）。

设计决策：
- 会话列表以 conversations 表为准（每轮 /chat 都 ensure_conversation 登记，见 chat.py），
  标题/副标题从 checkpointer 的 messages[0] 与 preferences 反推，不额外建表建列；
- 会话详情读 graph.aget_state 的最新快照：messages 全文 + 最新一轮结构化结果
  （首档局限：checkpointer 只存最新 recommendation/order，多轮历史卡片不逐轮保留）；
- langchain Message 对象不可 JSON 序列化，统一折成 {role, content}；
- HITL 挂起时草稿在 snap.interrupts 载荷里（state 尚未提交 draft_order），需从载荷取回。
"""
from langchain_core.messages import BaseMessage
from sqlalchemy import delete, select

from app.db.models import ConversionEvent, Conversation, Order, User
from app.db.session import async_session_factory
from app.services import profile
from app.services.order import require_conversation_owner


def _text(content) -> str:
    """Message.content 可能为 str 或分块列表，折成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("text")
        )
    return ""


def _serialize_messages(messages) -> list[dict]:
    out = []
    for m in messages or []:
        if not isinstance(m, BaseMessage):
            continue
        out.append({
            "role": "user" if m.type == "human" else "assistant",
            "content": _text(m.content),
            "ts": (m.additional_kwargs or {}).get("ts"),
        })
    return out


def _title_subtitle(values: dict) -> tuple[str, str]:
    title = "新对话"
    for m in values.get("messages", []):
        if isinstance(m, BaseMessage) and m.type == "human":
            title = _text(m.content)[:18] or "新对话"
            break
    prefs = values.get("preferences") or {}
    parts = []
    if prefs.get("category"):
        parts.append(str(prefs["category"]))
    if prefs.get("budget") is not None:
        parts.append(f"预算 ¥{prefs['budget']}")
    return title, " · ".join(parts) or "等待输入购物需求"


async def list_conversations(graph, profile_id: str) -> list[dict]:
    """会话列表：按最近更新排序，标题/副标题从各自最新快照反推。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation)
            .join(User, Conversation.user_id == User.id)
            .where(User.external_id == profile_id)
            .order_by(Conversation.updated_at.desc())
        )
        convs = result.scalars().all()
    out = []
    for c in convs:
        snap = await graph.aget_state({"configurable": {"thread_id": c.thread_id}})
        title, subtitle = _title_subtitle(snap.values or {})
        out.append({
            "thread_id": c.thread_id,
            "title": title,
            "subtitle": subtitle,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        })
    return out


async def get_conversation(graph, thread_id: str, profile_id: str) -> dict | None:
    """会话详情：最新快照的消息全文 + 结构化结果（供前端重建 turns）。"""
    try:
        await require_conversation_owner(thread_id, profile_id)
    except (PermissionError, ValueError):
        return None
    snap = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    values = snap.values or {}
    rec = values.get("recommendation") or {}
    awaiting = bool(snap.interrupts)
    draft = values.get("draft_order")
    if awaiting and snap.interrupts[0].value:
        # HITL 挂起：草稿在 interrupt 载荷里（state 尚未提交 draft_order）
        val = snap.interrupts[0].value
        if isinstance(val, dict) and val.get("type") == "confirm_order":
            draft = val.get("draft_order") or draft
    current_profile = await profile.get_preferences(profile_id)
    return {
        "messages": _serialize_messages(values.get("messages", [])),
        "preferences": values.get("preferences") or {},
        "profile_preferences": current_profile,
        "memory_events": values.get("memory_events") or [],
        "normalized": values.get("normalized") or [],
        "recommendation": rec or None,
        "draft_order": draft,
        "order": values.get("order"),
        "price_raised": bool(values.get("price_raised")),
        "awaiting_hitl": awaiting,
    }


async def delete_conversation(graph, thread_id: str, profile_id: str) -> bool:
    """删除会话：清关联转化事件/订单 → 删会话行 → 删 checkpointer 线程状态。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation)
            .join(User, Conversation.user_id == User.id)
            .where(
                Conversation.thread_id == thread_id,
                User.external_id == profile_id,
            )
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            return False
        cid = conv.id
        # 无 ondelete cascade，先清子表再删父表，否则外键约束拒绝删除
        await session.execute(delete(ConversionEvent).where(ConversionEvent.conversation_id == cid))
        await session.execute(delete(Order).where(Order.conversation_id == cid))
        await session.delete(conv)
        await session.commit()
    # checkpointer 线程一并删除（会话彻底移除）；线程可能无 checkpoint（/chat 登记后流失败），失败不影响
    try:
        await graph.checkpointer.adelete_thread(thread_id)
    except Exception:  # noqa: BLE001
        pass
    return True

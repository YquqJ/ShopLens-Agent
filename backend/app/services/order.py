"""订单服务：会话落库 + 下单落库 + 金额固化。

设计决策：
- 金额用 Decimal(str(x)) 固化落库，规避 float 二进制精度问题；
- thread_id（LangGraph 线程）即会话归属，下单前按 thread_id 取/建 Conversation，
  订单挂到其 conversation_id 上，M8 转化漏斗按此归因。
"""
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Conversation, Order
from app.db.session import async_session_factory


def _dec(v: float | None) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


async def ensure_conversation(thread_id: str) -> int:
    """按 thread_id 取/建会话，返回 conversation.id。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.thread_id == thread_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            conv = Conversation(thread_id=thread_id)
            session.add(conv)
            await session.commit()
            await session.refresh(conv)
        return conv.id


async def place_order(draft: dict, thread_id: str) -> dict:
    """落库生成已下单订单（status=placed），金额固化。"""
    conversation_id = await ensure_conversation(thread_id)
    async with async_session_factory() as session:
        order = Order(
            conversation_id=conversation_id,
            sku_id=draft["sku_id"],
            platform=draft.get("platform"),
            quoted_price=_dec(draft.get("price")),
            final_price=_dec(draft.get("final_price")),
            status="placed",
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)
        return {
            "order_id": order.id,
            "conversation_id": order.conversation_id,
            "sku_id": order.sku_id,
            "platform": order.platform,
            "final_price": float(order.final_price) if order.final_price is not None else None,
            "status": order.status,
        }
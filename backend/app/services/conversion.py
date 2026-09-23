"""转化事件服务：曝光/点击埋点落库 + 漏斗统计。

设计决策：
- 曝光/点击按 thread_id 归因到 Conversation（同 order_service.ensure_conversation），由前端埋点写入；
  成交不再写事件，改由 orders 表的 is_recommended 标记统计（只算购买👍推荐品的下单）；
- funnel_stats 做「曝光/点击/成交」三段计数 + 层层转化率 + 成交明细，ECharts 漏斗与明细表取数用。
"""
from sqlalchemy import func, select

from app.db.models import ConversionEvent, Conversation, Order, Product, User
from app.db.session import async_session_factory
from app.services.order import ensure_conversation

# 合法事件类型（与 db.models.ConversionEvent.event_type 对齐）
EVENT_TYPES = ("impression", "click", "order")


async def record_event(
    thread_id: str,
    profile_key: str,
    event_type: str,
    sku_id: str | None = None,
) -> dict:
    """按 thread_id 归因写入一条转化事件；非法类型抛 ValueError（不静默）。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {event_type!r}，应为 {EVENT_TYPES}")
    conversation_id = await ensure_conversation(thread_id, profile_key)
    async with async_session_factory() as session:
        ev = ConversionEvent(
            conversation_id=conversation_id, event_type=event_type, sku_id=sku_id
        )
        session.add(ev)
        await session.commit()
        await session.refresh(ev)
        return {
            "id": ev.id,
            "conversation_id": ev.conversation_id,
            "event_type": ev.event_type,
            "sku_id": ev.sku_id,
        }


async def funnel_stats(profile_key: str) -> dict:
    """漏斗统计：曝光/点击/成交三段计数 + 点击率/成交率（相对曝光）+ 成交明细。

    曝光/点击来自前端埋点（点击只统计👍推荐品）；成交来自 orders 表中购买首推的下单。
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversionEvent.event_type, func.count())
            .join(Conversation, ConversionEvent.conversation_id == Conversation.id)
            .join(User, Conversation.user_id == User.id)
            .where(User.external_id == profile_key)
            .group_by(ConversionEvent.event_type)
        )
        counts = {et: n for et, n in result.all()}
        # 成交明细：购买推荐品（is_recommended）的下单，联 products 取标题，最近在下
        rows = (
            await session.execute(
                select(Order, Product.title)
                .join(Conversation, Order.conversation_id == Conversation.id)
                .join(User, Conversation.user_id == User.id)
                .join(Product, Product.sku_id == Order.sku_id)
                .where(
                    User.external_id == profile_key,
                    Order.status == "placed",
                    Order.is_recommended.is_(True),
                )
                .order_by(Order.id.desc())
            )
        ).all()
    impression = counts.get("impression", 0)
    click = counts.get("click", 0)
    orders = [
        {
            "title": title or o.sku_id,
            "platform": o.platform,
            "final_price": float(o.final_price) if o.final_price is not None else None,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o, title in rows
    ]
    order = len(orders)
    return {
        "impression": impression,
        "click": click,
        "order": order,
        "click_rate": round(click / impression, 4) if impression else 0.0,
        "order_rate": round(order / impression, 4) if impression else 0.0,
        "orders": orders,
    }

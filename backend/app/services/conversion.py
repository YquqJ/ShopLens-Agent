"""转化事件服务：曝光/点击埋点落库 + 漏斗统计。

设计决策：
- 事件按 thread_id 归因到 Conversation（同 order_service.ensure_conversation），
  曝光/点击由前端埋点写入，下单由 place_order 落库时写入，三类同表统一归因；
- funnel_stats 只做「曝光/点击/下单」三段计数 + 层层转化率，ECharts 漏斗取数用。
"""
from sqlalchemy import func, select

from app.db.models import ConversionEvent
from app.db.session import async_session_factory
from app.services.order import ensure_conversation

# 合法事件类型（与 db.models.ConversionEvent.event_type 对齐）
EVENT_TYPES = ("impression", "click", "order")


async def record_event(thread_id: str, event_type: str, sku_id: str | None = None) -> dict:
    """按 thread_id 归因写入一条转化事件；非法类型抛 ValueError（不静默）。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"未知事件类型 {event_type!r}，应为 {EVENT_TYPES}")
    conversation_id = await ensure_conversation(thread_id)
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


async def funnel_stats() -> dict:
    """漏斗统计：曝光/点击/下单三段计数 + 点击率/下单转化率（相对曝光）。"""
    async with async_session_factory() as session:
        result = await session.execute(
            select(ConversionEvent.event_type, func.count())
            .group_by(ConversionEvent.event_type)
        )
        counts = {et: n for et, n in result.all()}
    impression = counts.get("impression", 0)
    click = counts.get("click", 0)
    order = counts.get("order", 0)
    return {
        "impression": impression,
        "click": click,
        "order": order,
        "click_rate": round(click / impression, 4) if impression else 0.0,
        "order_rate": round(order / impression, 4) if impression else 0.0,
    }
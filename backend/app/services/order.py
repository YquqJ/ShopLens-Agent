"""订单服务：会话落库 + 下单落库 + 金额固化。

设计决策：
- 金额用 Decimal(str(x)) 固化落库，规避 float 二进制精度问题；
- thread_id（LangGraph 线程）即会话归属，下单前按 thread_id 取/建 Conversation，
  订单挂到其 conversation_id 上，M8 转化漏斗按此归因。
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from app.db.models import Conversation, Order, User
from app.db.session import async_session_factory


def _dec(v: float | None) -> Decimal | None:
    return Decimal(str(v)) if v is not None else None


def _profile_key(profile_key: str) -> str:
    value = (profile_key or "").strip()
    if not value:
        raise ValueError("profile_id 不能为空")
    return value


async def _get_or_create_user(session, profile_key: str) -> User:
    user = await session.scalar(select(User).where(User.external_id == profile_key))
    if user is None:
        user = User(external_id=profile_key)
        session.add(user)
        await session.flush()
    return user


async def ensure_conversation(thread_id: str, profile_key: str) -> int:
    """按 thread/profile 取建会话，确保已有会话不能被其他画像接管。"""
    profile_key = _profile_key(profile_key)
    async with async_session_factory() as session:
        user = await _get_or_create_user(session, profile_key)
        result = await session.execute(
            select(Conversation).where(Conversation.thread_id == thread_id)
        )
        conv = result.scalar_one_or_none()
        if conv is None:
            conv = Conversation(thread_id=thread_id, user_id=user.id)
            session.add(conv)
        elif conv.user_id != user.id:
            raise PermissionError("会话不属于当前浏览器画像")
        else:
            # 会话活跃即刷新更新时间，供会话列表按最近互动排序
            conv.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(conv)
        return conv.id


async def require_conversation_owner(thread_id: str, profile_key: str) -> int:
    """返回当前画像拥有的会话 ID；不存在或 owner 不匹配均拒绝。"""
    profile_key = _profile_key(profile_key)
    async with async_session_factory() as session:
        conversation_id = await session.scalar(
            select(Conversation.id)
            .join(User, Conversation.user_id == User.id)
            .where(
                Conversation.thread_id == thread_id,
                User.external_id == profile_key,
            )
        )
    if conversation_id is None:
        raise PermissionError("会话不存在或不属于当前浏览器画像")
    return conversation_id


async def place_order(
    draft: dict,
    thread_id: str,
    profile_key: str,
    is_recommended: bool = False,
) -> dict:
    """落库生成已下单订单（status=placed），金额固化；is_recommended 标记是否购买首推。"""
    conversation_id = await ensure_conversation(thread_id, profile_key)
    async with async_session_factory() as session:
        order = Order(
            conversation_id=conversation_id,
            sku_id=draft["sku_id"],
            platform=draft.get("platform"),
            quoted_price=_dec(draft.get("price")),
            final_price=_dec(draft.get("final_price")),
            status="placed",
            is_recommended=is_recommended,
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

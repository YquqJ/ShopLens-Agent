import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.api import conversion as conversion_api
from app.db.models import ConversionEvent, Conversation, Order, Product, User
from app.db.session import async_session_factory, init_db
from app.services import conversion


@pytest.fixture
def anyio_backend() -> tuple[str, dict]:
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture
async def conversion_profiles() -> tuple[str, str]:
    try:
        await init_db()
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except (OSError, OperationalError, InterfaceError) as exc:
        pytest.skip(f"PostgreSQL unavailable for conversion integration tests: {exc}")

    suffix = str(uuid4())
    profile_a = f"pytest-funnel-a-{suffix}"
    profile_b = f"pytest-funnel-b-{suffix}"
    async with async_session_factory() as session:
        user_a = User(external_id=profile_a)
        user_b = User(external_id=profile_b)
        session.add_all([user_a, user_b])
        await session.flush()
        conversation_a = Conversation(thread_id=f"pytest-funnel-thread-a-{suffix}", user_id=user_a.id)
        conversation_b = Conversation(thread_id=f"pytest-funnel-thread-b-{suffix}", user_id=user_b.id)
        product_a = Product(
            sku_id=f"pytest-funnel-sku-a-{suffix}", title="画像 A 专属外套",
            category="外套", attributes={}, price=Decimal("199.00"), stock=1,
        )
        product_b = Product(
            sku_id=f"pytest-funnel-sku-b-{suffix}", title="画像 B 专属衬衫",
            category="衬衫", attributes={}, price=Decimal("299.00"), stock=1,
        )
        session.add_all([conversation_a, conversation_b, product_a, product_b])
        await session.flush()
        session.add_all([
            ConversionEvent(conversation_id=conversation_a.id, event_type="impression", sku_id=product_a.sku_id),
            ConversionEvent(conversation_id=conversation_a.id, event_type="click", sku_id=product_a.sku_id),
            ConversionEvent(conversation_id=conversation_b.id, event_type="impression", sku_id=product_b.sku_id),
            ConversionEvent(conversation_id=conversation_b.id, event_type="click", sku_id=product_b.sku_id),
            Order(
                conversation_id=conversation_a.id, sku_id=product_a.sku_id, platform="taobao",
                final_price=Decimal("199.00"), status="placed", is_recommended=True,
            ),
            Order(
                conversation_id=conversation_b.id, sku_id=product_b.sku_id, platform="jd",
                final_price=Decimal("299.00"), status="placed", is_recommended=True,
            ),
        ])
        await session.commit()

    try:
        yield profile_a, profile_b
    finally:
        async with async_session_factory() as session:
            conversation_ids = (
                await session.scalars(
                    Conversation.__table__.select()
                    .with_only_columns(Conversation.id)
                    .where(Conversation.thread_id.like(f"pytest-funnel-thread-%-{suffix}"))
                )
            ).all()
            if conversation_ids:
                await session.execute(delete(ConversionEvent).where(ConversionEvent.conversation_id.in_(conversation_ids)))
                await session.execute(delete(Order).where(Order.conversation_id.in_(conversation_ids)))
                await session.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            await session.execute(delete(Product).where(Product.sku_id.like(f"pytest-funnel-sku-%-{suffix}")))
            await session.execute(delete(User).where(User.external_id.in_([profile_a, profile_b])))
            await session.commit()


@pytest.mark.anyio
async def test_funnel_stats_only_returns_events_and_orders_owned_by_requested_profile(
    conversion_profiles: tuple[str, str],
) -> None:
    profile_a, profile_b = conversion_profiles

    stats_a = await conversion.funnel_stats(profile_a)
    stats_b = await conversion.funnel_stats(profile_b)

    assert (stats_a["impression"], stats_a["click"], stats_a["order"]) == (1, 1, 1)
    assert [order["title"] for order in stats_a["orders"]] == ["画像 A 专属外套"]
    assert (stats_b["impression"], stats_b["click"], stats_b["order"]) == (1, 1, 1)
    assert [order["title"] for order in stats_b["orders"]] == ["画像 B 专属衬衫"]


@pytest.mark.anyio
async def test_funnel_endpoint_requires_and_forwards_profile_id(monkeypatch) -> None:
    expected = {"impression": 0, "click": 0, "order": 0, "orders": []}

    async def stats(profile_id: str) -> dict:
        assert profile_id == "browser-profile"
        return expected

    monkeypatch.setattr(conversion_api.conversion, "funnel_stats", stats)

    assert await conversion_api.get_funnel("browser-profile") == expected

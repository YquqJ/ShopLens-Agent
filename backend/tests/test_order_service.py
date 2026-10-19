from types import SimpleNamespace

import pytest

from app.services import order as order_service


class _Session:
    def __init__(self, *, scalar_values=None, execute_value=None):
        self.scalar_values = list(scalar_values or [])
        self.execute_value = execute_value
        self.added = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def scalar(self, _statement):
        return self.scalar_values.pop(0) if self.scalar_values else None

    async def execute(self, _statement):
        return self.execute_value

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        if self.added and getattr(self.added[-1], "id", None) is None:
            self.added[-1].id = 42

    async def commit(self):
        self.committed = True

    async def refresh(self, _value):
        return None


class _Factory:
    def __init__(self, *sessions):
        self.sessions = list(sessions)

    def __call__(self):
        return self.sessions.pop(0)


@pytest.mark.anyio
async def test_place_order_persists_intent_and_product_snapshot(monkeypatch):
    user = SimpleNamespace(id=7, external_id="profile-1")
    conversation = SimpleNamespace(id=8, user_id=7)
    setup_session = _Session(
        scalar_values=[user],
        execute_value=SimpleNamespace(scalar_one_or_none=lambda: conversation),
    )
    order_session = _Session()
    monkeypatch.setattr(
        order_service,
        "async_session_factory",
        _Factory(setup_session, order_session),
    )

    result = await order_service.place_order(
        {
            "sku_id": "SKU1",
            "title": "黑色外套",
            "url": "https://mall.example/item",
            "catalog_price": 399,
            "price": 299,
            "shipping_fee": 0,
            "final_price": 299,
            "platform": "jd",
        },
        "thread-1",
        "profile-1",
        order_intent_id="intent-1",
    )

    assert result["status"] == "placed"
    assert result["order_intent_id"] == "intent-1"
    assert order_session.added[0].title_snapshot == "黑色外套"
    assert order_session.added[0].source_url == "https://mall.example/item"
    assert order_session.added[0].order_intent_id == "intent-1"


@pytest.mark.anyio
async def test_place_order_is_idempotent_for_same_intent(monkeypatch):
    user = SimpleNamespace(id=7, external_id="profile-1")
    existing = SimpleNamespace(
        id=99,
        conversation_id=8,
        sku_id="SKU1",
        platform="jd",
        final_price=299,
        status="placed",
        order_intent_id="intent-1",
    )
    conversation = SimpleNamespace(id=8, user_id=7)
    setup_session = _Session(
        scalar_values=[user],
        execute_value=SimpleNamespace(scalar_one_or_none=lambda: conversation),
    )
    order_session = _Session(scalar_values=[existing])
    monkeypatch.setattr(order_service, "async_session_factory", _Factory(setup_session, order_session))

    result = await order_service.place_order(
        {"sku_id": "SKU1", "title": "黑色外套", "price": 299, "final_price": 299, "platform": "jd"},
        "thread-1",
        "profile-1",
        order_intent_id="intent-1",
    )

    assert result["order_id"] == 99
    assert result["status"] == "placed"
    assert order_session.added == []

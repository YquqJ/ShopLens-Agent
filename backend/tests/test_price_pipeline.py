import pytest

from app.agents.nodes.compare import compare_node
from app.agents.nodes.confirm import _build_draft
from app.agents.nodes.lock_price import lock_price_node
from app.mcp import _sim


class _RecordingTool:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, payload: dict) -> dict:
        self.calls.append(payload)
        return self.result


def test_platform_quote_is_derived_from_catalog_base_price(monkeypatch) -> None:
    monkeypatch.setattr(_sim.random, "uniform", lambda _low, _high: 1.0)

    assert _sim.quote("SKU0001", "jd", base_price=200) == 204


@pytest.mark.anyio
async def test_compare_passes_catalog_price_to_each_platform_quote() -> None:
    price_tools = {
        name: _RecordingTool({"price": 200, "platform": platform})
        for name, platform in {
            "price_jd_get_quote": "jd",
            "price_taobao_get_quote": "taobao",
            "price_pdd_get_quote": "pdd",
        }.items()
    }
    coupon = _RecordingTool({"coupon": None})
    logistics = _RecordingTool({"eta_days": 1, "shipping_fee": 0})
    tools = {**price_tools, "coupon_get_coupons": coupon, "logistics_get_delivery": logistics}

    await compare_node(
        {"candidates": [{"sku_id": "SKU0001", "price": 200}]},
        {"configurable": {"mcp_tools": tools}},
    )

    for tool in price_tools.values():
        assert tool.calls == [{"sku_id": "SKU0001", "base_price": 200}]
    assert coupon.calls == [
        {"sku_id": "SKU0001", "platform": "jd", "base_price": 200},
        {"sku_id": "SKU0001", "platform": "taobao", "base_price": 200},
        {"sku_id": "SKU0001", "platform": "pdd", "base_price": 200},
    ]


@pytest.mark.anyio
async def test_lock_price_uses_the_same_catalog_base_price() -> None:
    price_tool = _RecordingTool({"price": 205})
    draft = {
        "sku_id": "SKU0001",
        "platform": "jd",
        "catalog_price": 200,
        "discount": 0,
        "shipping_fee": 0,
        "final_price": 204,
    }

    await lock_price_node(
        {"draft_order": draft, "lock_attempts": 0},
        {"configurable": {"mcp_tools": {"price_jd_get_quote": price_tool}}},
    )

    assert price_tool.calls == [{"sku_id": "SKU0001", "base_price": 200}]


def test_confirmation_carries_catalog_price_into_the_order_draft() -> None:
    draft = _build_draft({
        "sku_id": "SKU0001",
        "title": "黑色卫衣",
        "catalog_price": 200,
        "best": {
            "platform": "pdd",
            "price": 190,
            "discount": 0,
            "shipping_fee": 0,
            "eta_days": 1,
            "final_price": 190,
        },
    })

    assert draft["catalog_price"] == 200

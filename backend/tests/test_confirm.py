import pytest

from app.agents.nodes import confirm


@pytest.mark.anyio
async def test_confirm_cancel_message_guides_next_action(monkeypatch):
    monkeypatch.setattr(confirm, "interrupt", lambda _payload: {"confirm": False})

    result = await confirm.confirm_node({
        "normalized": [{
            "sku_id": "SKU1",
            "title": "黑色外套",
            "best": {
                "platform": "jd",
                "price": 299,
                "discount": 0,
                "shipping_fee": 0,
                "eta_days": 2,
                "final_price": 299,
            },
        }],
        "lock_attempts": 0,
        "draft_order": None,
    })

    assert result["messages"][0].content == (
        "好的，已取消本次下单。你可以继续说明购物需求，也可以说“退出导购”结束本轮。"
    )

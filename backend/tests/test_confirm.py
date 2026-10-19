import pytest
from langchain_core.messages import AIMessage

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


@pytest.mark.anyio
async def test_confirm_rejects_selection_outside_current_recommendation(monkeypatch):
    monkeypatch.setattr(confirm, "interrupt", lambda _payload: {
        "confirm": True,
        "sku_id": "NOT-IN-RESULT",
    })

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

    assert result["draft_order"] is None
    assert result["order"] == {"status": "invalid_selection"}
    assert isinstance(result["messages"][0], AIMessage)
    assert "推荐结果" in result["messages"][0].content


@pytest.mark.anyio
async def test_confirm_builds_real_source_draft_from_candidates(monkeypatch):
    monkeypatch.setattr(confirm, "interrupt", lambda payload: {
        "confirm": True,
        "sku_id": "ZDM01",
    })

    result = await confirm.confirm_node({
        "candidates": [{
            "sku_id": "ZDM01",
            "title": "真实外套",
            "url": "https://mall.example/item",
            "page_price": 299,
            "mall": "京东",
        }],
        "recommendation": {"items": [{"sku_id": "ZDM01", "title": "真实外套", "final_price": 299, "platform": "京东"}]},
        "normalized": [],
        "lock_attempts": 0,
        "draft_order": None,
    })

    assert result["draft_order"]["sku_id"] == "ZDM01"
    assert result["draft_order"]["url"] == "https://mall.example/item"
    assert result["draft_order"]["final_price"] == 299

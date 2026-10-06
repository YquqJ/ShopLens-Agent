import pytest

from app.agents.graph import _after_recommend
from app.agents.nodes.recommend import recommend_node, select_top


def _item(sku_id: str, final_price: float) -> dict:
    return {
        "sku_id": sku_id,
        "title": sku_id,
        "attributes": {},
        "best": {"platform": "pdd", "final_price": final_price, "price": final_price},
    }


def test_select_top_does_not_fall_back_to_over_budget_products() -> None:
    assert select_top([_item("SKU1", 220), _item("SKU2", 260)], 200) == []


@pytest.mark.anyio
async def test_recommend_returns_a_budget_message_without_calling_the_llm(monkeypatch) -> None:
    from app.agents.nodes import recommend

    monkeypatch.setattr(recommend, "get_structured_llm", lambda _model, _schema: (_ for _ in ()).throw(AssertionError("LLM should not run")))
    result = await recommend_node({
        "normalized": [_item("SKU1", 220)],
        "preferences": {"budget": 200},
        "profile_preferences": {},
        "degraded_sources": [],
    })

    assert result["recommendation"]["items"] == []
    assert "预算" in result["recommendation"]["summary"]


def test_graph_skips_hitl_when_no_budget_eligible_recommendation_exists() -> None:
    assert _after_recommend({"zdm_mode": False, "recommendation": {"items": []}}) == "end"


@pytest.mark.anyio
async def test_recommend_preserves_real_product_media_fields(monkeypatch) -> None:
    from types import SimpleNamespace
    from app.agents.nodes import recommend

    class _FakeStructured:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                summary="真实商品推荐",
                reasons=[SimpleNamespace(sku_id="ZDM01", reason="价格符合预算")],
            )

    monkeypatch.setattr(recommend, "get_structured_llm", lambda *_args: _FakeStructured())
    result = await recommend_node({
        "normalized": [{
            "sku_id": "ZDM01",
            "title": "真实商品",
            "attributes": {"品牌": "Example"},
            "image_url": "https://img.example/item.jpg",
            "url": "https://mall.example/item",
            "best": {"platform": "京东", "final_price": 199, "price": 199, "total_cost": 199},
        }],
        "preferences": {"budget": 300},
        "profile_preferences": {},
        "degraded_sources": [],
    })

    assert result["recommendation"]["items"][0]["image_url"] == "https://img.example/item.jpg"
    assert result["recommendation"]["items"][0]["url"] == "https://mall.example/item"

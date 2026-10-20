import asyncio
from types import SimpleNamespace

import pytest
from langgraph.graph import START, StateGraph

from app.agents.graph import _after_recommend
from app.agents.nodes.recommend import recommend_node, select_top
from app.agents.state import AgentState


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
        "turn_id": "turn-budget",
    })

    assert result["recommendation"]["items"] == []
    assert "预算" in result["recommendation"]["summary"]
    assert result["recommendation_history"][0]["turn_id"] == "turn-budget"


@pytest.mark.anyio
async def test_recommend_distinguishes_no_search_results_from_over_budget_results(monkeypatch) -> None:
    from app.agents.nodes import recommend

    monkeypatch.setattr(recommend, "get_structured_llm", lambda *_args: (_ for _ in ()).throw(AssertionError("LLM should not run")))
    result = await recommend_node({
        "normalized": [],
        "preferences": {"category": "手机", "budget": 8000},
        "profile_preferences": {},
        "degraded_sources": [],
        "turn_id": "turn-empty",
    })

    summary = result["recommendation"]["summary"]
    assert "没有找到" in summary
    assert "均高于预算" not in summary


def test_graph_skips_hitl_when_no_budget_eligible_recommendation_exists() -> None:
    assert _after_recommend({"zdm_mode": False, "recommendation": {"items": []}}) == "end"


@pytest.mark.anyio
async def test_recommend_preserves_real_product_media_fields(monkeypatch) -> None:
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
        "preferences": {"category": "无人机", "color": "白色", "budget": 8000},
        "profile_preferences": {},
        "degraded_sources": [],
        "turn_id": "turn-real",
    })

    assert result["recommendation"]["items"][0]["image_url"] == "https://img.example/item.jpg"
    assert result["recommendation"]["items"][0]["url"] == "https://mall.example/item"
    assert result["recommendation_history"][0]["turn_id"] == "turn-real"
    assert result["recommendation_intro"] == "根据您的“无人机 · 白色 · 预算 ¥8000”需求，为你推荐1款候选"


@pytest.mark.anyio
async def test_recommend_streams_factual_preview_before_model_result(monkeypatch) -> None:
    from app.agents.nodes import recommend

    class _FakeStructured:
        async def ainvoke(self, _messages):
            await asyncio.sleep(0.01)
            return SimpleNamespace(
                summary="完整推荐理由",
                reasons=[SimpleNamespace(sku_id="SKU1", reason="符合预算")],
            )

    monkeypatch.setattr(recommend, "get_structured_llm", lambda *_args: _FakeStructured())
    graph = StateGraph(AgentState)
    graph.add_node("recommend", recommend.recommend_node)
    graph.add_edge(START, "recommend")
    compiled = graph.compile()

    chunks = [
        chunk
        async for chunk in compiled.astream(
            {
                "normalized": [_item("SKU1", 200)],
                "preferences": {"budget": 300},
                "profile_preferences": {},
                "degraded_sources": [],
            },
            stream_mode=["updates", "custom"],
        )
    ]

    preview_index = next(
        index for index, (mode, payload) in enumerate(chunks)
        if mode == "custom" and payload.get("event") == "recommendation_preview"
    )
    update_index = next(
        index for index, (mode, payload) in enumerate(chunks)
        if mode == "updates" and "recommend" in payload
    )
    preview = chunks[preview_index][1]["recommendation"]

    assert preview_index < update_index
    assert preview["items"] == [{
        "sku_id": "SKU1",
        "title": "SKU1",
        "final_price": 200,
        "platform": "pdd",
        "attributes": {},
        "image_url": "",
        "url": "",
        "reason": "",
    }]
    assert preview["recommendation_intro"] == "根据您的“预算 ¥300”需求，为你推荐1款候选"

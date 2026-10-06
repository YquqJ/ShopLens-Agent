"""检索改造的离线单测：description 生成 + 硬约束/语义两路拆分 + 数据源路由。"""
import json

import pytest
from langchain_core.messages import HumanMessage

from app.agents.nodes import retrieve
from app.agents.nodes.retrieve import _build_filters, _build_semantic_query
from app.eval.retrieval_eval import _matches
from app.mcp import adapter
from app.services.catalog import build_description, build_seed_products


def test_build_description_contains_category_and_all_attribute_values():
    p = build_seed_products(1)[0]
    desc = build_description(p["attributes"], p["category"])
    assert p["category"] in desc
    for value in p["attributes"].values():
        assert value in desc


def test_build_filters_keeps_only_closed_fields():
    prefs = {
        "category": "T恤", "color": "黑色", "material": "纯棉", "fit": "修身",
        "scene": "通勤", "budget": 200.0, "requirements": "显瘦一点",
    }
    assert _build_filters(prefs) == {
        "category": "T恤", "color": "黑色", "material": "纯棉",
        "scene": "通勤", "fit": "修身",
    }


def test_build_filters_falls_back_to_profile_lists():
    prefs = {"category": "T恤", "preferred_colors": ["黑色", "白色"]}
    filters = _build_filters(prefs)
    assert filters["category"] == "T恤"
    assert filters["color"] == ["黑色", "白色"]  # 会话无 color，回退长期偏好（$in）


def test_build_semantic_query_prefers_requirements():
    prefs = {"category": "T恤", "requirements": "显瘦、透气一点"}
    assert _build_semantic_query(prefs) == "显瘦、透气一点"


def test_build_semantic_query_falls_back_to_closed_fields_order():
    prefs = {"category": "T恤", "color": "黑色"}
    assert _build_semantic_query(prefs) == "黑色 T恤"


def test_matches_respects_hard_constraints():
    item = {
        "sku_id": "SKU0001", "category": "T恤",
        "attributes": {"颜色": "黑色", "风格": "简约", "材质": "纯棉", "场景": "周末", "版型": "修身"},
    }
    assert _matches(item, {"category": "T恤", "material": "纯棉"}) is True
    assert _matches(item, {"category": "外套"}) is False
    assert _matches(item, {"color": ["黑色", "白色"]}) is True  # 列表约束命中
    assert _matches(item, {"fit": "宽松"}) is False
    assert _matches(item, {}) is True  # 空约束 = 全部相关


def test_mcp_adapter_registers_real_source_server():
    assert adapter.SERVERS["zhidemai"] == "app.mcp.zhidemai"


@pytest.mark.anyio
async def test_retrieve_explicit_mock_mode_ignores_legacy_real_source_switch(monkeypatch):
    monkeypatch.setattr(retrieve, "_zhidemai_enabled", lambda: True)
    search = pytest.importorskip("unittest.mock").AsyncMock(return_value=[
        {"sku_id": "SKU0001", "title": "仿真耳机", "category": "耳机"},
    ])
    monkeypatch.setattr(retrieve.catalog, "search", search)

    result = await retrieve.retrieve_node(
        {"preferences": {"category": "耳机"}, "profile_preferences": {}, "messages": []},
        {"configurable": {"source_mode": "mock", "mcp_tools": {}}},
    )

    assert result["zdm_mode"] is False
    assert result["candidates"][0]["sku_id"] == "SKU0001"
    search.assert_awaited_once()


@pytest.mark.anyio
async def test_retrieve_real_mode_calls_zhidemai_mcp_and_builds_recommendable_items():
    class _Tool:
        def __init__(self, value):
            self.value = value
            self.calls = []

        async def ainvoke(self, payload):
            self.calls.append(payload)
            return self.value

    tool = _Tool([{
        "type": "text",
        "text": json.dumps([{
            "title": "真实降噪耳机",
            "price": 399,
            "mall": "京东",
            "brand": "Example",
            "category": "耳机",
            "promotion": "满 300 减 20",
            "url": "https://example.test/item",
        }], ensure_ascii=False),
    }])

    result = await retrieve.retrieve_node(
        {
            "preferences": {"category": "耳机", "budget": 500},
            "profile_preferences": {},
            "messages": [HumanMessage("想买降噪耳机")],
        },
        {"configurable": {
            "source_mode": "real",
            "mcp_tools": {"zhidemai_search_real_products": tool},
        }},
    )

    assert tool.calls == [{
        "product_query": "耳机",
        "size": 5,
        "category_name": "耳机",
        "max_price": 500,
    }]
    assert result["zdm_mode"] is True
    assert result["normalized"][0]["sku_id"] == "ZDM01"
    assert result["normalized"][0]["best"]["platform"] == "京东"


@pytest.mark.anyio
async def test_retrieve_real_mode_does_not_silently_fallback_without_mcp_tool():
    with pytest.raises(RuntimeError, match="zhidemai_search_real_products"):
        await retrieve.retrieve_node(
            {"preferences": {"category": "耳机"}, "profile_preferences": {}, "messages": []},
            {"configurable": {"source_mode": "real", "mcp_tools": {}}},
        )

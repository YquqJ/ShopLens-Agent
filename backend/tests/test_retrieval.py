"""检索改造的离线单测：description 生成 + 硬约束/语义两路拆分 + 相关性规则判定（不碰 embedding/DB）。"""
from app.agents.nodes.retrieve import _build_filters, _build_semantic_query
from app.eval.retrieval_eval import _matches
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
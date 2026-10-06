from copy import deepcopy
from pathlib import Path

from app.services.catalog import build_seed_products
from app.services.memory import memory_tags, merge_memory


def test_active_demo_eval_and_smoke_fixtures_stay_in_apparel_domain() -> None:
    fixture_paths = [
        Path("app/eval/golden_set.json"),
        Path("app/eval/clarify_set.json"),
        Path("app/eval/memory_set.json"),
        Path("app/eval/chaos.py"),
        Path("app/eval/token_profiler.py"),
        Path("app/agents/demo.py"),
        Path("app/mcp/smoke_zhidemai.py"),
    ]
    legacy_terms = (
        "项链", "手链", "戒指", "耳环", "手镯", "胸针", "首饰",
        "手机", "耳机", "iphone", "ipad", "macbook", "airpods",
    )

    for path in fixture_paths:
        content = path.read_text(encoding="utf-8")
        assert not any(term in content for term in legacy_terms), path


def test_seed_catalog_contains_only_apparel_with_reusable_attributes() -> None:
    products = build_seed_products(24)
    apparel_categories = {"T恤", "衬衫", "卫衣", "外套", "裤装", "鞋"}

    assert len(products) == 24
    assert {product["category"] for product in products} <= apparel_categories
    assert all(
        {"颜色", "风格", "材质", "场景", "性别"} <= set(product["attributes"])
        and "尺码" not in product["attributes"]
        for product in products
    )


def test_seed_catalog_uses_plausible_materials_for_each_apparel_category() -> None:
    allowed_materials = {
        "T恤": {"纯棉", "亚麻"},
        "衬衫": {"纯棉", "亚麻", "牛仔"},
        "卫衣": {"纯棉", "抓绒"},
        "外套": {"羊毛", "牛仔", "棉服"},
        "裤装": {"纯棉", "亚麻", "牛仔"},
        "鞋": {"真皮", "帆布", "网面"},
    }

    for product in build_seed_products(60):
        assert product["attributes"]["材质"] in allowed_materials[product["category"]]


def test_seed_catalog_varies_reusable_attributes_within_each_category() -> None:
    by_category: dict[str, list[dict]] = {}
    for product in build_seed_products(120):
        by_category.setdefault(product["category"], []).append(product["attributes"])

    assert by_category
    for attributes in by_category.values():
        assert len({item["颜色"] for item in attributes}) >= 4
        assert len({item["风格"] for item in attributes}) >= 2
        assert len({item["材质"] for item in attributes}) >= 2
        assert len({item["场景"] for item in attributes}) >= 2
        assert {item["性别"] for item in attributes} <= {"男款", "女款"}
def test_merge_memory_combines_preferences_and_forgets_requested_key() -> None:
    profile = {
        "preferred_colors": ["黑色", "蓝色"],
        "preferred_styles": ["简约"],
        "preferred_materials": ["纯棉"],
    }
    update = {
        "preferred_colors": ["蓝色", "米白"],
        "preferred_styles": ["通勤"],
        "forget": ["preferred_materials"],
    }

    assert merge_memory(profile, update) == {
        "preferred_colors": ["黑色", "蓝色", "米白"],
        "preferred_styles": ["简约", "通勤"],
    }


def test_merge_memory_only_keeps_durable_preferences_without_mutating_inputs() -> None:
    profile = {"preferred_colors": ["黑色"], "scene": "通勤"}
    update = {
        "preferred_colors": ["蓝色"],
        "preferred_budget_range": {"min": 200, "max": 500},
        "budget": 300,
        "scene": "约会",
        "unknown": "ignore me",
        "preferred_materials": None,
    }
    original_profile = deepcopy(profile)
    original_update = deepcopy(update)

    assert merge_memory(profile, update) == {
        "preferred_colors": ["黑色", "蓝色"],
    }
    assert profile == original_profile
    assert update == original_update


def test_merge_memory_never_persists_budget_ranges() -> None:
    assert merge_memory(
        {"preferred_budget_range": {"min": 200, "max": 500}},
        {"preferred_budget_range": {"min": 300, "max": 600}},
    ) == {}


def test_merge_memory_supports_string_forget_and_empty_updates_are_noops() -> None:
    profile = {"preferred_colors": ["黑色"], "preferred_materials": ["纯棉"]}

    assert merge_memory(profile, {"forget": "preferred_materials"}) == {
        "preferred_colors": ["黑色"],
    }
    assert merge_memory(profile, {"preferred_colors": [], "preferred_materials": None}) == profile


def test_memory_tags_returns_readable_nonempty_preferences() -> None:
    profile = {
        "preferred_colors": ["黑色", "蓝色"],
        "preferred_platforms": ["淘宝"],  # legacy persisted row
        "avoid_preferences": ["聚酯纤维"],
    }

    assert memory_tags(profile) == [
        "颜色：黑色、蓝色",
        "避雷：聚酯纤维",
    ]


def test_memory_tags_returns_gender_preferences_without_size() -> None:
    assert memory_tags({
        "preferred_genders": ["男款"],
        "legacy_size": ["L"],
    }) == ["性别：男款"]

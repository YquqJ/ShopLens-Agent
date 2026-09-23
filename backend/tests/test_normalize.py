import pytest

from app.services.normalize import normalize
from app.agents.nodes.normalize import normalize_node
from app.agents.nodes.retrieve import _zhidemai_to_normalized


_CANDIDATES = [{
    "sku_id": "SKU-PREFERENCE-1",
    "title": "黑色 简约 纯棉 通勤 衬衫",
    "category": "衬衫",
    "attributes": {"颜色": "黑色", "风格": "简约"},
}]

_QUOTES = [
    {"sku_id": "SKU-PREFERENCE-1", "platform": "taobao", "price": 300, "coupon": None, "shipping_fee": 0, "eta_days": 2},
    {"sku_id": "SKU-PREFERENCE-1", "platform": "pdd", "price": 250, "coupon": None, "shipping_fee": 0, "eta_days": 1},
]


def test_normalize_prefers_available_profile_platform_over_lower_cost_quote() -> None:
    item = normalize(_QUOTES, _CANDIDATES, preferred_platforms=["淘宝"])[0]

    assert item["best"]["platform"] == "taobao"
    assert item["best_strategy"] == "preferred_platform"
    assert item["cost_best"]["platform"] == "pdd"


def test_normalize_falls_back_to_lowest_cost_when_preferred_platform_is_unavailable() -> None:
    item = normalize(_QUOTES, _CANDIDATES, preferred_platforms=["京东"])[0]

    assert item["best"]["platform"] == "pdd"
    assert item["best_strategy"] == "lowest_total_cost"
    assert item["cost_best"]["platform"] == "pdd"


def test_normalize_keeps_lowest_cost_selection_without_profile_platform_preference() -> None:
    item = normalize(_QUOTES, _CANDIDATES)[0]

    assert item["best"]["platform"] == "pdd"
    assert item["best_strategy"] == "lowest_total_cost"


@pytest.mark.anyio
async def test_normalize_node_applies_profile_platform_preference() -> None:
    result = await normalize_node({
        "quotes": _QUOTES,
        "candidates": _CANDIDATES,
        "profile_preferences": {"preferred_platforms": ["淘宝"]},
    })

    assert result["normalized"][0]["best"]["platform"] == "taobao"


def test_real_catalog_normalized_items_follow_the_shared_normalization_contract() -> None:
    item = _zhidemai_to_normalized([{
        "title": "黑色简约衬衫",
        "price": 299,
        "mall": "淘宝",
    }])[0]

    assert item["cost_best"] == item["best"]
    assert item["best_strategy"] == "lowest_total_cost"

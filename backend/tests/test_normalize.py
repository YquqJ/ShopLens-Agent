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


def test_normalize_chooses_the_lowest_total_cost() -> None:
    item = normalize(_QUOTES, _CANDIDATES)[0]

    assert item["best"]["platform"] == "pdd"


def test_normalize_chooses_comprehensive_best_even_when_delivery_is_slower() -> None:
    quotes = [
        {"sku_id": "SKU-PREFERENCE-1", "platform": "taobao", "price": 167, "coupon": None, "shipping_fee": 0, "eta_days": 1},
        {"sku_id": "SKU-PREFERENCE-1", "platform": "pdd", "price": 149, "coupon": None, "shipping_fee": 0, "eta_days": 3},
    ]

    item = normalize(quotes, _CANDIDATES)[0]

    assert item["best"]["platform"] == "taobao"
    assert item["best"]["total_cost"] == 177


def test_normalize_preserves_catalog_media_links() -> None:
    candidates = [{
        **_CANDIDATES[0],
        "image_url": "https://img.example/catalog.jpg",
        "url": "https://mall.example/catalog",
    }]

    item = normalize(_QUOTES, candidates)[0]

    assert item["image_url"] == "https://img.example/catalog.jpg"
    assert item["url"] == "https://mall.example/catalog"


@pytest.mark.anyio
async def test_normalize_node_ignores_legacy_profile_platform_preference() -> None:
    result = await normalize_node({
        "quotes": _QUOTES,
        "candidates": _CANDIDATES,
        "profile_preferences": {"preferred_platforms": ["淘宝"]},
    })

    assert result["normalized"][0]["best"]["platform"] == "pdd"


def test_real_catalog_normalized_items_follow_the_shared_normalization_contract() -> None:
    item = _zhidemai_to_normalized([{
        "title": "黑色简约衬衫",
        "price": 299,
        "mall": "淘宝",
    }])[0]

    assert item["best"]["platform"] == "淘宝"

from app.agents.nodes import clarify


def test_normalize_preferences_preserves_explicit_platform():
    assert clarify.normalize_preferences({"platform": "jd"})["platform"] == "jd"


def test_normalize_preferences_maps_known_platform_aliases():
    assert clarify.normalize_preferences({"platform": "京东"})["platform"] == "jd"


def test_clothing_gender_is_not_required_for_other_product_categories():
    assert clarify._missing({"category": "手机", "budget": 3000}) == []

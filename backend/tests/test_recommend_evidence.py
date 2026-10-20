from app.agents.nodes.recommend import (
    ComparisonResult,
    _build_evidence_items,
    validate_comparison,
)


def _fact(sku, field, value):
    return {
        "evidence_id": f"{sku}:zdm:{field}",
        "sku_id": sku,
        "source": "zhidemai",
        "field": field,
        "value": value,
    }


def test_unknown_best_sku_is_cleared():
    result = ComparisonResult(summary="候选比较", best_sku_id="OTHER", dimensions=[], reasons=[])

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01", "title": "商品 A"}],
        {"ZDM01": [_fact("ZDM01", "page_price", 128)]},
    )

    assert validated["best_sku_id"] is None


def test_comparison_values_are_loaded_from_facts_not_model_output():
    result = ComparisonResult(
        summary="候选比较",
        best_sku_id="ZDM01",
        dimensions=[{
            "field": "page_price",
            "evidence_by_sku": {
                "ZDM01": "ZDM01:zdm:page_price",
                "ZDM02": "ZDM02:zdm:page_price",
            },
        }],
        reasons=[],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01"}, {"sku_id": "ZDM02"}],
        {
            "ZDM01": [_fact("ZDM01", "page_price", 128)],
            "ZDM02": [_fact("ZDM02", "page_price", 158)],
        },
    )

    assert validated["dimensions"][0]["values"] == {"ZDM01": 128, "ZDM02": 158}


def test_invalid_evidence_dimension_and_reason_are_removed():
    result = ComparisonResult(
        summary="比较",
        best_sku_id="ZDM01",
        dimensions=[{
            "field": "page_price",
            "evidence_by_sku": {"ZDM01": "missing", "ZDM02": "ZDM02:zdm:page_price"},
        }],
        reasons=[
            {"sku_id": "OTHER", "reason": "越界", "evidence_ids": ["missing"]},
            {"sku_id": "ZDM01", "reason": "有证据", "evidence_ids": ["ZDM01:zdm:page_price"]},
        ],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01"}, {"sku_id": "ZDM02"}],
        {"ZDM01": [_fact("ZDM01", "page_price", 128)], "ZDM02": [_fact("ZDM02", "page_price", 158)]},
    )

    assert validated["dimensions"] == []
    assert [reason["sku_id"] for reason in validated["reasons"]] == ["ZDM01"]
    assert validated["best_sku_id"] is None


def test_best_sku_requires_a_valid_dimension_covering_it():
    result = ComparisonResult(
        summary="比较",
        best_sku_id="ZDM01",
        dimensions=[{
            "field": "battery",
            "evidence_by_sku": {"ZDM01": "ZDM01:kg:battery"},
        }],
        reasons=[],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01"}, {"sku_id": "ZDM02"}],
        {"ZDM01": [{"evidence_id": "ZDM01:kg:battery", "field": "battery", "value": "5000mAh"}], "ZDM02": []},
    )

    assert validated["best_sku_id"] is None


def test_evidence_prompt_includes_source_values_for_reason_generation():
    items = _build_evidence_items(
        [{"sku_id": "ZDM01", "title": "商品 A"}],
        {"ZDM01": [_fact("ZDM01", "page_price", 128)]},
    )

    assert items == [{
        "sku_id": "ZDM01",
        "title": "商品 A",
        "evidence": [{
            "evidence_id": "ZDM01:zdm:page_price",
            "field": "page_price",
            "value": 128,
        }],
    }]


def test_comparison_validates_regular_price_timing_against_source_values():
    result = ComparisonResult(
        summary="候选比较",
        best_sku_id=None,
        dimensions=[],
        reasons=[],
        price_assessments=[{
            "sku_id": "ZDM01",
            "reference_label": "常卖价",
            "assessment": "wait",
            "evidence_ids": ["ZDM01:zdm:page_price", "ZDM01:zdm:price_tags"],
        }],
    )
    evidence = {
        "ZDM01": [
            _fact("ZDM01", "page_price", 599),
            _fact("ZDM01", "price_tags", [{"title": "常卖价", "price": 499}]),
        ],
    }

    validated = validate_comparison(result, [{"sku_id": "ZDM01", "page_price": 599}], evidence)

    assert validated["price_insights"] == [{
        "sku_id": "ZDM01",
        "current_price": 599.0,
        "reference_label": "常卖价",
        "reference_price": 499.0,
        "reference_kind": "reference",
        "assessment": "wait",
        "evidence_ids": ["ZDM01:zdm:page_price", "ZDM01:zdm:price_tags"],
    }]


def test_regular_price_tag_recommends_buying_when_current_price_is_lower():
    result = ComparisonResult(
        summary="比较",
        dimensions=[],
        reasons=[],
        price_assessments=[{
            "sku_id": "ZDM01",
            "reference_label": "常卖价",
            "assessment": "good_time",
            "evidence_ids": ["ZDM01:zdm:page_price", "ZDM01:zdm:price_tags"],
        }],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01", "page_price": 399}],
        {"ZDM01": [
            _fact("ZDM01", "page_price", 399),
            _fact("ZDM01", "price_tags", [{"title": "常卖价", "price": 499}]),
        ]},
    )

    assert validated["price_insights"][0]["reference_kind"] == "reference"
    assert validated["price_insights"][0]["assessment"] == "good_time"
    assert validated["price_insights"][0]["reference_label"] == "常卖价"


def test_comparison_ignores_non_regular_price_label_or_no_tag():
    result = ComparisonResult(
        summary="比较",
        dimensions=[],
        reasons=[],
        price_assessments=[{
            "sku_id": "ZDM01",
            "reference_label": "历史最低价",
                "assessment": "wait",
                "evidence_ids": ["ZDM01:zdm:page_price"],
        }],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01", "page_price": 399}],
        {"ZDM01": [_fact("ZDM01", "page_price", 399)]},
    )

    assert validated["price_insights"] == []


def test_comparison_uses_verified_price_fallback_when_model_omits_assessment():
    result = ComparisonResult(summary="比较", dimensions=[], reasons=[])

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01", "page_price": 599}],
        {"ZDM01": [
            _fact("ZDM01", "page_price", 599),
            _fact("ZDM01", "price_tags", [{"title": "历史最低价", "price": 399}]),
        ]},
    )

    assert validated["price_insights"] == []


def test_comparison_uses_verified_price_fallback_when_model_evidence_does_not_match():
    result = ComparisonResult(
        summary="比较",
        dimensions=[],
        reasons=[],
        price_assessments=[{
            "sku_id": "ZDM01",
            "reference_label": "历史最低价",
            "assessment": "good_time",
            "evidence_ids": ["unrelated-evidence"],
        }],
    )

    validated = validate_comparison(
        result,
        [{"sku_id": "ZDM01", "page_price": 599}],
        {"ZDM01": [
            _fact("ZDM01", "page_price", 599),
            _fact("ZDM01", "price_tags", [{"title": "历史最低价", "price": 399}]),
        ]},
    )

    assert validated["price_insights"] == []


def test_equal_current_and_regular_price_is_not_displayed():
    validated = validate_comparison(
        ComparisonResult(summary="比较", dimensions=[], reasons=[]),
        [{"sku_id": "ZDM01", "page_price": 499}],
        {"ZDM01": [
            _fact("ZDM01", "page_price", 499),
            _fact("ZDM01", "price_tags", [{"title": "常卖价", "price": 499}]),
        ]},
    )

    assert validated["price_insights"] == []

from app.agents.nodes.recommend import ComparisonResult, _build_evidence_items, validate_comparison


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

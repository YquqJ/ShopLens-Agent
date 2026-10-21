from app.agents.nodes import retrieve


def _build_zdm_evidence(item, sku_id):
    from app.services.product_evidence import build_zdm_evidence

    return build_zdm_evidence(item, sku_id)


def test_build_zdm_evidence_omits_missing_values_and_uses_stable_ids():
    item = {
        "page_price": 128,
        "brand": "Acme",
        "good_comments_share": None,
    }

    facts = _build_zdm_evidence(item, "ZDM01")

    assert [(fact["evidence_id"], fact["value"]) for fact in facts] == [
        ("ZDM01:zdm:page_price", 128),
        ("ZDM01:zdm:brand", "Acme"),
    ]
    assert all(fact["source"] == "zhidemai" for fact in facts)


def test_build_zdm_evidence_keeps_only_finite_numeric_prices():
    facts = _build_zdm_evidence({"page_price": float("nan"), "title": "商品"}, "ZDM01")

    assert [fact["field"] for fact in facts] == ["title"]


def test_real_search_payload_maps_known_malls_and_ignores_unknown_platform():
    assert retrieve.build_real_search_payload({"platform": "pdd"})["mall_id"] == 8645
    assert "mall_id" not in retrieve.build_real_search_payload({"platform": "unknown"})


def test_real_search_payload_requests_three_candidates():
    payload = retrieve.build_real_search_payload({"category": "冲锋衣"})

    assert payload["size"] == 3
    assert payload["category_name"] == "冲锋衣"

"""Normalize source-backed product facts into traceable evidence records."""
import math


_FIELD_SOURCES = {
    "page_price": ("page_price", "price"),
    "brand": ("brand", "brand_name"),
    "external_product_id": ("external_product_id", "product_id", "item_id", "id"),
    "title": ("title",),
    "category": ("category", "cate3_name", "cate2_name"),
    "mall": ("mall", "mall_name"),
    "good_comments_share": ("good_comments_share",),
    "price_tags": ("price_tags",),
    "promotion": ("promotion", "promotional_info"),
    "picture": ("picture", "main_picture"),
    "url": ("url",),
}


def _valid_value(field: str, value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, bool):
        return field != "page_price"
    if isinstance(value, (int, float)):
        return math.isfinite(float(value)) and (field != "page_price" or value >= 0)
    if field == "page_price":
        try:
            return math.isfinite(float(value)) and float(value) >= 0
        except (TypeError, ValueError):
            return False
    return isinstance(value, (str, list, dict))


def build_zdm_evidence(item: dict, sku_id: str) -> list[dict]:
    """Build stable evidence IDs from fields actually returned by the ZDM source."""
    facts = []
    for field, aliases in _FIELD_SOURCES.items():
        for source_ref in aliases:
            value = item.get(source_ref)
            if not _valid_value(field, value):
                continue
            if field == "page_price":
                value = float(value)
            facts.append({
                "evidence_id": f"{sku_id}:zdm:{field}",
                "sku_id": sku_id,
                "source": "zhidemai",
                "field": field,
                "value": value,
                "source_ref": source_ref,
            })
            break
    return facts

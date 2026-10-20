from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from app.api import orders as orders_api


@pytest.mark.anyio
async def test_orders_endpoint_forwards_date_filters_and_returns_page(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def page(**kwargs):
        captured.update(kwargs)
        return {
            "items": [{"order_id": 9, "title": "降噪耳机", "final_price": 299.0}],
            "total": 1,
            "page": 2,
            "page_size": 10,
            "categories": ["耳机"],
        }

    monkeypatch.setattr(orders_api.order_query_service, "search_orders_page", page)

    result = await orders_api.list_orders(
        profile_id="profile-1",
        page=2,
        page_size=10,
        start_date="2026-10-01",
        end_date="2026-10-21",
        keyword="降噪",
        category="耳机",
    )

    assert captured == {
        "profile_key": "profile-1",
        "page": 2,
        "page_size": 10,
        "start_at": datetime(2026, 10, 1, tzinfo=ZoneInfo("Asia/Shanghai")),
        "end_at": datetime(2026, 10, 22, tzinfo=ZoneInfo("Asia/Shanghai")),
        "keyword": "降噪",
        "category": "耳机",
    }
    assert result["items"][0]["order_id"] == 9
    assert result["categories"] == ["耳机"]


@pytest.mark.anyio
async def test_orders_endpoint_rejects_invalid_date_range(monkeypatch) -> None:
    async def fail_if_called(**_kwargs):
        raise AssertionError("query should not run for invalid date range")

    monkeypatch.setattr(orders_api.order_query_service, "search_orders_page", fail_if_called)

    with pytest.raises(HTTPException, match="开始日期不能晚于结束日期"):
        await orders_api.list_orders(
            profile_id="profile-1",
            page=1,
            page_size=20,
            start_date="2026-10-22",
            end_date="2026-10-21",
        )

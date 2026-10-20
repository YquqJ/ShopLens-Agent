from datetime import datetime
from zoneinfo import ZoneInfo
from langchain_core.messages import HumanMessage

import pytest

from app.services.order_query import (
    group_orders,
    normalize_order_query,
    resolve_time_range,
    summarize_orders,
)
from app.agents.nodes.route import _fast_path
from app.agents.nodes.order_query import parse_order_query_node


NOW = datetime(2026, 10, 21, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_resolve_yesterday_uses_half_open_local_day_range():
    result = resolve_time_range("昨天我买了什么", NOW, "Asia/Shanghai")

    assert result.start_at.isoformat() == "2026-10-20T00:00:00+08:00"
    assert result.end_at.isoformat() == "2026-10-21T00:00:00+08:00"
    assert result.label == "昨天"


def test_resolve_previous_week_and_recent_days():
    previous_week = resolve_time_range("上周买了哪些商品", NOW, "Asia/Shanghai")
    recent = resolve_time_range("最近 30 天买耳机花了多少钱", NOW, "Asia/Shanghai")

    assert previous_week.start_at.isoformat() == "2026-10-12T00:00:00+08:00"
    assert previous_week.end_at.isoformat() == "2026-10-19T00:00:00+08:00"
    assert recent.start_at.isoformat() == "2026-09-21T15:30:00+08:00"
    assert recent.end_at == NOW


def test_resolve_concrete_date_range_and_naive_now_in_app_timezone():
    result = resolve_time_range("2026-10-01 至 2026-10-03 的订单", datetime(2026, 10, 21, 15, 30), "Asia/Shanghai")

    assert result.start_at.isoformat() == "2026-10-01T00:00:00+08:00"
    assert result.end_at.isoformat() == "2026-10-04T00:00:00+08:00"


def test_normalize_query_extracts_category_platform_and_summary_type():
    parsed_time = resolve_time_range("今年在京东买了什么", NOW, "Asia/Shanghai")
    result = normalize_order_query("今年在京东买了什么", parsed_time)

    assert result.keyword is None
    assert result.category is None
    assert result.platform == "jd"
    assert result.query_type == "search"

    summary = normalize_order_query(
        "最近 30 天买耳机花了多少钱", resolve_time_range("最近 30 天", NOW, "Asia/Shanghai")
    )
    assert summary.category == "耳机"
    assert summary.query_type == "summary"


def test_summarize_orders_and_group_orders_are_decimal_safe():
    orders = [
        {"title": "降噪耳机", "platform": "jd", "final_price": 100.10, "category": "耳机"},
        {"title": "蓝牙耳机", "platform": "jd", "final_price": 200.20, "category": "耳机"},
        {"title": "手机壳", "platform": "taobao", "final_price": 50, "category": "手机配件"},
    ]

    summary = summarize_orders(orders, group_by="platform")
    assert summary["order_count"] == 3
    assert summary["total_amount"] == 350.30
    assert summary["average_order_amount"] == 116.77
    assert summary["groups"] == [
        {"key": "jd", "order_count": 2, "total_amount": 300.30},
        {"key": "taobao", "order_count": 1, "total_amount": 50.0},
    ]
    assert group_orders(orders, "category")[0]["key"] == "耳机"


def test_resolve_time_range_requires_explicit_time():
    with pytest.raises(ValueError, match="时间范围"):
        resolve_time_range("我买了什么", NOW, "Asia/Shanghai")


def test_route_distinguishes_order_search_from_shopping():
    assert _fast_path({"messages": [HumanMessage("昨天我买了什么")], "preferences": {}}) == "order_search"
    assert _fast_path({"messages": [HumanMessage("最近 30 天买耳机花了多少钱")], "preferences": {}}) == "order_summary"
    assert _fast_path({"messages": [HumanMessage("我想买耳机")], "preferences": {}}) is None


@pytest.mark.anyio
async def test_order_query_node_clears_previous_query_and_asks_for_time():
    result = await parse_order_query_node({"messages": [HumanMessage("我买了什么")], "intent": "order_search"})

    assert result["order_query"] is None
    assert result["order_results"] == []
    assert "时间范围" in result["order_clarification"]

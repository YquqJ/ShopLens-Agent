import json
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
from app.agents.nodes.order_query import parse_order_query_node, query_orders_node, summarize_orders_node
from app.agents.graph import build_graph
from app.agents.state import initial_state


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


@pytest.mark.parametrize(
    ("text", "start", "label"),
    [
        ("这两天买了什么", "2026-10-20T00:00:00+08:00", "这2天"),
        ("这几天买了什么", "2026-10-19T00:00:00+08:00", "这3天"),
        ("这三天买了什么", "2026-10-19T00:00:00+08:00", "这3天"),
        ("近五天买了什么", "2026-10-17T00:00:00+08:00", "这5天"),
        ("这两天都买了些什么东西", "2026-10-20T00:00:00+08:00", "这2天"),
    ],
)
def test_resolve_colloquial_day_spans(text, start, label):
    result = resolve_time_range(text, NOW, "Asia/Shanghai")

    assert result.start_at.isoformat() == start
    # 区间右边界始终是明天零点，包含今天
    assert result.end_at.isoformat() == "2026-10-22T00:00:00+08:00"
    assert result.label == label


def test_resolve_current_week_and_day_before_yesterday():
    this_week = resolve_time_range("本周买了什么", NOW, "Asia/Shanghai")
    day_before = resolve_time_range("大前天买了什么", NOW, "Asia/Shanghai")

    assert this_week.start_at.isoformat() == "2026-10-19T00:00:00+08:00"
    assert this_week.end_at.isoformat() == "2026-10-22T00:00:00+08:00"
    assert day_before.start_at.isoformat() == "2026-10-18T00:00:00+08:00"
    assert day_before.end_at.isoformat() == "2026-10-19T00:00:00+08:00"


def test_normalize_colloquial_span_does_not_create_keyword():
    parsed_time = resolve_time_range("这两天买了什么", NOW, "Asia/Shanghai")
    result = normalize_order_query("这两天买了什么", parsed_time)

    assert result.keyword is None
    assert result.category is None
    assert result.query_type == "search"
    assert result.label == "这2天"


@pytest.mark.parametrize(
    "text",
    [
        "这两天我都买了些什么东西",
        "这两天买些什么",
        "这两天买了些什么",
        "这几天我都买过些什么东西",
    ],
)
def test_normalize_colloquial_span_ignores_generic_question_words(text):
    parsed_time = resolve_time_range(text, NOW, "Asia/Shanghai")
    result = normalize_order_query(text, parsed_time)

    assert result.keyword is None
    assert result.category is None


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


def test_normalize_query_treats_general_requests_without_creating_keyword():
    parsed_time = resolve_time_range("我今天买了哪些东西", NOW, "Asia/Shanghai")
    result = normalize_order_query("我今天买了哪些东西", parsed_time)

    assert result.keyword is None
    assert result.category is None
    assert result.query_type == "search"


def test_normalize_query_strips_measure_words_but_keeps_category_filters():
    parsed_time = resolve_time_range("今天买了个鼠标", NOW, "Asia/Shanghai")
    result = normalize_order_query("今天买了个鼠标", parsed_time)

    assert result.category == "鼠标"
    assert result.keyword is None


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
    assert _fast_path({"messages": [HumanMessage("这两天买了什么")], "preferences": {}}) == "order_search"
    assert _fast_path({"messages": [HumanMessage("这两天买耳机花了多少钱")], "preferences": {}}) == "order_summary"
    assert _fast_path({"messages": [HumanMessage("这两天想买耳机")], "preferences": {}}) is None


@pytest.mark.anyio
async def test_order_query_node_clears_previous_query_and_asks_for_time():
    result = await parse_order_query_node({"messages": [HumanMessage("我买了什么")], "intent": "order_search"})

    assert result["order_query"] is None
    assert result["order_results"] == []
    assert "时间范围" in result["order_clarification"]


@pytest.mark.anyio
async def test_order_query_history_keeps_results_by_turn():
    class SearchTool:
        name = "orders_search_orders"

        def __init__(self):
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return [{"order_id": 7, "title": "降噪耳机", "final_price": 299}]

    search_tool = SearchTool()
    parsed = await parse_order_query_node({"messages": [HumanMessage("昨天买了什么")], "intent": "order_search"})
    result = await query_orders_node(
        {**parsed, "turn_id": "turn-1"},
        {
            "configurable": {
                "profile_id": "profile-1",
                "mcp_tools": {search_tool.name: search_tool},
            }
        },
    )

    assert result["order_query_history"][0]["turn_id"] == "turn-1"
    assert result["order_query_history"][0]["results"][0]["order_id"] == 7
    assert search_tool.payload == {
        "start_at": parsed["order_query"]["start_at"],
        "end_at": parsed["order_query"]["end_at"],
        "keyword": None,
        "category": None,
        "platform": None,
        "profile_key": "profile-1",
    }


@pytest.mark.anyio
async def test_order_query_unwraps_mcp_text_content_before_returning_results():
    orders = [{
        "order_id": 7,
        "order_number": "202610210001001",
        "title": "降噪耳机",
        "image_url": "https://example.com/headphones.png",
        "platform": "京东",
        "final_price": 299.0,
        "created_at": "2026-10-20T12:00:00+08:00",
    }]

    class SearchTool:
        name = "orders_search_orders"

        async def ainvoke(self, _payload):
            return [{"type": "text", "text": json.dumps(orders, ensure_ascii=False)}]

    parsed = await parse_order_query_node({
        "messages": [HumanMessage("昨天买了什么")],
        "intent": "order_search",
    })
    result = await query_orders_node(
        {**parsed, "turn_id": "turn-mcp-content"},
        {"configurable": {
            "profile_id": "profile-mcp-content",
            "mcp_tools": {SearchTool.name: SearchTool()},
        }},
    )

    assert result["order_results"] == orders, result
    assert result["order_query_history"][0]["results"] == orders


@pytest.mark.anyio
async def test_order_query_collects_orders_from_separate_mcp_text_blocks():
    orders = [
        {"order_id": 7, "title": "降噪耳机", "final_price": 299.0},
        {"order_id": 8, "title": "手机壳", "final_price": 39.0},
    ]

    class SearchTool:
        name = "orders_search_orders"

        async def ainvoke(self, _payload):
            return [
                {"type": "text", "text": json.dumps(order, ensure_ascii=False)}
                for order in orders
            ]

    parsed = await parse_order_query_node({
        "messages": [HumanMessage("这两天买了什么")],
        "intent": "order_search",
    })
    result = await query_orders_node(
        {**parsed, "turn_id": "turn-separate-content"},
        {"configurable": {
            "profile_id": "profile-separate-content",
            "mcp_tools": {SearchTool.name: SearchTool()},
        }},
    )

    assert result["order_results"] == orders
    assert result["order_query_history"][0]["results"] == orders


@pytest.mark.anyio
async def test_order_summary_uses_mcp_aggregation_and_keeps_history_for_same_turn():
    class SummaryTool:
        name = "orders_summarize_orders"

        def __init__(self):
            self.payload = None

        async def ainvoke(self, payload):
            self.payload = payload
            return {
                "order_count": 1,
                "total_amount": 100.0,
                "average_order_amount": 100.0,
                "groups": [{"key": "jd", "order_count": 1, "total_amount": 100.0}],
            }

    summary_tool = SummaryTool()
    result = await summarize_orders_node({
        "turn_id": "turn-2",
        "order_query": {
            "label": "最近30天", "group_by": "platform",
            "start_at": "2026-09-21T00:00:00+08:00", "end_at": "2026-10-21T00:00:00+08:00",
        },
    }, {
        "configurable": {
            "profile_id": "profile-2",
            "mcp_tools": {summary_tool.name: summary_tool},
        }
    })

    assert result["order_query_history"][0]["turn_id"] == "turn-2"
    assert result["order_query_history"][0]["summary"]["total_amount"] == 100.0
    assert summary_tool.payload == {
        "start_at": "2026-09-21T00:00:00+08:00",
        "end_at": "2026-10-21T00:00:00+08:00",
        "keyword": None,
        "category": None,
        "platform": None,
        "group_by": "platform",
        "profile_key": "profile-2",
    }


@pytest.mark.anyio
async def test_order_summary_unwraps_mcp_text_content():
    summary = {
        "order_count": 2,
        "total_amount": 399.0,
        "average_order_amount": 199.5,
        "groups": [],
    }

    class SummaryTool:
        name = "orders_summarize_orders"

        async def ainvoke(self, _payload):
            return [{"type": "text", "text": json.dumps(summary, ensure_ascii=False)}]

    result = await summarize_orders_node(
        {
            "turn_id": "turn-summary-content",
            "order_query": {
                "start_at": "2026-10-20T00:00:00+08:00",
                "end_at": "2026-10-22T00:00:00+08:00",
            },
        },
        {"configurable": {
            "profile_id": "profile-summary-content",
            "mcp_tools": {SummaryTool.name: SummaryTool()},
        }},
    )

    assert result["order_summary"] == summary
    assert result["order_query_history"][0]["summary"] == summary


@pytest.mark.anyio
async def test_summary_intent_calls_aggregation_tool_without_searching_twice():
    class SearchTool:
        name = "orders_search_orders"

        async def ainvoke(self, _payload):
            raise AssertionError("summary flow should not call the detail search tool")

    class SummaryTool:
        name = "orders_summarize_orders"

        async def ainvoke(self, _payload):
            return {
                "order_count": 2,
                "total_amount": 300.0,
                "average_order_amount": 150.0,
                "groups": [],
            }

    graph = build_graph()
    state = await graph.ainvoke(
        initial_state("最近 30 天买耳机花了多少钱"),
        {
            "configurable": {
                "thread_id": "summary-test",
                "profile_id": "profile-3",
                "mcp_tools": {
                    SearchTool.name: SearchTool(),
                    SummaryTool.name: SummaryTool(),
                },
            }
        },
    )

    assert state["order_summary"]["order_count"] == 2

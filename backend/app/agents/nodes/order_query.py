"""订单查询流程节点：解析条件、检索订单、生成结构化统计。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig

from app.agents.state import AgentState
from app.services import order_query as order_query_service
from app.agents.nodes.memory import profile_key_from_config


def _latest_user_text(state: AgentState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


async def parse_order_query_node(state: AgentState) -> dict:
    text = _latest_user_text(state)
    try:
        parsed_time = order_query_service.resolve_time_range(text)
    except ValueError:
        question = "请补充明确的时间范围，例如“今天”“昨天”“这两天”“上周”或“最近 30 天”。"
        return {
            "order_query": None,
            "order_clarification": question,
            "order_results": [],
            "order_summary": None,
            "order_query_history": [{
                "turn_id": state.get("turn_id"), "query": None, "results": [],
                "summary": None, "clarification": question, "error": None,
            }],
        }
    query = order_query_service.normalize_order_query(text, parsed_time)
    if state.get("intent") == "order_summary":
        query = query.__class__(**{**query.__dict__, "query_type": "summary"})
    return {
        "order_query": {
            **query.__dict__,
            "start_at": query.start_at.isoformat(),
            "end_at": query.end_at.isoformat(),
        },
        "order_clarification": None,
        "order_results": [],
        "order_summary": None,
        "order_error": None,
    }


async def query_orders_node(state: AgentState, config: RunnableConfig) -> dict:
    query = state.get("order_query") or {}
    if not query:
        return {}
    try:
        from datetime import datetime
        results = await order_query_service.search_orders(
            datetime.fromisoformat(query["start_at"]), datetime.fromisoformat(query["end_at"]),
            keyword=query.get("keyword"), category=query.get("category"),
            platform=query.get("platform"), profile_key=profile_key_from_config(config),
        )
    except Exception as exc:  # noqa: BLE001
        message = "订单查询暂时不可用，请稍后重试。"
        return {
            "order_error": str(exc), "order_results": [],
            "order_query_history": [{
                "turn_id": state.get("turn_id"), "query": query, "results": [],
                "summary": None, "clarification": None, "error": message,
            }],
        }
    return {
        "order_results": results, "order_error": None,
        "order_query_history": [{
            "turn_id": state.get("turn_id"), "query": query, "results": results,
            "summary": None, "clarification": None, "error": None,
        }],
    }


async def summarize_orders_node(state: AgentState) -> dict:
    query = state.get("order_query") or {}
    summary = order_query_service.summarize_orders(
        state.get("order_results") or [], query.get("group_by")
    )
    return {
        "order_summary": summary,
        "order_query_history": [{
            "turn_id": state.get("turn_id"), "query": query,
            "results": state.get("order_results") or [], "summary": summary,
            "clarification": None, "error": None,
        }],
    }

"""图组装：clarify → route → retrieve → recommend
       → confirm(interrupt) → place_order。

单 Agent StateGraph 让共享状态、澄清分支和 HITL 确认都能被 checkpointer
持久化；值得买真实商品事实直接进入推荐决策，确认后只做一次模拟订单幂等落库。
"""
from langgraph.graph import END, START, StateGraph
from app.agents.nodes import (
    clarify,
    confirm,
    memory,
    place_order,
    recommend,
    retrieve,
    route,
)
from app.agents.state import AgentState


def _after_capture(state: AgentState) -> str:
    return "retrieve" if state.get("needs_action") == "shopping" else "end"


def _after_route(state: AgentState) -> str:
    intent = state.get("intent")
    if intent == "shopping":
        return "shopping"
    if intent == "memory":
        return "memory"
    if intent == "cancel":
        return "cancel"
    return "end"


def _after_recommend(state: AgentState) -> str:
    if not (state.get("recommendation") or {}).get("items"):
        return "end"
    return "confirm"


def _after_confirm(state: AgentState) -> str:
    return "place_order" if state.get("draft_order") else "end"


def build_graph(checkpointer=None):
    g = StateGraph[AgentState, None, AgentState, AgentState](AgentState)
    g.add_node("clarify", clarify.clarify_node)
    g.add_node("load_memory", memory.load_memory_node)
    g.add_node("capture_memory", memory.capture_memory_node)
    g.add_node("route", route.route_node)
    g.add_node("retrieve", retrieve.retrieve_node)
    g.add_node("recommend", recommend.recommend_node)
    g.add_node("confirm", confirm.confirm_node)
    g.add_node("place_order", place_order.place_order_node)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "route")
    g.add_conditional_edges(
        "route", _after_route,
        {"shopping": "clarify", "memory": "capture_memory", "cancel": END, "end": END},
    )
    g.add_edge("clarify", "capture_memory")
    g.add_conditional_edges("capture_memory", _after_capture, {"retrieve": "retrieve", "end": END})
    g.add_edge("retrieve", "recommend")
    g.add_conditional_edges(
        "recommend", _after_recommend,
        {"confirm": "confirm", "end": END},
    )
    g.add_conditional_edges(
        "confirm", _after_confirm,
        {"place_order": "place_order", "end": END},
    )
    g.add_edge("place_order", END)
    return g.compile(checkpointer=checkpointer)

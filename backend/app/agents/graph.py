"""图组装：clarify → route → retrieve → compare → normalize → recommend
       → confirm(interrupt) → lock_price → place_order。

设计决策：
- 单 Agent StateGraph（非多 Agent），interrupt/checkpointer 是选 LangGraph 的理由；
- confirm 取消 → END；确认后首轮去 lock_price 二次询价，涨价则回 confirm 重确认，
  重确认后（lock_attempts>0）直接 place_order，避免无限循环；
- compare/lock_price 按 configurable.mcp_tools 直调 MCP 工具（编排式）。
"""
from langgraph.graph import END, START, StateGraph

from app.agents.nodes import (
    clarify,
    compare,
    confirm,
    lock_price,
    normalize,
    place_order,
    recommend,
    retrieve,
    route,
)
from app.agents.state import AgentState


def _after_clarify(state: AgentState) -> str:
    return "route" if state.get("needs_action") == "shopping" else "end"


def _after_route(state: AgentState) -> str:
    return "retrieve" if state.get("intent") == "shopping" else "end"


def _after_retrieve(state: AgentState) -> str:
    # 值海纳真实源：检索结果已是可推荐结构，跳过比价/归一，直连推荐
    return "recommend" if state.get("zdm_mode") else "compare"


def _after_recommend(state: AgentState) -> str:
    # 真实源无下单闭环（不可对真实商品走仿真二次询价），推荐后即结束
    return "end" if state.get("zdm_mode") else "confirm"


def _after_confirm(state: AgentState) -> str:
    if not state.get("draft_order"):
        return "end"                          # 取消下单
    if state.get("lock_attempts", 0) > 0:
        return "place_order"                  # 已二次询价过，直接下单
    return "lock_price"


def _after_lock_price(state: AgentState) -> str:
    return "confirm" if state.get("price_raised") else "place_order"


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    g.add_node("clarify", clarify.clarify_node)
    g.add_node("route", route.route_node)
    g.add_node("retrieve", retrieve.retrieve_node)
    g.add_node("compare", compare.compare_node)
    g.add_node("normalize", normalize.normalize_node)
    g.add_node("recommend", recommend.recommend_node)
    g.add_node("confirm", confirm.confirm_node)
    g.add_node("lock_price", lock_price.lock_price_node)
    g.add_node("place_order", place_order.place_order_node)

    g.add_edge(START, "clarify")
    g.add_conditional_edges("clarify", _after_clarify, {"route": "route", "end": END})
    g.add_conditional_edges("route", _after_route, {"retrieve": "retrieve", "end": END})
    g.add_conditional_edges(
        "retrieve", _after_retrieve,
        {"compare": "compare", "recommend": "recommend"},
    )
    g.add_edge("compare", "normalize")
    g.add_edge("normalize", "recommend")
    g.add_conditional_edges(
        "recommend", _after_recommend,
        {"confirm": "confirm", "end": END},
    )
    g.add_conditional_edges(
        "confirm", _after_confirm,
        {"lock_price": "lock_price", "place_order": "place_order", "end": END},
    )
    g.add_conditional_edges(
        "lock_price", _after_lock_price,
        {"confirm": "confirm", "place_order": "place_order"},
    )
    g.add_edge("place_order", END)
    return g.compile(checkpointer=checkpointer)